"""Full elaboration with slang: the errors a simulator's compile and elaboration report.

The index works on syntax trees alone, so it cannot see type errors, unknown
members, wrong argument counts, missing modules or packages, bad overrides and
the like. This compiles and elaborates the whole design with pyslang's driver.
pyslang holds the GIL for the whole compile, so the language server runs it in a
worker process: `python -m uvmls elab-worker` reads one JSON request on stdin
and writes one JSON result on stdout.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time

import pyslang as ps
from pyslang import ast, driver
from pyslang import syntax as S

from .indexer import SHIM_DIR, UVM_MACROS_PRELUDE, _uses_unknown_uvm_macro
from .project import BUNDLED_UVM, SourceSet, norm

log = logging.getLogger("uvmls")

SOURCE = "slang-elab"
SEVERITY = {ps.DiagnosticSeverity.Error: "error", ps.DiagnosticSeverity.Fatal: "error",
            ps.DiagnosticSeverity.Warning: "warning"}
NOTE_RE = re.compile(r"^(.+?):(\d+):(\d+): note: (.*)$")


# -- requests ------------------------------------------------------------------


def request_for(ws, overrides: dict[str, str] | None = None, tops=(), args=()) -> dict:
    """The elaboration request for a workspace's sources; overrides hold unsaved editor text."""
    s = ws.sources
    library = [d for d in (s.uvm_dir, os.path.dirname(BUNDLED_UVM), SHIM_DIR) if d]
    return {"roots": list(s.roots), "includeDirs": list(s.include_dirs), "defines": dict(s.defines),
            "overrides": dict(overrides or {}), "tops": list(tops or ()), "library": library,
            "args": list(args or ())}


def skip_reason(ws) -> str | None:
    """Why the workspace cannot be elaborated as configured, or None."""
    s = ws.sources
    if s is None or all(r in s.library_roots for r in s.roots):
        return "there are no SystemVerilog sources to elaborate"
    if s.uvm_source == "builtin":
        return ("the built-in UVM model cannot be elaborated; turn uvm.useBundledUvm back on "
                "or set uvm.uvmHome")
    if s.mode == "discovery":
        dups = sorted(name for name, units in ws.units_by_name.items()
                      if len({u.range.path for u in units}) > 1)
        if dups:
            names = ", ".join(dups[:3]) + (f" and {len(dups) - 3} more" if len(dups) > 3 else "")
            return (f"{names} {'is' if len(dups) == 1 else 'are'} defined in more than one file, as "
                    "when a workspace holds several testbenches; list the files to compile in "
                    "uvm.filelists (CLI: -f)")
    return None


def uvm_label(s: SourceSet | None) -> str:
    """The UVM library in use, e.g. 'bundled Accellera UVM 1.2' or 'UVM 1.1d from /opt/uvm/src'."""
    if s is None or s.uvm_source == "builtin":
        return "built-in model"
    version = f"UVM {s.uvm_version}" if s.uvm_version else "UVM"
    if s.uvm_source == "bundled":
        return f"bundled Accellera {version}"
    return f"{version} from {s.uvm_dir}" + (" (the project's own)" if s.uvm_source == "project" else "")


# -- elaboration ----------------------------------------------------------------


class _Locator:
    """Maps slang locations to files, the way indexer.Extractor.file_loc does."""

    def __init__(self, sm, library: list[str]):
        self.sm = sm
        self.library = [norm(p) for p in library]
        self._paths: dict[int, str | None] = {}

    def path(self, buffer) -> str | None:
        """The file behind a buffer; None for generated text such as the macro prelude."""
        bid = buffer.id
        if bid not in self._paths:
            p = str(self.sm.getFullPath(buffer) or "")
            self._paths[bid] = None if p in ("", ".") or p.startswith("<") else norm(p)
        return self._paths[bid]

    def is_library(self, path: str) -> bool:
        return any(path == d or path.startswith(d + os.sep) for d in self.library)

    def file_loc(self, loc):
        """(location in a file, expanded): a macro argument where the user wrote it, else the
        outermost expansion site."""
        sm = self.sm
        if not sm.isMacroLoc(loc):
            return loc, False
        if sm.isMacroArgLoc(loc):
            orig = sm.getFullyOriginalLoc(loc)
            path = None if sm.isMacroLoc(orig) else self.path(orig.buffer)
            if path and not self.is_library(path):
                return orig, False
        return sm.getFullyExpandedLoc(loc), True

    def span(self, diag) -> tuple[str, int, int, int, int] | None:
        """(path, line, col, end line, end col), 0-based; None when the diagnostic has no file."""
        sm = self.sm
        if diag.location == ps.SourceLocation.NoLocation:
            return None
        start, expanded = self.file_loc(diag.location)
        path = self.path(start.buffer)
        if path is None:
            return None
        end = start
        if expanded:  # the whole macro invocation
            loc = diag.location
            while sm.isMacroLoc(loc):
                rng = sm.getExpansionRange(loc)
                loc, end = rng.start, rng.end
        else:
            for r in diag.ranges:
                s, _ = self.file_loc(r.start)
                e, _ = self.file_loc(r.end)
                if s.buffer == start.buffer == e.buffer and s.offset <= start.offset <= e.offset:
                    end = e
                    break
        if end.buffer != start.buffer or end.offset < start.offset:
            end = start
        return (path, sm.getLineNumber(start) - 1, sm.getColumnNumber(start) - 1,
                sm.getLineNumber(end) - 1, sm.getColumnNumber(end) - 1)


class _Notes:
    """slang's notes ("declared here", "did you mean ...") are only reachable as text."""

    def __init__(self, eng):
        self.eng = eng
        c = self.client = ps.TextDiagnosticClient()
        c.showColors(False)
        c.showSourceLine(False)
        c.showIncludeStack(False)
        c.showOptionName(False)
        c.showAbsPaths(True)
        c.setColumnUnit(ps.ColumnUnit.Byte)
        eng.clearClients()
        eng.addClient(c)
        eng.setErrorLimit(0)

    def of(self, diag) -> list[dict]:
        self.client.clear()
        self.eng.issue(diag)
        out = []
        for line in self.client.getString().splitlines():
            m = NOTE_RE.match(line)
            if m and os.path.isabs(m.group(1)):  # not a generated buffer such as the prelude
                out.append({"file": norm(m.group(1)), "line": int(m.group(2)) - 1,
                            "col": int(m.group(3)) - 1, "message": m.group(4)})
        return out


def _quote(arg: str) -> str:
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _code(code) -> str:
    return str(code).replace("DiagCode(", "").rstrip(")")


def _with_uvm_macros(d, loc: _Locator, include_dirs: list[str]):
    """Re-parse user files that use UVM macros without including them, with the real ones
    predefined, as the indexer does. Returns the new list of trees, or None if none changed."""
    if not any(os.path.isfile(os.path.join(i, "uvm_macros.svh")) for i in include_dirs):
        return None
    sm = d.sourceManager
    trees, prelude = [], None
    for tree in d.syntaxTrees:
        path = loc.path(tree.root.endOfFile.location.buffer)
        if path and not loc.is_library(path) and _uses_unknown_uvm_macro(tree):
            prelude = prelude or sm.assignText(UVM_MACROS_PRELUDE, '`include "uvm_macros.svh"\n')
            tree = S.SyntaxTree.fromBuffers([prelude, sm.readSource(path)], sm, tree.options)
        trees.append(tree)
    return trees if prelude else None


def _counts(diags: list[dict], library: dict[str, int]) -> dict:
    sev = [d["severity"] for d in diags]
    return {"errors": sev.count("error"), "warnings": sev.count("warning"), "infos": sev.count("info"),
            "libraryErrors": library["error"], "libraryWarnings": library["warning"]}


def _failed(message: str) -> dict:
    return {"diagnostics": [], "tops": [], "counts": _counts([], {"error": 0, "warning": 0}),
            "error": message}


def _diag(path: str | None, severity: str, code: str, message: str) -> dict:
    return {"file": path, "line": 0, "col": 0, "endLine": 0, "endCol": 0, "severity": severity,
            "code": code, "message": message, "related": []}


def _elaborate(req: dict) -> dict:
    overrides = req.get("overrides") or {}
    roots = list(dict.fromkeys(req["roots"]))
    missing = [r for r in roots if not os.path.isfile(r)]  # slang's loader needs the file on disk
    d = driver.Driver()
    d.addStandardArgs()
    args = ["slang"]
    for inc in req.get("includeDirs") or []:
        args += ["-I", inc]
    for name, value in (req.get("defines") or {}).items():
        args += ["-D", f"{name}={value}" if value != "" else name]
    for top in req.get("tops") or []:
        args += ["--top", top]
    args += list(req.get("args") or []) + [r for r in roots if r not in missing]
    if not d.parseCommandLine(" ".join(_quote(a) for a in args)) or not d.processOptions():
        return _failed("slang did not accept the elaboration options")
    sm = d.sourceManager
    for path, text in overrides.items():
        sm.assignText(path, text)
    d.parseAllSources()
    loc = _Locator(sm, req.get("library") or [])
    trees = _with_uvm_macros(d, loc, req.get("includeDirs") or [])
    if trees is None:
        comp = d.createCompilation()
    else:
        comp = ast.Compilation(d.createOptionBag())
        for tree in trees:
            comp.addSyntaxTree(tree)
    diags = comp.getAllDiagnostics()
    eng = d.diagEngine
    notes = _Notes(eng)
    out = [_diag(r, "error", "io", "cannot read file") for r in missing]
    out += [_diag(None, "error", "io", str(e)) for e in d.sourceLoader.errors]
    seen = set()
    library = {"error": 0, "warning": 0}
    for diag in diags:
        sev = eng.getSeverity(diag.code, diag.location)
        code = _code(diag.code)
        if sev == ps.DiagnosticSeverity.Ignored or code == "NoTopModules":
            continue  # without a top module, packages and classes are still checked
        severity = SEVERITY.get(sev, "info")
        span = loc.span(diag)
        if span is not None and loc.is_library(span[0]):
            if severity in library:
                library[severity] += 1
            continue
        message = eng.formatMessage(diag)
        if (span, code, message) in seen:
            continue  # e.g. a macro that expands its argument twice
        seen.add((span, code, message))
        item = _diag(None, severity, code, message)
        if span is not None:
            item.update(zip(("file", "line", "col", "endLine", "endCol"), span))
        item["related"] = notes.of(diag)
        out.append(item)
    out.sort(key=lambda x: (x["file"] or "", x["line"], x["col"], x["code"], x["message"]))
    return {"diagnostics": out, "tops": [i.name for i in comp.getRoot().topInstances],
            "counts": _counts(out, library), "error": None}


def elaborate(request: dict) -> dict:
    """Compile and elaborate a request (see request_for). Never raises: failures set 'error'."""
    t0 = time.monotonic()
    try:
        result = _elaborate(request)
    except Exception as e:
        log.exception("elaboration failed")
        result = _failed(f"{type(e).__name__}: {e}")
    result["seconds"] = round(time.monotonic() - t0, 3)
    return result


def worker():
    """`python -m uvmls elab-worker`: one JSON request on stdin, one JSON result on stdout."""
    sys.stdout.flush()
    result_fd = os.dup(1)
    os.dup2(2, 1)  # anything slang prints goes to stderr, never into the result
    try:
        request = json.loads(sys.stdin.buffer.read())
    except ValueError as e:
        result = _failed(f"bad elaboration request: {e}")
        result["seconds"] = 0.0
    else:
        result = elaborate(request)
    with os.fdopen(result_fd, "w") as f:
        json.dump(result, f)
