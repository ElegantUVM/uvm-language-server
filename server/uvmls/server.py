"""The language server: LSP plumbing around Workspace, Navigator and the views."""

from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import unquote, urlparse

from lsprotocol import types as T
from pygls.lsp.server import LanguageServer
from pygls.uris import from_fs_path

from . import __version__
from .lints import lint
from .model import Range
from .navigation import Navigator
from .project import ProjectConfig, is_sv_file, norm
from .views import class_graph, config_db_report, topology
from .workspace import Workspace

log = logging.getLogger("uvmls")

DEBOUNCE_SECONDS = 0.25

SYMBOL_KINDS = {
    "class": T.SymbolKind.Class, "interface": T.SymbolKind.Interface,
    "module": T.SymbolKind.Module, "package": T.SymbolKind.Package,
    "program": T.SymbolKind.Module, "field": T.SymbolKind.Field,
    "method": T.SymbolKind.Method, "constructor": T.SymbolKind.Constructor,
    "constraint": T.SymbolKind.Property, "covergroup": T.SymbolKind.Event,
    "typedef": T.SymbolKind.TypeParameter, "object": T.SymbolKind.Object,
}
COMPLETION_KINDS = {
    "variable": T.CompletionItemKind.Variable, "field": T.CompletionItemKind.Field,
    "method": T.CompletionItemKind.Method, "class": T.CompletionItemKind.Class,
    "typedef": T.CompletionItemKind.TypeParameter, "module": T.CompletionItemKind.Module,
    "interface": T.CompletionItemKind.Interface, "package": T.CompletionItemKind.Module,
    "program": T.CompletionItemKind.Module, "enum": T.CompletionItemKind.EnumMember,
    "macro": T.CompletionItemKind.Snippet,
}
SEVERITY = {"error": T.DiagnosticSeverity.Error, "warning": T.DiagnosticSeverity.Warning,
            "info": T.DiagnosticSeverity.Information, "hint": T.DiagnosticSeverity.Hint}


def uri_to_path(uri: str) -> str:
    p = urlparse(uri)
    return norm(unquote(p.path)) if p.scheme == "file" else uri


def path_to_uri(path: str) -> str:
    return from_fs_path(path) or f"file://{path}"


def lsp_range(r: Range) -> T.Range:
    return T.Range(T.Position(r.line, r.col), T.Position(r.end_line, r.end_col))


def location(r: Range) -> T.Location:
    return T.Location(path_to_uri(r.path), lsp_range(r))


class UvmLanguageServer(LanguageServer):
    def __init__(self):
        super().__init__("uvm-language-server", __version__)
        self.ws = Workspace()
        self.nav = Navigator(self.ws)
        self.settings: dict = {}
        self.workspace_roots: list[str] = []
        self.published: set[str] = set()
        self.pending: set[str] = set()
        self._debounce: asyncio.TimerHandle | None = None

    # -- indexing ------------------------------------------------------------

    def config(self) -> ProjectConfig:
        return ProjectConfig.from_settings(self.workspace_roots, self.settings)

    async def full_index(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.ws.configure, self.config())
        for w in self.ws.sources.warnings if self.ws.sources else []:
            self.window_log_message(T.LogMessageParams(T.MessageType.Warning, w))
        self.after_index()

    def schedule(self, path: str):
        self.pending.add(path)
        loop = asyncio.get_running_loop()
        if self._debounce is not None:
            self._debounce.cancel()
        self._debounce = loop.call_later(DEBOUNCE_SECONDS, lambda: asyncio.ensure_future(self.flush()))

    async def flush(self):
        paths, self.pending = self.pending, set()
        loop = asyncio.get_running_loop()

        def work():
            for p in paths:
                doc = self.documents.get(p)
                self.ws.update_document(p, doc)
        await loop.run_in_executor(None, work)
        self.after_index()

    @property
    def documents(self) -> dict[str, str]:
        out = {}
        for uri, doc in self.workspace.text_documents.items():
            out[uri_to_path(uri)] = doc.source
        return out

    def after_index(self):
        self.publish_diagnostics()
        self.protocol.notify("uvm/indexChanged", {"stats": self.ws.stats(), "version": self.ws.version})

    def publish_diagnostics(self):
        with self.ws.lock:
            per_file: dict[str, list] = {}
            seen = set()
            for idx in self.ws.indices.values():
                if idx.library:
                    continue
                for d in idx.diagnostics:
                    key = (d.range.path, d.range.line, d.range.col, d.message)
                    if key in seen:
                        continue
                    seen.add(key)
                    per_file.setdefault(d.range.path, []).append(d)
            lint_cfg = self.settings.get("lint")
            if not isinstance(lint_cfg, dict) or lint_cfg.get("enabled", True):
                for path, diags in lint(self.ws).items():
                    per_file.setdefault(path, []).extend(diags)
        for path in self.published - set(per_file):
            self.text_document_publish_diagnostics(T.PublishDiagnosticsParams(path_to_uri(path), []))
        for path, diags in per_file.items():
            items = [T.Diagnostic(range=lsp_range(d.range), message=d.message,
                                  severity=SEVERITY.get(d.severity, T.DiagnosticSeverity.Warning),
                                  code=d.code, source=d.source) for d in diags]
            self.text_document_publish_diagnostics(T.PublishDiagnosticsParams(path_to_uri(path), items))
        self.published = set(per_file)


server = UvmLanguageServer()


def _text(ls: UvmLanguageServer, uri: str) -> str:
    doc = ls.workspace.get_text_document(uri)
    try:
        return doc.source
    except Exception:
        return ""


# -- lifecycle ---------------------------------------------------------------


@server.feature(T.INITIALIZE)
def initialize(ls: UvmLanguageServer, params: T.InitializeParams):
    roots = []
    for f in params.workspace_folders or []:
        roots.append(uri_to_path(f.uri))
    if not roots and params.root_uri:
        roots.append(uri_to_path(params.root_uri))
    if not roots and params.root_path:
        roots.append(norm(params.root_path))
    ls.workspace_roots = roots
    opts = params.initialization_options or {}
    ls.settings = opts.get("settings", opts) if isinstance(opts, dict) else {}


@server.feature(T.INITIALIZED)
async def initialized(ls: UvmLanguageServer, params: T.InitializedParams):
    await ls.full_index()


@server.feature(T.WORKSPACE_DID_CHANGE_CONFIGURATION)
async def did_change_configuration(ls: UvmLanguageServer, params: T.DidChangeConfigurationParams):
    settings = params.settings or {}
    if isinstance(settings, dict) and "uvm" in settings:
        settings = settings["uvm"]
    if isinstance(settings, dict) and settings != ls.settings:
        ls.settings = settings
        await ls.full_index()


@server.feature(T.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: UvmLanguageServer, params: T.DidOpenTextDocumentParams):
    path = uri_to_path(params.text_document.uri)
    if is_sv_file(path):
        ls.schedule(path)


@server.feature(T.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: UvmLanguageServer, params: T.DidChangeTextDocumentParams):
    ls.schedule(uri_to_path(params.text_document.uri))


@server.feature(T.TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: UvmLanguageServer, params: T.DidSaveTextDocumentParams):
    ls.schedule(uri_to_path(params.text_document.uri))


@server.feature(T.TEXT_DOCUMENT_DID_CLOSE)
def did_close(ls: UvmLanguageServer, params: T.DidCloseTextDocumentParams):
    path = uri_to_path(params.text_document.uri)

    async def later():
        await asyncio.sleep(0)
        ls.ws.update_document(path, None)
        ls.after_index()
    asyncio.ensure_future(later())


@server.feature(T.WORKSPACE_DID_CHANGE_WATCHED_FILES)
async def did_change_watched(ls: UvmLanguageServer, params: T.DidChangeWatchedFilesParams):
    structural = any(c.type in (T.FileChangeType.Created, T.FileChangeType.Deleted) for c in params.changes)
    filelist_changed = any(uri_to_path(c.uri).endswith((".f", ".F")) for c in params.changes)
    if structural or filelist_changed:
        await ls.full_index()
        return
    for c in params.changes:
        path = uri_to_path(c.uri)
        if is_sv_file(path) and path not in ls.documents:
            ls.schedule(path)


# -- navigation ----------------------------------------------------------------


@server.feature(T.TEXT_DOCUMENT_DEFINITION)
def definition(ls: UvmLanguageServer, params: T.DefinitionParams):
    path = uri_to_path(params.text_document.uri)
    with ls.ws.lock:
        ranges = ls.nav.definition(path, params.position.line, params.position.character,
                                   _text(ls, params.text_document.uri))
    return [location(r) for r in ranges] or None


@server.feature(T.TEXT_DOCUMENT_TYPE_DEFINITION)
def type_definition(ls: UvmLanguageServer, params: T.TypeDefinitionParams):
    path = uri_to_path(params.text_document.uri)
    with ls.ws.lock:
        ranges = ls.nav.type_definition(path, params.position.line, params.position.character,
                                        _text(ls, params.text_document.uri))
    return [location(r) for r in ranges] or None


@server.feature(T.TEXT_DOCUMENT_HOVER)
def hover(ls: UvmLanguageServer, params: T.HoverParams):
    path = uri_to_path(params.text_document.uri)
    with ls.ws.lock:
        md = ls.nav.hover(path, params.position.line, params.position.character,
                          _text(ls, params.text_document.uri))
    if not md:
        return None
    return T.Hover(contents=T.MarkupContent(kind=T.MarkupKind.Markdown, value=md))


@server.feature(T.TEXT_DOCUMENT_REFERENCES)
def references(ls: UvmLanguageServer, params: T.ReferenceParams):
    path = uri_to_path(params.text_document.uri)
    with ls.ws.lock:
        ranges = ls.nav.references(path, params.position.line, params.position.character,
                                   _text(ls, params.text_document.uri),
                                   params.context.include_declaration)
    return [location(r) for r in ranges]


@server.feature(T.TEXT_DOCUMENT_COMPLETION,
                T.CompletionOptions(trigger_characters=[".", ":", "`"]))
def completion(ls: UvmLanguageServer, params: T.CompletionParams):
    path = uri_to_path(params.text_document.uri)
    with ls.ws.lock:
        items = ls.nav.completions(path, params.position.line, params.position.character,
                                   _text(ls, params.text_document.uri))
    out = []
    for it in items:
        ci = T.CompletionItem(label=it["label"], kind=COMPLETION_KINDS.get(it["kind"]),
                              detail=it.get("detail"))
        if it.get("snippet"):
            ci.insert_text = it["snippet"]
            ci.insert_text_format = T.InsertTextFormat.Snippet
        out.append(ci)
    return T.CompletionList(is_incomplete=False, items=out)


def _doc_symbol(s: dict) -> T.DocumentSymbol:
    return T.DocumentSymbol(
        name=s["name"], kind=SYMBOL_KINDS.get(s["kind"], T.SymbolKind.Variable),
        detail=s.get("detail") or None, range=lsp_range(s["range"]),
        selection_range=lsp_range(s["selection"]),
        children=[_doc_symbol(c) for c in s.get("children", [])])


@server.feature(T.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def document_symbol(ls: UvmLanguageServer, params: T.DocumentSymbolParams):
    path = uri_to_path(params.text_document.uri)
    with ls.ws.lock:
        syms = ls.nav.document_symbols(path)
    return [_doc_symbol(s) for s in syms]


@server.feature(T.WORKSPACE_SYMBOL)
def workspace_symbol(ls: UvmLanguageServer, params: T.WorkspaceSymbolParams):
    with ls.ws.lock:
        syms = ls.nav.workspace_symbols(params.query)
    return [T.SymbolInformation(name=s["name"], kind=SYMBOL_KINDS.get(s["kind"], T.SymbolKind.Variable),
                                location=location(s["range"]), container_name=s["container"] or None)
            for s in syms]


@server.feature(T.TEXT_DOCUMENT_DOCUMENT_LINK)
def document_link(ls: UvmLanguageServer, params: T.DocumentLinkParams):
    path = uri_to_path(params.text_document.uri)
    links = []
    with ls.ws.lock:
        seen = set()
        for idx in ls.ws.indices.values():
            for target, r in idx.includes.get(path, []):
                if (r.line, target) in seen:
                    continue
                seen.add((r.line, target))
                links.append(T.DocumentLink(range=lsp_range(r), target=path_to_uri(target)))
    return links


def _type_item(cls) -> T.TypeHierarchyItem:
    return T.TypeHierarchyItem(name=cls.name, kind=T.SymbolKind.Class, uri=path_to_uri(cls.range.path),
                               range=lsp_range(cls.decl_range), selection_range=lsp_range(cls.range),
                               detail=cls.package or None, data={"qualname": cls.qualname})


@server.feature(T.TEXT_DOCUMENT_PREPARE_TYPE_HIERARCHY)
def prepare_type_hierarchy(ls: UvmLanguageServer, params: T.TypeHierarchyPrepareParams):
    path = uri_to_path(params.text_document.uri)
    with ls.ws.lock:
        cls = ls.nav.class_at(path, params.position.line, params.position.character,
                              _text(ls, params.text_document.uri))
        return [_type_item(cls)] if cls else None


def _item_class(ls, item: T.TypeHierarchyItem):
    data = item.data or {}
    qual = data.get("qualname") if isinstance(data, dict) else None
    return ls.ws.class_by_qual.get(qual) if qual else ls.ws.find_class(item.name)


@server.feature(T.TYPE_HIERARCHY_SUPERTYPES)
def supertypes(ls: UvmLanguageServer, params: T.TypeHierarchySupertypesParams):
    with ls.ws.lock:
        cls = _item_class(ls, params.item)
        if cls is None:
            return None
        base = ls.ws.base_spec(cls)
        return [_type_item(base.cls)] if base and base.cls else []


@server.feature(T.TYPE_HIERARCHY_SUBTYPES)
def subtypes(ls: UvmLanguageServer, params: T.TypeHierarchySubtypesParams):
    with ls.ws.lock:
        cls = _item_class(ls, params.item)
        if cls is None:
            return None
        return [_type_item(c) for c in ls.ws.subclasses.get(cls.qualname, [])]


@server.feature(T.TEXT_DOCUMENT_CODE_LENS)
def code_lens(ls: UvmLanguageServer, params: T.CodeLensParams):
    path = uri_to_path(params.text_document.uri)
    lenses = []
    with ls.ws.lock:
        for sym in ls.ws.symbols_in(path):
            if not hasattr(sym, "qualname") or getattr(sym, "library", False):
                continue
            r = lsp_range(sym.range)
            subs = ls.ws.subclasses.get(sym.qualname, [])
            role = ls.ws.role(sym)
            title = f"{role}"
            if subs:
                title += f" · {len(subs)} subclass{'es' if len(subs) != 1 else ''}"
            lenses.append(T.CodeLens(range=r, command=T.Command(
                title=f"$(type-hierarchy) {title} · show in graph", command="uvm.showClassGraph",
                arguments=[{"focus": sym.qualname}])))
            if role == "test":
                lenses.append(T.CodeLens(range=r, command=T.Command(
                    title="$(list-tree) show topology", command="uvm.showTopology",
                    arguments=[{"test": sym.name}])))
    return lenses


# -- custom requests ---------------------------------------------------------------


def _plain(value):
    """pygls hands custom-request params over as namedtuple-like Objects."""
    if hasattr(value, "_asdict"):
        return {k: _plain(v) for k, v in value._asdict().items()}
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _params(params) -> dict:
    p = _plain(params)
    return p if isinstance(p, dict) else {}


@server.feature("uvm/classGraph")
def uvm_class_graph(ls: UvmLanguageServer, params):
    with ls.ws.lock:
        return class_graph(ls.ws)


@server.feature("uvm/topology")
def uvm_topology(ls: UvmLanguageServer, params):
    p = _params(params)
    with ls.ws.lock:
        return topology(ls.ws, p.get("test"))


@server.feature("uvm/configDb")
def uvm_config_db(ls: UvmLanguageServer, params):
    with ls.ws.lock:
        return config_db_report(ls.ws)


@server.feature("uvm/stats")
def uvm_stats(ls: UvmLanguageServer, params):
    return ls.ws.stats()


@server.feature("uvm/classAt")
def uvm_class_at(ls: UvmLanguageServer, params):
    p = _params(params)
    uri = p.get("uri")
    pos = p.get("position") or {}
    if not uri:
        return None
    with ls.ws.lock:
        cls = ls.nav.class_at(uri_to_path(uri), pos.get("line", 0), pos.get("character", 0), _text(ls, uri))
        return cls.qualname if cls else None


@server.feature("uvm/reindex")
async def uvm_reindex(ls: UvmLanguageServer, params):
    await ls.full_index()
    return ls.ws.stats()


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="uvmls", description="UVM / SystemVerilog language server")
    ap.add_argument("--stdio", action="store_true", help="talk LSP over stdin/stdout (default)")
    ap.add_argument("--tcp", type=int, help="listen on this TCP port instead")
    ap.add_argument("--log", help="write a debug log to this file")
    args = ap.parse_args(argv)
    if args.log:
        logging.basicConfig(filename=args.log, level=logging.DEBUG)
    if args.tcp:
        server.start_tcp("127.0.0.1", args.tcp)
    else:
        server.start_io()
