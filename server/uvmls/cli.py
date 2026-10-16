"""Command line access to the index, for scripts and CI.

    python -m uvmls lint     -f tb.f            # exit 1 if any warning/error
    python -m uvmls elab     -f tb.f [--top T]  # full slang elaboration; exit 1 if any error
    python -m uvmls graph    -f tb.f -o graph.json
    python -m uvmls topology -f tb.f --test my_test
    python -m uvmls configdb -f tb.f
    python -m uvmls stats    [DIR]
    python -m uvmls mcp      [DIR] -f tb.f      # MCP server over stdio (see mcp.py)

The UVM library is --uvm-home or $UVM_HOME, else a uvm_pkg.sv the project already
compiles, else the bundled Accellera UVM 1.2 (--no-bundled-uvm: the built-in model).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .elab import displayed, elaborate, request_for, skip_reason, uvm_label
from .lints import lint
from .model import Diag, Range
from .project import ProjectConfig, build_source_set, relative_or_absolute
from .sarif import write_log
from .views import class_graph, config_db_report, topology
from .workspace import Workspace


def _workspace(args, index: bool = True, config: ProjectConfig | None = None) -> Workspace:
    """index=False only works out the sources (all `elab` needs with a filelist); `config` replaces
    the one the flags describe."""
    root = os.path.abspath(args.dir)
    cfg = config or ProjectConfig(workspace_roots=[root],
                                  filelists=[os.path.abspath(f) for f in args.filelist],
                                  include_dirs=[os.path.abspath(d) for d in args.incdir],
                                  defines=dict(d.partition("=")[::2] for d in args.define),
                                  uvm_home=args.uvm_home, env_uvm_home=os.environ.get("UVM_HOME"),
                                  bundled_uvm=not args.no_bundled_uvm, exclude=list(args.exclude))
    ws = Workspace()
    if index:
        ws.configure(cfg)
    else:
        ws.config, ws.sources = cfg, build_source_set(cfg)
    for w in ws.sources.warnings:
        print(f"warning: {w}", file=sys.stderr)
    return ws


def _print_tree(node, depth=0, out=sys.stdout):
    extra = []
    if node.get("overriddenFrom"):
        extra.append(f"override of {node['overriddenFrom']}")
    if node.get("conditional"):
        extra.append(f"if {node['condition']}")
    tail = f"  ({'; '.join(extra)})" if extra else ""
    out.write(f"{'  ' * depth}{node['name']} : {node['type']} [{node['role']}]{tail}\n")
    for c in node["children"]:
        _print_tree(c, depth + 1, out)


def _elab(ws: Workspace, args) -> int:
    """Exit status: 0 clean, 1 errors, 2 not elaborated (skipped or failed)."""
    reason = skip_reason(ws)
    if reason:
        if args.sarif:
            write_log(args.sarif, args.dir, [])
        print(f"elaboration skipped: {reason}", file=sys.stderr)
        return 2
    request = request_for(ws, tops=args.top)
    request["listAll"] = bool(args.sarif)  # a code-scanning report must not drop findings past the display limits
    result = elaborate(request)
    if args.sarif:
        write_log(args.sarif, args.dir, [Diag(
            Range(d["file"] or "", d["line"], d["col"], d["endLine"], d["endCol"]),
            d["message"], d["severity"], d["code"], "slang-elab") for d in result["diagnostics"]],
            fallback=args.filelist[0] if args.filelist else None)
        listed, more = displayed(result["diagnostics"])  # the console and --json keep their limits
        result = {**result, "diagnostics": listed, "counts": {**result["counts"], "omitted": result["counts"]["omitted"] + more}}
    out = sys.stderr if args.sarif == "-" else sys.stdout
    if args.json and args.sarif != "-":
        print(json.dumps(result, indent=1))
    elif not args.json:
        for d in result["diagnostics"]:
            where = f"{relative_or_absolute(d['file'])}:{d['line'] + 1}:{d['col'] + 1}: " if d["file"] else ""
            print(f"{where}{d['severity']}: {d['message']} [{d['code']}]", file=out)
        c = result["counts"]
        notes = [f"top: {', '.join(result['tops']) or 'none'}", uvm_label(ws.sources)]
        if c.get("omitted"):
            notes.append(f"{c['omitted']} more not listed")
        if c.get("libraryErrors"):
            notes.append(f"{c['libraryErrors']} error(s) the UVM library reports on its own not shown")
        print(f"{c['errors']} error(s), {c['warnings']} warning(s) in {result['seconds']}s "
              f"({'; '.join(notes)})", file=sys.stderr)
    if result["error"]:
        print(f"elaboration failed: {result['error']}", file=sys.stderr)
        return 2
    return 1 if result["counts"]["errors"] else 0


def findings(ws: Workspace) -> list[Diag]:
    """Lint findings and parse errors or warnings of the user files, each once, in file order."""
    diags = [d for d in ws.user_diagnostics() if d.severity in ("error", "warning")]
    for ds in lint(ws).values():
        diags += ds
    seen = set()
    uniq = []
    for d in sorted(diags, key=lambda d: (d.range.path, d.range.line, d.range.col)):
        key = (d.range.path, d.range.line, d.range.col, d.message)
        if key not in seen:
            seen.add(key)
            uniq.append(d)
    return uniq


def pyslang_version() -> str:
    try:
        from importlib.metadata import version
        return version("pyslang")
    except Exception:  # not installed as a distribution
        return "unknown"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m uvmls", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"uvmls {__version__} (pyslang {pyslang_version()})")
    ap.add_argument("command", choices=["graph", "topology", "configdb", "lint", "stats", "elab", "mcp"])
    ap.add_argument("dir", nargs="?", default=".", help="workspace directory (default: .)")
    ap.add_argument("-f", dest="filelist", action="append", default=[], help="filelist (.f)")
    ap.add_argument("-I", "--incdir", action="append", default=[], help="include directory")
    ap.add_argument("-D", "--define", action="append", default=[], help="macro define NAME[=VALUE]")
    ap.add_argument("--uvm-home", help="UVM installation (falls back to $UVM_HOME)")
    ap.add_argument("--no-bundled-uvm", action="store_true",
                    help="use the built-in UVM model instead of the bundled UVM 1.2")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                    help="leave out files matching this glob in discovery mode (as uvm.exclude)")
    ap.add_argument("--test", help="test class for topology")
    ap.add_argument("--top", action="append", default=[], help="top module for elab (default: slang picks)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    ap.add_argument("--sarif", metavar="FILE", help="write lint or elab diagnostics as SARIF (use - for stdout)")
    ap.add_argument("-o", "--output", help="write JSON to this file")
    # Intermixed: `lint --test t DIR -f x.f` has the positional after an option, which argparse before
    # Python 3.12 would otherwise take as absent and reject DIR as unrecognized.
    args = ap.parse_intermixed_args(argv)
    if args.sarif and args.command not in ("lint", "elab"):
        ap.error("--sarif is only available for lint and elab")
    if args.command == "mcp":
        from .mcp import serve
        sys.exit(serve(args))
    # Without a filelist, elab needs the index to spot several testbenches in one tree.
    ws = _workspace(args, index=not (args.command == "elab" and args.filelist))

    if args.command == "elab":
        sys.exit(_elab(ws, args))
    if args.command == "stats":
        data = ws.stats()
    elif args.command == "graph":
        data = class_graph(ws)
    elif args.command == "topology":
        data = topology(ws, args.test)
    elif args.command == "configdb":
        data = config_db_report(ws)
    else:
        uniq = findings(ws)
        if args.sarif:
            write_log(args.sarif, args.dir, uniq)
        out = sys.stderr if args.sarif == "-" else sys.stdout
        if args.json and args.sarif != "-":
            print(json.dumps([{"file": d.range.path, "line": d.range.line + 1, "col": d.range.col + 1,
                               "severity": d.severity, "code": d.code, "message": d.message}
                              for d in uniq], indent=2))
        elif not args.json:
            for d in uniq:
                rel = relative_or_absolute(d.range.path)
                print(f"{rel}:{d.range.line + 1}:{d.range.col + 1}: {d.severity}: {d.message} [{d.code}]", file=out)
            print(f"{len(uniq)} problem(s)", file=sys.stderr)
        sys.exit(1 if any(d.severity in ("error", "warning") for d in uniq) else 0)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(data, f, indent=1)
    elif args.json or args.command in ("graph", "stats"):
        print(json.dumps(data, indent=1))
    elif args.command == "topology":
        print(f"test: {data['test']}   (tests: {', '.join(data['tests'])})")
        if data["root"]:
            _print_tree(data["root"])
        for c in data["connections"]:
            print(f"  {c['source']}.{c['sourcePort']} -> {c['target']}.{c['targetPort']}"
                  + ("  (alias)" if c["kind"] == "alias" else ""))
    elif args.command == "configdb":
        for row in data["fields"]:
            print(f"{row['field']:24} {row['status']:14} types={', '.join(row['types'])}  "
                  f"sets={len(row['sets'])} gets={len(row['gets'])}")


if __name__ == "__main__":
    main()
