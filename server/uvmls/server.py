"""The language server: LSP plumbing around Workspace, Navigator and the views."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from urllib.parse import unquote, urlparse

from lsprotocol import types as T
from pygls.lsp.server import LanguageServer
from pygls.uris import from_fs_path

from . import __version__, elab
from .lints import lint
from .model import Diag, Range
from .navigation import Navigator
from .project import ProjectConfig, is_sv_file, norm
from .views import class_graph, config_db_report, topology
from .workspace import Workspace, _same_text

log = logging.getLogger("uvmls")

DEBOUNCE_SECONDS = 0.25
ELAB_MODES = ("off", "onSave", "onChange")
ELAB_SAVE_DELAY = 0.3  # coalesces a "Save All"
ELAB_CHANGE_DELAY = 1.0  # after the last edit, in onChange mode
ELAB_TIMEOUT = 120.0
PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


def lsp_diagnostic(d: Diag) -> T.Diagnostic:
    return T.Diagnostic(range=lsp_range(d.range), message=d.message,
                        severity=SEVERITY.get(d.severity, T.DiagnosticSeverity.Warning),
                        code=d.code, source=d.source,
                        related_information=[T.DiagnosticRelatedInformation(location(r), msg)
                                             for r, msg in d.related] or None)


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
        self.elab_diags: dict[str, list[Diag]] = {}
        self.elab_status: dict = {"state": "off"}
        self._elab_run = 0  # bumped by every start or stop; older runs discard their results
        self._elab_task: asyncio.Future | None = None
        self._elab_proc: asyncio.subprocess.Process | None = None
        self._elab_timer: asyncio.TimerHandle | None = None

    # -- indexing ------------------------------------------------------------

    def config(self) -> ProjectConfig:
        return ProjectConfig.from_settings(self.workspace_roots, self.settings)

    async def full_index(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.ws.configure, self.config())
        for w in self.ws.sources.warnings if self.ws.sources else []:
            self.window_log_message(T.LogMessageParams(T.MessageType.Warning, w))
        self.after_index()
        if self.elab_mode != "off":
            self.start_elaboration()

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
            for d in self.ws.user_diagnostics():
                per_file.setdefault(d.range.path, []).append(d)
            lint_cfg = self.settings.get("lint")
            if not isinstance(lint_cfg, dict) or lint_cfg.get("enabled", True):
                for path, diags in lint(self.ws).items():
                    per_file.setdefault(path, []).extend(diags)
        for path, diags in self.elab_diags.items():
            # Parse errors come from both; keep the index's (it adds hints, e.g. for includes).
            have = per_file.get(path, [])
            known = {(d.range.line, d.message) for d in have} | {(d.range.line, d.code) for d in have}
            extra = [d for d in diags if (d.range.line, d.message) not in known
                     and (d.range.line, d.code) not in known]
            if extra:
                per_file.setdefault(path, []).extend(extra)
        for path in self.published - set(per_file):
            self.text_document_publish_diagnostics(T.PublishDiagnosticsParams(path_to_uri(path), []))
        for path, diags in per_file.items():
            items = [lsp_diagnostic(d) for d in diags]
            self.text_document_publish_diagnostics(T.PublishDiagnosticsParams(path_to_uri(path), items))
        self.published = set(per_file)

    # -- elaboration -----------------------------------------------------------

    @property
    def elab_mode(self) -> str:
        mode = self.settings.get("elaboration")
        return mode if mode in ELAB_MODES else "onSave"

    def schedule_elaboration(self, delay: float):
        """Elaborate `delay` seconds from now unless the setting is off; a later call resets it."""
        if self.elab_mode == "off":
            return
        if self._elab_timer is not None:
            self._elab_timer.cancel()
        self._elab_timer = asyncio.get_running_loop().call_later(delay, self.start_elaboration)

    def start_elaboration(self) -> asyncio.Future:
        """Elaborate now, superseding any run in progress. The task's result is the status."""
        self.stop_elaboration()
        self._elab_task = asyncio.ensure_future(self.elaborate(self._elab_run))
        return self._elab_task

    def stop_elaboration(self):
        """Cancel a scheduled run and kill a running worker; their results are discarded."""
        if self._elab_timer is not None:
            self._elab_timer.cancel()
            self._elab_timer = None
        self._elab_run += 1
        if self._elab_proc is not None:
            _kill(self._elab_proc)
        self._elab_proc = None

    def clear_elaboration(self):
        self.stop_elaboration()
        self.elab_diags = {}
        self.publish_diagnostics()
        self.notify_elaboration({"state": "off"})

    def notify_elaboration(self, status: dict):
        self.elab_status = {"state": status["state"], "errors": status.get("errors", 0),
                            "warnings": status.get("warnings", 0), "seconds": status.get("seconds", 0),
                            "message": status.get("message", ""), "uvm": status.get("uvm", "")}
        self.protocol.notify("uvm/elaborationStatus", self.elab_status)

    def elab_overrides(self) -> dict[str, str]:
        """Unsaved editor text the compile uses instead of the files on disk."""
        files = self.ws.files()
        return {p: t for p, t in self.documents.items() if is_sv_file(p) or p in files}

    async def elaborate(self, run: int) -> dict | None:
        """One run in a worker process; None if a newer run or a stop superseded it."""
        try:
            return await self._elaborate(run)
        except Exception as e:  # never leave the client showing "running"
            log.exception("elaboration")
            if run != self._elab_run:
                return None
            self.notify_elaboration({"state": "failed", "message": f"{type(e).__name__}: {e}"})
            return self.elab_status

    async def _elaborate(self, run: int) -> dict | None:
        with self.ws.lock:
            reason = elab.skip_reason(self.ws)
            request = None if reason else elab.request_for(self.ws, self.elab_overrides())
            uvm = elab.uvm_label(self.ws.sources)
        if reason:
            self.elab_diags = {}
            self.publish_diagnostics()
            self.notify_elaboration({"state": "skipped", "message": reason, "uvm": uvm})
            return self.elab_status
        self.notify_elaboration({"state": "running", "uvm": uvm})
        t0 = time.monotonic()
        result = await self.run_worker(run, request)
        if result is None or run != self._elab_run:
            return None
        per_file: dict[str, list[Diag]] = {}
        notes = []
        for d in result.get("diagnostics", []):
            if not d["file"]:
                notes.append(f"{d['severity']}: {d['message']}")
                continue
            related = [(Range(n["file"], n["line"], n["col"], n["line"], n["col"]), n["message"])
                       for n in d["related"]]
            per_file.setdefault(d["file"], []).append(
                Diag(Range(d["file"], d["line"], d["col"], d["endLine"], d["endCol"]), d["message"],
                     d["severity"], d["code"], elab.SOURCE, related))
        counts = result.get("counts") or {}
        if result.get("error"):
            notes = [result["error"]]
        else:
            tops = result.get("tops") or []
            notes.insert(0, f"top: {', '.join(tops)}" if tops else
                         "no top module; packages and classes were checked")
            if counts.get("omitted"):
                listed = len(result.get("diagnostics", []))
                notes.append(f"Problems lists the first {listed} of {listed + counts['omitted']}")
            if counts.get("libraryErrors"):
                notes.append(f"{counts['libraryErrors']} error(s) the UVM library reports on its own are not shown")
        self.elab_diags = per_file
        self.publish_diagnostics()
        self.notify_elaboration({"state": "failed" if result.get("error") else "done",
                                 "errors": counts.get("errors", 0), "warnings": counts.get("warnings", 0),
                                 "seconds": round(time.monotonic() - t0, 2), "message": "; ".join(notes),
                                 "uvm": uvm})
        return self.elab_status

    async def run_worker(self, run: int, request: dict) -> dict | None:
        """`python -m uvmls elab-worker` on the request: pyslang holds the GIL while it compiles."""
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [PACKAGE_PARENT] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "uvmls", "elab-worker", stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
        except OSError as e:
            return {"error": f"cannot start {sys.executable}: {e}"}
        if run != self._elab_run:  # superseded while starting
            _kill(proc)
            return None
        self._elab_proc = proc
        try:
            out, err = await asyncio.wait_for(proc.communicate(json.dumps(request).encode()), ELAB_TIMEOUT)
        except asyncio.TimeoutError:
            return {"error": f"it took longer than {ELAB_TIMEOUT:.0f} s and was stopped"}
        finally:  # also when the server shuts down and cancels this task
            _kill(proc)
            if self._elab_proc is proc:
                self._elab_proc = None
        if run != self._elab_run:
            return None
        if err.strip():
            self.window_log_message(T.LogMessageParams(
                T.MessageType.Log, f"elaboration worker: {err.decode(errors='replace')[-4000:]}"))
        try:
            return json.loads(out)
        except ValueError:
            return {"error": f"the worker exited with status {proc.returncode} (see the log)"}


def _kill(proc: asyncio.subprocess.Process):
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


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
    if ls.elab_mode == "off":
        ls.notify_elaboration({"state": "off"})


@server.feature(T.SHUTDOWN)
def on_shutdown(ls: UvmLanguageServer, params):
    ls.stop_elaboration()


@server.feature(T.EXIT)
def on_exit(ls: UvmLanguageServer, params):
    ls.stop_elaboration()


def _without(settings: dict, key: str) -> dict:
    return {k: v for k, v in settings.items() if k != key}


@server.feature(T.WORKSPACE_DID_CHANGE_CONFIGURATION)
async def did_change_configuration(ls: UvmLanguageServer, params: T.DidChangeConfigurationParams):
    settings = params.settings or {}
    if isinstance(settings, dict) and "uvm" in settings:
        settings = settings["uvm"]
    if isinstance(settings, dict) and settings != ls.settings:
        was_off = ls.elab_mode == "off"
        old, ls.settings = ls.settings, settings
        if ls.elab_mode == "off":
            ls.clear_elaboration()
        if _without(old, "elaboration") != _without(settings, "elaboration"):
            await ls.full_index()  # elaborates afterwards unless it is off
        elif was_off and ls.elab_mode != "off":
            ls.start_elaboration()


@server.feature(T.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: UvmLanguageServer, params: T.DidOpenTextDocumentParams):
    path = uri_to_path(params.text_document.uri)
    if is_sv_file(path):
        ls.schedule(path)


@server.feature(T.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: UvmLanguageServer, params: T.DidChangeTextDocumentParams):
    ls.schedule(uri_to_path(params.text_document.uri))
    if ls.elab_mode == "onChange":
        ls.schedule_elaboration(ELAB_CHANGE_DELAY)


@server.feature(T.TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: UvmLanguageServer, params: T.DidSaveTextDocumentParams):
    ls.schedule(uri_to_path(params.text_document.uri))
    ls.schedule_elaboration(ELAB_SAVE_DELAY)


@server.feature(T.TEXT_DOCUMENT_DID_CLOSE)
def did_close(ls: UvmLanguageServer, params: T.DidCloseTextDocumentParams):
    path = uri_to_path(params.text_document.uri)

    async def later():
        await asyncio.sleep(0)
        unsaved = ls.ws.overrides.get(path)
        ls.ws.update_document(path, None)
        ls.after_index()
        if unsaved is not None and not _same_text(path, unsaved, None):
            ls.schedule_elaboration(ELAB_SAVE_DELAY)  # the last run compiled the discarded text
    asyncio.ensure_future(later())


@server.feature(T.WORKSPACE_DID_CHANGE_WATCHED_FILES)
async def did_change_watched(ls: UvmLanguageServer, params: T.DidChangeWatchedFilesParams):
    structural = any(c.type in (T.FileChangeType.Created, T.FileChangeType.Deleted) for c in params.changes)
    filelist_changed = any(uri_to_path(c.uri).endswith((".f", ".F")) for c in params.changes)
    if structural or filelist_changed:
        await ls.full_index()
        return
    open_docs = ls.documents
    changed = [p for p in (uri_to_path(c.uri) for c in params.changes)
               if is_sv_file(p) and p not in open_docs]
    for path in changed:
        ls.schedule(path)
    if changed:  # an open file's save already triggered it
        ls.schedule_elaboration(ELAB_SAVE_DELAY)


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


@server.feature("uvm/elaborate")
async def uvm_elaborate(ls: UvmLanguageServer, params):
    """Elaborate now, whatever the setting; returns the status once the latest run is done."""
    task = ls.start_elaboration()
    status = await task
    while status is None and ls._elab_task is not task:  # superseded: wait for the newer run
        task = ls._elab_task
        status = await task
    return status or ls.elab_status


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
