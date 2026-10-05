"""The language server: LSP plumbing around Workspace, Navigator and the views."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections import Counter
from dataclasses import replace
from difflib import SequenceMatcher
from urllib.parse import quote, unquote, urlparse

from lsprotocol import types as T
from pygls.capabilities import get_capability
from pygls.lsp.server import LanguageServer
from pygls.uris import from_fs_path

from . import __version__, elab, fixes, indexer
from .documents import Workspace as LspWorkspace
from .lints import lint
from .model import Diag, Range
from .navigation import Navigator
from .project import ProjectConfig, is_sv_file, norm
from .views import class_graph, config_db_report, effective_type, topology
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
RANK = {"hint": 0, "info": 1, "warning": 2, "error": 3}


def uri_to_path(uri: str) -> str:
    p = urlparse(uri)
    return norm(unquote(p.path)) if p.scheme == "file" else uri


def path_to_uri(path: str) -> str:
    return from_fs_path(path) or f"file://{path}"


def lsp_range(r: Range) -> T.Range:
    return T.Range(T.Position(r.line, r.col), T.Position(r.end_line, r.end_col))


def location(r: Range) -> T.Location:
    return T.Location(path_to_uri(r.path), lsp_range(r))


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            return f.read()
    except OSError:
        return None


def changed_lines(old: str, new: str) -> tuple[int, int, int]:
    """The common prefix length and the exclusive ends of the old and new changed blocks."""
    a, b = fixes.EOL_RE.split(old), fixes.EOL_RE.split(new)
    n = min(len(a), len(b))
    head = 0
    while head < n and a[head] == b[head]:
        head += 1
    tail = 0
    while tail < n - head and a[-1 - tail] == b[-1 - tail]:
        tail += 1
    return head, len(a) - tail, len(b) - tail


def shift_lines(old: str, new: str):
    """Where each line of `old` is in `new`, taking the edit as one changed block of lines: lines
    before it keep their number, lines after it move with it, lines inside it are gone (None)."""
    head, end_old, end_new = changed_lines(old, new)
    return lambda line: line if line < head else line + end_new - end_old if line >= end_old else None


def lsp_diagnostic(d: Diag) -> T.Diagnostic:
    return T.Diagnostic(range=lsp_range(d.range), message=d.message,
                        severity=SEVERITY.get(d.severity, T.DiagnosticSeverity.Warning),
                        code=d.code, source=d.source,
                        related_information=[T.DiagnosticRelatedInformation(location(r), msg)
                                             for r, msg in d.related] or None,
                        data=d.data or None)


class UvmLanguageServer(LanguageServer):
    def __init__(self):
        super().__init__("uvm-language-server", __version__)
        self.ws = Workspace()
        self.nav = Navigator(self.ws)
        self.settings: dict = {}
        self.workspace_roots: list[str] = []
        self.published: dict[str, list[Diag]] = {}  # what the client was last sent, per file
        self.diag_texts: dict[str, str | None] = {}  # indexed text those describe, including unopened files
        self.diag_origins: dict[str, dict[tuple, int | None]] = {}
        self._diagnostic_id = 0
        self.client_commands: frozenset[str] = frozenset()  # client-side commands it can run
        self.pending: set[str] = set()
        self._debounce: asyncio.TimerHandle | None = None
        self._flush_task: asyncio.Future | None = None
        self._index_task: asyncio.Future | None = None
        self._index_lock = asyncio.Lock()  # indexing through publication is one operation
        self.elab_diags: dict[str, list[Diag]] = {}
        self.elab_texts: dict[str, str] = {}  # the unsaved editor text those diagnostics come from
        self.elab_shown: list[str] = []  # the severity Problems shows for each of them
        self.elab_basis: dict[str, str] = {}  # the text of open files when the latest run started
        self.elab_compiled: dict[str, str] = {}  # that, without the files opened since
        self.elab_status: dict = {"state": "off"}
        self._elab_run = 0  # bumped by every start or stop; older runs discard their results
        self._elab_task: asyncio.Future | None = None
        self._elab_waiters: list[asyncio.Future] = []  # Elaborate Now requests waiting for a result
        self._elab_proc: asyncio.subprocess.Process | None = None
        self._elab_timer: asyncio.TimerHandle | None = None

    # -- indexing ------------------------------------------------------------

    def config(self) -> ProjectConfig:
        return ProjectConfig.from_settings(self.workspace_roots, self.settings)

    async def full_index(self):
        previous = self._index_task

        async def work():
            if previous is not None and not previous.done():
                await asyncio.shield(previous)
            await self._full_index()
        self._index_task = asyncio.ensure_future(work())
        await asyncio.shield(self._index_task)

    async def _full_index(self):
        async with self._index_lock:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.ws.configure, self.config())
            for w in self.ws.sources.warnings if self.ws.sources else []:
                self.window_log_message(T.LogMessageParams(T.MessageType.Warning, w))
            if self.elab_mode == "off":
                self.outdate_elaboration()
            self.after_index()
            if self.elab_mode != "off":
                self.start_elaboration()

    def schedule(self, path: str):
        self.pending.add(path)
        loop = asyncio.get_running_loop()
        if self._debounce is not None:
            self._debounce.cancel()
        self._debounce = loop.call_later(DEBOUNCE_SECONDS, self._start_flush)

    def _start_flush(self):
        self._debounce = None
        self._flush_task = asyncio.ensure_future(self.flush())

    async def fresh(self, timeout: float = 2.0) -> bool:
        """Wait until the index, the lint results and `published` describe the current text of
        every open document: pending edits are indexed now, without waiting for the debounce.
        False if that takes longer than `timeout`. Cancelling the wait never cancels the indexing."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            task = self._index_task
            if task is None or task.done():
                task = self._flush_task
                if task is None or task.done():
                    if not self.pending:
                        return True
                    if self._debounce is not None:
                        self._debounce.cancel()
                    self._start_flush()
                    task = self._flush_task
            try:
                await asyncio.wait_for(asyncio.shield(task), max(0.0, deadline - loop.time()))
            except asyncio.TimeoutError:
                return False
            except Exception:  # logged by the flush itself; the index may not be current
                return False

    async def flush(self):
        async with self._index_lock:
            paths, self.pending = self.pending, set()
            loop = asyncio.get_running_loop()
            docs, basis = self.documents, self.elab_basis

            def work() -> list[str]:
                for p in paths:
                    self.ws.update_document(p, docs.get(p))
                # An open file that is the file on disk again (reloaded after another program changed
                # it, or reverted) but not what the latest run compiled: no save will elaborate it.
                return [p for p in paths if p in docs and p in basis and docs[p] != basis[p]
                        and _same_text(p, docs[p], None)]
            reloaded = await loop.run_in_executor(None, work)
            self.after_index()
            if reloaded and self._elab_timer is None:  # unless a run is due anyway, or started meanwhile
                docs = self.documents
                if any(docs.get(p) != self.elab_basis.get(p) for p in reloaded):
                    self.schedule_elaboration(ELAB_SAVE_DELAY)

    @property
    def documents(self) -> dict[str, str]:
        out = {}
        for uri, doc in self.workspace.text_documents.items():
            out[uri_to_path(uri)] = doc.source
        return out

    def after_index(self):
        self.publish_diagnostics()
        self.protocol.notify("uvm/indexChanged", {"stats": self.ws.stats(), "version": self.ws.version})
        if get_capability(self.client_capabilities, "text_document.code_lens") is not None and \
                get_capability(self.client_capabilities, "workspace.code_lens.refresh_support", False):
            self.workspace_code_lens_refresh(None)
        if get_capability(self.client_capabilities, "text_document.inlay_hint") is not None and \
                get_capability(self.client_capabilities, "workspace.inlay_hint.refresh_support", False):
            self.workspace_inlay_hint_refresh(None)

    def publish_diagnostics(self):
        with self.ws.lock:
            per_file: dict[str, list] = {}
            for d in self.ws.user_diagnostics():
                per_file.setdefault(d.range.path, []).append(d)
            lint_cfg = self.settings.get("lint")
            if not isinstance(lint_cfg, dict) or lint_cfg.get("enabled", True):
                for path, diags in lint(self.ws).items():
                    per_file.setdefault(path, []).extend(diags)
        docs = self.documents
        shown = []
        for path, diags in self.elab_diags.items():
            # Parse errors come from both: keep the index's (it adds hints, e.g. for includes),
            # with slang's verdict when elaboration rates it higher.
            have = per_file.setdefault(path, [])
            twins: dict = {}
            for i, d in enumerate(have):
                twins.setdefault((d.range.line, d.message), i)
                twins.setdefault((d.range.line, d.code), i)
            hits = set()
            for d in self.current_elab_diags(path, diags, docs):
                i = twins.get((d.range.line, d.message), twins.get((d.range.line, d.code)))
                if i is None:
                    i = len(have)
                    have.append(d)
                elif RANK[d.severity] > RANK[have[i].severity]:
                    have[i] = replace(have[i], severity=d.severity)
                hits.add(i)
            shown += [have[i].severity for i in hits]
        self.elab_shown = shown
        for path, diags in per_file.items():
            then, now = self.diag_texts.get(path), self.ws.texts.get(path, docs.get(path))
            where = finding_lines(then, now)
            previous = {}
            for d in self.published.get(path, ()):
                line, end = where(d.range.line), where(d.range.end_line)
                if line is not None and end is not None:
                    moved = replace(d, range=replace(d.range, line=line, end_line=end))
                    previous[diagnostic_key(lsp_diagnostic(moved))] = d
            origins = self.diag_origins.setdefault(path, {})
            for i, d in enumerate(diags):
                key = diagnostic_key(lsp_diagnostic(d))
                old = previous.get(key)
                token = (old.data or {}).get(DIAGNOSTIC_ID) if old and _identity(old.data) == _identity(d.data) else None
                if token is None:
                    self._diagnostic_id += 1
                    token = self._diagnostic_id
                diags[i] = replace(d, data={**(d.data or {}), DIAGNOSTIC_ID: token})
                # Without data, two findings sent at the same range are indistinguishable.
                # Keep an ambiguous signature retired instead of silently selecting the newer one.
                origins[key] = token if key not in origins or origins[key] == token else None
        for path in self.published.keys() - per_file.keys():
            self.text_document_publish_diagnostics(T.PublishDiagnosticsParams(path_to_uri(path), []))
        for path, diags in per_file.items():
            if self.published.get(path, []) != diags:  # files whose diagnostics did not change are not sent
                items = [lsp_diagnostic(d) for d in diags]
                self.text_document_publish_diagnostics(T.PublishDiagnosticsParams(path_to_uri(path), items))
        self.published = per_file
        # Unchanged or not, what the client shows now describes this text.
        self.diag_texts = {path: self.ws.texts.get(path, docs.get(path)) for path in per_file}

    def current_elab_diags(self, path: str, diags: list[Diag], docs: dict[str, str]) -> list[Diag]:
        """The last run's diagnostics for a file, on the lines they are on now: an edit since that run
        moves those after it and drops those on the lines it changed, until the next run."""
        then, now = self.elab_texts.get(path), docs.get(path)
        if then is None and now is None:
            return diags  # compiled from disk and not open: a change on disk elaborates again
        if then is None or now is None:
            disk = _read(path)
            if disk is None:
                return []
            then, now = then if then is not None else disk, now if now is not None else disk
        if then == now:
            return diags
        where = shift_lines(then, now)

        def move(r: Range) -> Range | None:
            if r.path != path:
                return r
            line, end = where(r.line), where(r.end_line)
            return None if line is None or end is None else replace(r, line=line, end_line=end)

        out = []
        for d in diags:
            rng = move(d.range)
            if rng is not None:
                related = [(r, m) for r, m in ((move(r), m) for r, m in d.related) if r is not None]
                out.append(replace(d, range=rng, related=related))
        return out

    # -- elaboration -----------------------------------------------------------

    @property
    def elab_mode(self) -> str:
        mode = self.settings.get("elaboration")
        return mode if mode in ELAB_MODES else "onSave"

    def schedule_elaboration(self, delay: float):
        """Elaborate `delay` seconds from now; a later call resets it. With the setting off, the
        results of an Elaborate Now are dropped instead: they describe files that have changed."""
        if self.elab_mode == "off":
            self.outdate_elaboration()
            return
        if self._elab_timer is not None:
            self._elab_timer.cancel()
        self._elab_timer = asyncio.get_running_loop().call_later(delay, self.start_elaboration)

    def start_elaboration(self) -> asyncio.Future:
        """Elaborate now, superseding any run in progress. The task's result is the status."""
        self.stop_elaboration()
        self.elab_basis = self.documents
        self.elab_compiled = dict(self.elab_basis)
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
        self.elab_diags, self.elab_texts = {}, {}
        self.publish_diagnostics()
        self.notify_elaboration({"state": "off"})

    def outdate_elaboration(self):
        """Drop a finished on-demand run's results (a run in progress stays)."""
        if self.elab_status["state"] in ("done", "failed", "skipped"):
            self.elab_diags, self.elab_texts = {}, {}
            self.publish_diagnostics()
            self.notify_elaboration({"state": "off", "message": "files changed since Elaborate Now"})

    def notify_elaboration(self, status: dict):
        self.elab_status = {"state": status["state"], "errors": status.get("errors", 0),
                            "warnings": status.get("warnings", 0), "seconds": status.get("seconds", 0),
                            "message": status.get("message", ""), "uvm": status.get("uvm", "")}
        self.protocol.notify("uvm/elaborationStatus", self.elab_status)
        if self.elab_status["state"] != "running":
            waiters, self._elab_waiters = self._elab_waiters, []
            for w in waiters:
                if not w.done():  # a cancelled request
                    w.set_result(self.elab_status)

    def elab_overrides(self) -> dict[str, str]:
        """Unsaved editor text the compile uses instead of the files on disk."""
        files = self.ws.files()
        return {p: t for p, t in self.documents.items() if is_sv_file(p) or p in files}

    async def elaborate(self, run: int) -> dict | None:
        """One run in a worker process; None if a newer run or a stop superseded it."""
        try:
            return await self._elaborate(run)
        except asyncio.CancelledError:
            if run == self._elab_run:  # never leave the client showing "running"
                self.notify_elaboration({"state": "failed", "message": "stopped"})
            raise
        except Exception as e:
            log.exception("elaboration")
            if run != self._elab_run:
                return None
            self.notify_elaboration({"state": "failed", "message": f"{type(e).__name__}: {e}"})
            return self.elab_status

    async def _elaborate(self, run: int) -> dict | None:
        async with self._index_lock:
            if run != self._elab_run:
                return None
            with self.ws.lock:
                reason = elab.skip_reason(self.ws)
                request = None if reason else elab.request_for(self.ws, self.elab_overrides())
                uvm = elab.uvm_label(self.ws.sources)
            if reason:
                self.elab_diags, self.elab_texts = {}, {}
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
                     d["severity"], d["code"], elab.SOURCE, related, d.get("data")))
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
                notes.append(f"{counts['libraryErrors']} error(s) the UVM library reports on its own "
                             "are not shown")
        async with self._index_lock:
            if run != self._elab_run:
                return None
            self.elab_diags, self.elab_texts = per_file, request["overrides"]
            self.publish_diagnostics()
            # The counts describe what Problems shows: an error the index already reports is shown once.
            placed = [d.severity for ds in per_file.values() for d in ds]
            errors, warnings = (counts.get(s + "s", 0) + self.elab_shown.count(s) - placed.count(s)
                                for s in ("error", "warning"))
            self.notify_elaboration({"state": "failed" if result.get("error") else "done",
                                     "errors": errors, "warnings": warnings,
                                     "seconds": round(time.monotonic() - t0, 2), "message": "; ".join(notes),
                                     "uvm": uvm})
            return self.elab_status

    async def run_worker(self, run: int, request: dict) -> dict | None:
        """`python -m uvmls elab-worker` on the request: pyslang holds the GIL while it compiles."""
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [PACKAGE_PARENT] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
        try:
            # On the event loop's thread: the worker ends when the thread that started it does.
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "uvmls", "elab-worker", str(os.getpid()), str(ELAB_TIMEOUT),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=env)
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
    workspace = ls.workspace
    ls.protocol._workspace = LspWorkspace(workspace.root_uri, workspace_folders=list(workspace.folders.values()),
                                           position_encoding=workspace.position_encoding)
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
    commands = opts.get("clientCommands") if isinstance(opts, dict) else None
    ls.client_commands = frozenset(c for c in commands if isinstance(c, str)) \
        if isinstance(commands, list) else frozenset()
    if get_capability(params.capabilities, "text_document.inlay_hint") is not None:
        ls.feature(T.TEXT_DOCUMENT_INLAY_HINT, T.InlayHintOptions(resolve_provider=False))(inlay_hint)


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
    ls.elab_basis.setdefault(path, params.text_document.text)  # the latest run read it from disk
    if is_sv_file(path):
        ls.schedule(path)


@server.feature(T.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: UvmLanguageServer, params: T.DidChangeTextDocumentParams):
    ls.schedule(uri_to_path(params.text_document.uri))
    if ls.elab_mode == "onChange":
        ls.stop_elaboration()  # a run in progress compiles older text
        ls.schedule_elaboration(ELAB_CHANGE_DELAY)


@server.feature(T.TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: UvmLanguageServer, params: T.DidSaveTextDocumentParams):
    path = uri_to_path(params.text_document.uri)
    ls.schedule(path)
    # An auto-save of the text an onChange run has compiled (or is compiling): same result.
    if ls.elab_status["state"] in ("running", "done") and path in ls.elab_compiled \
            and ls.elab_compiled[path] == ls.documents.get(path):
        return
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
    # An open file's change arrives as didChange too: its save elaborates, and flush does when it
    # is reloaded or reverted to the file on disk.
    if changed:
        ls.schedule_elaboration(ELAB_SAVE_DELAY)


# -- navigation ----------------------------------------------------------------


def _indexed_lines(ls: UvmLanguageServer, path: str) -> list[str]:
    if path not in ls.ws.lines:
        ls.ws.lines[path] = fixes.EOL_RE.split(ls.ws.texts.get(path, ""))
    return ls.ws.lines[path]


def _indexed_column(ls: UvmLanguageServer, path: str, pos: T.Position) -> int:
    lines = _indexed_lines(ls, path)
    decoded = ls.workspace.position_codec.position_from_client_units(lines, pos)
    return len(lines[pos.line][:decoded.character].encode("utf-8")) if pos.line < len(lines) else 0


def _indexed_range(ls: UvmLanguageServer, rng: Range) -> T.Range:
    # slang columns are UTF-8 bytes; editor positions use the negotiated encoding.
    lines = _indexed_lines(ls, rng.path)

    def position(line, col):
        prefix = lines[line].encode("utf-8")[:col].decode("utf-8", errors="replace") if line < len(lines) else ""
        return T.Position(line, ls.workspace.position_codec.client_num_units(prefix))

    return T.Range(position(rng.line, rng.col), position(rng.end_line, rng.end_col))


@server.feature(T.TEXT_DOCUMENT_DEFINITION)
def definition(ls: UvmLanguageServer, params: T.DefinitionParams):
    path = uri_to_path(params.text_document.uri)
    with ls.ws.lock:
        config = ls.nav.config_db_definition(path, params.position.line, _indexed_column(ls, path, params.position))
        if config:
            return [T.Location(path_to_uri(r.path), _indexed_range(ls, r)) for r in config]
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
        config = ls.nav.config_db_hover(path, params.position.line, _indexed_column(ls, path, params.position))
        if config:
            md, field, rng = config
            formats = get_capability(ls.client_capabilities, "text_document.hover.content_format", [])
            if "uvm.showConfigDb" in ls.client_commands and T.MarkupKind.Markdown in (formats or []):
                args = quote(json.dumps([{"field": field}]), safe="")
                md += f"\n\n[show in the config_db cross-reference](command:uvm.showConfigDb?{args})"
            return T.Hover(contents=T.MarkupContent(kind=T.MarkupKind.Markdown, value=md), range=_indexed_range(ls, rng))
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


def _entry_range(entry: dict) -> Range:
    return Range(entry["file"], entry["line"], entry["col"], entry["endLine"], entry["endCol"])


def config_db_lenses(ls: UvmLanguageServer, path: str) -> list[T.CodeLens]:
    cfg = ls.settings.get("codeLens", {})
    if isinstance(cfg, dict) and not cfg.get("configDb", True):
        return []
    if get_capability(ls.client_capabilities, "text_document.code_lens") is None:
        return []
    report = config_db_report(ls.ws)
    lenses = []
    for entry in report.by_file.get(path, ()):
        row = report.by_field.get(entry["field"])
        command, arguments = "uvm.showConfigDb", [{}]
        if id(entry) in report.dynamic_ids:
            title = "$(database) config_db · dynamic field"
        else:
            title = f"$(database) '{entry['field']}' · "
            arguments = [{"field": entry["field"]}]
            if entry["op"] == "set":
                count = len(row["gets"])
                title += f"{count} get{'s' if count != 1 else ''}" if count else "no get"
                if row["status"] == "type-mismatch":
                    title += " · type mismatch"
            else:
                count = len(row["sets"])
                if count == 1:
                    target = row["sets"][0]
                    title += f"set in {os.path.basename(target['file'])}:{target['line'] + 1}"
                    if "uvm.revealLocation" in ls.client_commands:
                        rng = _indexed_range(ls, _entry_range(target))
                        command, arguments = "uvm.revealLocation", [{
                            **_entry_range(target).to_json(), "col": rng.start.character, "endCol": rng.end.character}]
                else:
                    title += f"{count} sets" if count else "no set"
        lenses.append(T.CodeLens(range=_indexed_range(ls, _entry_range(entry)), command=T.Command(
            title=title, command=command, arguments=arguments)))
    return lenses


@server.feature(T.TEXT_DOCUMENT_CODE_LENS, T.CodeLensOptions(resolve_provider=False))
async def code_lens(ls: UvmLanguageServer, params: T.CodeLensParams):
    if not await ls.fresh():
        return []
    path = uri_to_path(params.text_document.uri)
    lenses = []
    with ls.ws.lock:
        lenses.extend(config_db_lenses(ls, path))
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


def inlay_hint(ls: UvmLanguageServer, params: T.InlayHintParams):
    if ls.settings.get("inlayHints", True) is False:
        return []
    path = uri_to_path(params.text_document.uri)
    hints = []
    with ls.ws.lock:
        creates = ls.ws.creates_by_file.get(path, ())
        result = None
        start, end = params.range.start, params.range.end
        for cr in creates:
            pos = _indexed_range(ls, cr.range).end
            if not (start.line, start.character) <= (pos.line, pos.character) < (end.line, end.character):
                continue
            if result is None:
                result = effective_type(ls.ws, ls.settings.get("inlayHints.test"))
            for entry in result.get(cr.range, ()):
                ov = entry.override
                if ov is None:
                    continue
                label = f" → {entry.effective}"
                count = result.counts[cr.range]
                if entry.instances < count:
                    label += f" ({entry.instances} of {count} instances)"
                tooltip = f"overridden in {ov.owner}::{ov.method} ({os.path.basename(ov.range.path)}:{ov.range.line + 1})"
                hints.append(T.InlayHint(position=pos, kind=T.InlayHintKind.Type, padding_left=True,
                    label=[T.InlayHintLabelPart(value=label, location=T.Location(
                        path_to_uri(ov.range.path), _indexed_range(ls, ov.range)))],
                    tooltip=T.MarkupContent(kind=T.MarkupKind.Markdown, value=tooltip)))
        for rng, bound, tooltip in ls.nav.bound_member_hints(path):
            pos = _indexed_range(ls, rng).end
            if (start.line, start.character) <= (pos.line, pos.character) < (end.line, end.character):
                hints.append(T.InlayHint(position=pos, kind=T.InlayHintKind.Type, padding_left=True,
                    label=f": {bound}", tooltip=T.MarkupContent(kind=T.MarkupKind.Markdown, value=tooltip)))
    return sorted(hints, key=lambda h: (h.position.line, h.position.character))


# -- code actions ------------------------------------------------------------------

FIX_ALL_KIND = "source.fixAll.uvm"
FIX_SOURCES = ("uvm", "slang", elab.SOURCE)
FIX_DATA = indexer.FIX_DATA
DIAGNOSTIC_ID = "_uvmls_id"
POSITION_KEYS = ("at", DIAGNOSTIC_ID)  # metadata, not the lint's semantic identity


def diagnostic_key(d: T.Diagnostic) -> tuple:
    r = d.range
    return d.source, d.code, d.message, r.start.line, r.start.character, r.end.line, r.end.character


def finding_lines(then: str | None, now: str | None):
    """Map unchanged, unambiguous lines across separate edits."""
    if then is None or now is None or then == now:
        return lambda line: line
    before, after = fixes.EOL_RE.split(then), fixes.EOL_RE.split(now)
    old_counts, new_counts = Counter(before), Counter(after)
    blocks = SequenceMatcher(None, before, after).get_matching_blocks()
    # A diff can attribute a deleted duplicate's line to its survivor. Text alone cannot
    # establish which occurrence survived, so none of those lines can retain an identity.
    lines = {a + i: b + i for a, b, n in blocks for i in range(n)
             if old_counts[before[a + i]] == new_counts[before[a + i]] == 1}
    return lines.get


def _kind_wanted(kind: str, only: list[str] | None) -> bool:
    if only is None:
        return kind == T.CodeActionKind.QuickFix
    return any(kind == o or kind.startswith(o + ".") for o in only)


def _fixable(ls: UvmLanguageServer, path: str, d: T.Diagnostic) -> bool:
    """Whether a diagnostic the client sent is ours, and one a quick fix may apply to."""
    if d.source not in FIX_SOURCES:
        return False
    if d.source == "uvm":
        return True  # any lint finding can at least be ignored on its line
    keys = FIX_DATA.get(str(d.code))
    if keys is None:
        return False
    data = _plain(d.data)
    if isinstance(data, dict):
        return all(k in data for k in keys)
    return diagnostic_key(d) in ls.diag_origins.get(path, {}) or any(
               x.code == d.code and x.message == d.message and x.range.line == d.range.start.line
               for x in ls.published.get(path, ()))  # a client that does not send data back


def _identity(data) -> dict | None:
    data = _plain(data)
    return ({k: v for k, v in data.items() if k not in POSITION_KEYS} or None) if isinstance(data, dict) else None


def current_finding(d: T.Diagnostic, published: list[Diag], then: str | None, now: str | None) -> Diag | None:
    """Resolve a published identity, or an untagged diagnostic on an unchanged mapped line."""
    same = [x for x in published if x.source == d.source and x.code == d.code and x.message == d.message]
    payload = _plain(d.data)
    if isinstance(payload, dict) and DIAGNOSTIC_ID in payload:
        return next((x for x in same if (x.data or {}).get(DIAGNOSTIC_ID) == payload[DIAGNOSTIC_ID]), None)
    data = _identity(d.data)
    same = [x for x in same if data is None or _identity(x.data) == data]
    where = finding_lines(then, now)
    line, end = where(d.range.start.line), where(d.range.end.line)
    return next((x for x in same if x.range.line == line and x.range.end_line == end
                 and x.range.col == d.range.start.character and x.range.end_col == d.range.end.character), None)


class _Actions:
    """Turns fixes into LSP code actions for one request."""

    def __init__(self, ls: UvmLanguageServer, files: fixes.FileTexts, uris: dict[str, str]):
        caps = ls.client_capabilities
        self.versioned = bool(get_capability(caps, "workspace.workspace_edit.document_changes", False))
        self.codec = ls.workspace.position_codec
        self.files, self.uris = files, uris

    def position(self, ft: fixes.FileText, offset: int) -> T.Position:
        line, col = ft.position(offset)
        return T.Position(line, self.codec.client_num_units(ft.line(line)[:col]))

    def edit(self, changes: list[fixes.TextChange]) -> T.WorkspaceEdit:
        by_path: dict[str, list[fixes.TextChange]] = {}
        for c in changes:
            by_path.setdefault(c.path, []).append(c)
        per_file = []
        for path, cs in by_path.items():
            ft = self.files.get(path)
            edits = [T.TextEdit(T.Range(self.position(ft, c.start), self.position(ft, c.end)), c.text)
                     for c in sorted(cs, key=lambda c: (c.start, c.end))]
            per_file.append((self.uris.get(path) or path_to_uri(path), ft.version, edits))
        if self.versioned:  # the client refuses an edit made for an older version of a document
            return T.WorkspaceEdit(document_changes=[
                T.TextDocumentEdit(T.OptionalVersionedTextDocumentIdentifier(uri, version), edits)
                for uri, version, edits in per_file])
        return T.WorkspaceEdit(changes={uri: edits for uri, _, edits in per_file})

    def action(self, fix: fixes.Fix, diagnostics: list[T.Diagnostic],
               kind: str = T.CodeActionKind.QuickFix) -> T.CodeAction:
        a = T.CodeAction(title=fix.title, kind=kind, diagnostics=diagnostics or None,
                         is_preferred=fix.preferred or None)
        if fix.changes:
            a.edit = self.edit(fix.changes)
        if fix.command:
            a.command = T.Command(title=fix.title, command=fix.command[0], arguments=fix.command[1])
        if fix.disabled:
            a.disabled = T.CodeActionDisabled(fix.disabled)
        return a


def code_actions(ls: UvmLanguageServer, uri: str, incoming: list[T.Diagnostic], then: str | None,
                 fix_all: bool = False) -> list:
    """The code actions for a document's diagnostics, and its fix-all source action if asked for
    (call with the workspace locked)."""
    path = uri_to_path(uri)
    docs = {uri_to_path(u): (doc.source, doc.version) for u, doc in ls.workspace.text_documents.items()}
    uris = {uri_to_path(u): u for u in ls.workspace.text_documents}
    uris[path] = uri
    files = fixes.FileTexts(docs)
    client = fixes.Client(roots=list(ls.workspace_roots), commands=ls.client_commands,
                          disabled_support=bool(get_capability(
                              ls.client_capabilities, "text_document.code_action.disabled_support", False)))
    ctx = fixes.Context(ls.ws, files, client, path, ls.nav)
    make = _Actions(ls, files, uris)
    published = ls.published.get(path, [])
    ft = files.get(path)
    now = ft.text if ft else None
    out = []
    per_code: dict[str, tuple[T.CodeAction | None, list[Diag]]] = {}
    emitted: set[str] = set()
    order = sorted(incoming, key=lambda d: (d.severity or 4, d.range.start.line, d.range.start.character))
    for d in order:
        sent, payload = d, _plain(d.data)
        if not isinstance(payload, dict) or DIAGNOSTIC_ID not in payload:
            origins = ls.diag_origins.get(path)
            if origins is not None:
                token = origins.get(diagnostic_key(d))
                if token is None:
                    continue
                sent = T.Diagnostic(d.range, d.message, code=d.code, source=d.source,
                                    data={**(payload if isinstance(payload, dict) else {}), DIAGNOSTIC_ID: token})
        finding = current_finding(sent, published, then, now)
        if finding is None:
            continue
        for fix in fixes.fixes_for(ctx, finding):
            out.append(make.action(fix, [d]))
        if finding.code in fixes.SAFE_CODES and finding.source == "uvm":
            if finding.code not in per_code:
                changes, fixed = fixes.fix_all(ctx, published, finding.code)
                action = make.action(fixes.Fix(
                    f"Fix all {len(fixed)} '{finding.code}' problems in this file", changes),
                    [lsp_diagnostic(x) for x in fixed]) if len(fixed) >= 2 else None
                per_code[finding.code] = action, fixed
            action, fixed = per_code[finding.code]
            if action is not None and finding.code not in emitted and any(x is finding for x in fixed):
                out.append(action)
                emitted.add(finding.code)
        for fix in fixes.ignore_fixes(ctx, finding):
            out.append(make.action(fix, [d]))
    if fix_all:
        changes, fixed = fixes.fix_all(ctx, published)
        if changes:
            out.append(make.action(fixes.Fix("Fix all auto-fixable UVM problems", changes),
                                   [lsp_diagnostic(x) for x in fixed], FIX_ALL_KIND))
    return out


@server.feature(T.TEXT_DOCUMENT_CODE_ACTION, T.CodeActionOptions(
    code_action_kinds=[T.CodeActionKind.QuickFix, FIX_ALL_KIND], resolve_provider=False))
async def code_action(ls: UvmLanguageServer, params: T.CodeActionParams):
    t0 = time.perf_counter()
    uri = params.text_document.uri
    path = uri_to_path(uri)
    only = [str(getattr(k, "value", k)) for k in params.context.only] if params.context.only else None
    incoming = [d for d in params.context.diagnostics if _fixable(ls, path, d)] \
        if _kind_wanted(T.CodeActionKind.QuickFix, only) else []
    fix_all = _kind_wanted(FIX_ALL_KIND, only)  # asked for by name only (codeActionsOnSave, say)
    if not incoming and not fix_all:
        return None  # moving the cursor over clean code: no wait, no lock
    then = ls.diag_texts.get(path)  # the text the client's diagnostics describe, before the flush
    if not await ls.fresh():
        log.debug("codeAction: the index did not catch up in time")
        return None
    t1 = time.perf_counter()
    if not ls.ws.lock.acquire(blocking=False):
        return None
    try:
        actions = code_actions(ls, uri, incoming, then, fix_all)
    finally:
        ls.ws.lock.release()
    log.debug("codeAction: %d action(s) in %.1f ms after waiting %.1f ms", len(actions),
              (time.perf_counter() - t1) * 1000, (t1 - t0) * 1000)
    return actions or None


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
    path, line, col = uri_to_path(uri), pos.get("line", 0), pos.get("character", 0)
    with ls.ws.lock:
        cls = ls.nav.class_at(path, line, col, _text(ls, uri))
        # The graph holds only the UVM classes user code uses; for any other, the class around the cursor.
        if cls is not None and cls.library and \
                cls.qualname not in {n["id"] for n in class_graph(ls.ws)["nodes"]}:
            cls = ls.ws.enclosing(path, line, col)[0]
        return cls.qualname if cls else None


@server.feature("uvm/reindex")
async def uvm_reindex(ls: UvmLanguageServer, params):
    await ls.full_index()
    return ls.ws.stats()


@server.feature("uvm/elaborate")
async def uvm_elaborate(ls: UvmLanguageServer, params):
    """Elaborate now, whatever the setting; returns the status of the run that ends next: this one,
    or whichever superseded it (another request, an edit in onChange mode). Cancelling the request
    stops only the wait, never a run that a save or another request is waiting on."""
    waiter = asyncio.get_running_loop().create_future()
    ls._elab_waiters.append(waiter)
    ls.start_elaboration()
    return await waiter


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
