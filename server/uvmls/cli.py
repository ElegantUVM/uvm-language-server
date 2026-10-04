"""Command line access to the index, for scripts and CI.

    python -m uvmls lint     -f tb.f            # exit 1 if any warning/error
    python -m uvmls graph    -f tb.f -o graph.json
    python -m uvmls topology -f tb.f --test my_test
    python -m uvmls configdb -f tb.f
    python -m uvmls stats    [DIR]

The UVM library is --uvm-home or $UVM_HOME, else a uvm_pkg.sv the project already
compiles, else the bundled Accellera UVM 1.2 (--no-bundled-uvm: the built-in model).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .lints import lint
from .project import ProjectConfig
from .views import class_graph, config_db_report, topology
from .workspace import Workspace


def _workspace(args) -> Workspace:
    root = os.path.abspath(args.dir)
    cfg = ProjectConfig(workspace_roots=[root],
                        filelists=[os.path.abspath(f) for f in args.filelist],
                        include_dirs=[os.path.abspath(d) for d in args.incdir],
                        defines=dict(d.partition("=")[::2] for d in args.define),
                        uvm_home=args.uvm_home or os.environ.get("UVM_HOME"),
                        bundled_uvm=not args.no_bundled_uvm)
    ws = Workspace()
    ws.configure(cfg)
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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m uvmls", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["graph", "topology", "configdb", "lint", "stats"])
    ap.add_argument("dir", nargs="?", default=".", help="workspace directory (default: .)")
    ap.add_argument("-f", dest="filelist", action="append", default=[], help="filelist (.f)")
    ap.add_argument("-I", "--incdir", action="append", default=[], help="include directory")
    ap.add_argument("-D", "--define", action="append", default=[], help="macro define NAME[=VALUE]")
    ap.add_argument("--uvm-home", help="UVM installation (defaults to $UVM_HOME)")
    ap.add_argument("--no-bundled-uvm", action="store_true",
                    help="use the built-in UVM model instead of the bundled UVM 1.2")
    ap.add_argument("--test", help="test class for topology")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    ap.add_argument("-o", "--output", help="write JSON to this file")
    args = ap.parse_args(argv)
    ws = _workspace(args)

    if args.command == "stats":
        data = ws.stats()
    elif args.command == "graph":
        data = class_graph(ws)
    elif args.command == "topology":
        data = topology(ws, args.test)
    elif args.command == "configdb":
        data = config_db_report(ws)
    else:
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
        if args.json:
            print(json.dumps([{"file": d.range.path, "line": d.range.line + 1, "col": d.range.col + 1,
                               "severity": d.severity, "code": d.code, "message": d.message}
                              for d in uniq], indent=2))
        else:
            for d in uniq:
                rel = os.path.relpath(d.range.path)
                print(f"{rel}:{d.range.line + 1}:{d.range.col + 1}: {d.severity}: {d.message} [{d.code}]")
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
