#!/usr/bin/env python3
"""Resolve a FuseSoC (CAPI=2) core into a simulator filelist, the way `fusesoc run --setup`
would order it: dependencies first, include files as +incdir+ only.

    fusesoc_flist.py --root REPO [--cores DIR ...] --top VLNV [--target sim] [--tool vcs]
                     [--flag NAME ...] [--define N=V ...] [--timescale 1ns/1ps] -o out.f

Only what a filelist needs is modelled: filesets, depend (with `flag ? (...)` expressions),
virtual cores (first provider found wins, prim_generic preferred), targets (the top core uses
--target, every dependency uses `default`) and generated cores already on disk (--cores).
"""

import argparse
import os
import re
import sys

import yaml


def vlnv_name(dep: str) -> str:
    """'>=lowrisc:ip:uart:0.1' -> 'lowrisc:ip:uart'."""
    dep = dep.strip().lstrip("<>=~^!")
    parts = dep.split(":")
    if len(parts) >= 4:
        parts = parts[:3]
    return ":".join(parts)


def load_cores(dirs):
    cores, virtual = {}, {}
    for d in dirs:
        for dp, dns, fns in os.walk(d):
            dns[:] = [x for x in dns if x not in (".git", "node_modules")]
            for fn in fns:
                if not fn.endswith(".core"):
                    continue
                path = os.path.join(dp, fn)
                try:
                    with open(path) as f:
                        text = f.read()
                    if not text.startswith("CAPI=2"):
                        continue
                    data = yaml.safe_load(text)
                except Exception as e:  # malformed or template cores
                    print(f"skip {path}: {e}", file=sys.stderr)
                    continue
                if not isinstance(data, dict) or "name" not in data:
                    continue
                name = vlnv_name(data["name"])
                data["_path"] = path
                cores.setdefault(name, data)
                for v in data.get("virtual") or []:
                    virtual.setdefault(vlnv_name(v), []).append(name)
    return cores, virtual


def eval_list(items, flags):
    """Expand FuseSoC `flag ? (a b)` / `!flag ? (a)` expressions in a list of strings."""
    out = []
    for item in items or []:
        out += eval_expr(str(item), flags)
    return out


def eval_expr(text, flags):
    toks = re.findall(r"!?[\w:.\-<>=~^/]+\s*\?|\(|\)|[^\s()]+", text)
    pos = 0

    def parse(until_close):
        nonlocal pos
        res = []
        while pos < len(toks):
            t = toks[pos]
            pos += 1
            if t == ")":
                if until_close:
                    return res
                continue
            if t.endswith("?"):
                cond = t[:-1].strip()
                neg = cond.startswith("!")
                cond = cond.lstrip("!")
                val = bool(flags.get(cond, False))
                if pos < len(toks) and toks[pos] == "(":
                    pos += 1
                    inner = parse(True)
                else:
                    inner = [toks[pos]]
                    pos += 1
                if val != neg:
                    res += inner
                continue
            if t == "(":
                res += parse(True)
                continue
            res.append(t)
        return res
    return parse(False)


class Resolver:
    def __init__(self, cores, virtual, flags):
        self.cores, self.virtual, self.flags = cores, virtual, flags
        self.done, self.files, self.incdirs, self.missing = set(), [], [], []

    def pick(self, name):
        if name in self.cores:
            return name
        cands = self.virtual.get(name, [])
        for c in cands:
            if "generic" in c:
                return c
        return cands[0] if cands else None

    def visit(self, name, toplevel=False, target=None):
        real = self.pick(name)
        if real is None:
            if name not in self.missing:
                self.missing.append(name)
            return
        if real in self.done:
            return
        self.done.add(real)
        core = self.cores[real]
        base = os.path.dirname(core["_path"])
        targets = core.get("targets") or {}
        tname = target if toplevel else "default"
        tgt = targets.get(tname) or (targets.get("default") if toplevel else None) or {}
        filesets = core.get("filesets") or {}
        fs_names = eval_list(tgt.get("filesets"), self.flags)
        # Generated cores (ralgen) go in front of the core that generates them.
        for g in eval_list(tgt.get("generate"), self.flags):
            gen = (core.get("generate") or {}).get(g) or {}
            if gen.get("generator") == "ralgen":
                self.visit(f"lowrisc:dv:{gen['parameters']['name']}_ral_pkg")
        # Dependencies of every fileset of the target, in order, then the files.
        for fsn in fs_names:
            fs = filesets.get(fsn) or {}
            for dep in eval_list(fs.get("depend"), self.flags):
                self.visit(vlnv_name(dep))
        for fsn in fs_names:
            fs = filesets.get(fsn) or {}
            ftype = fs.get("file_type", "")
            for entry in fs.get("files") or []:
                attrs = {}
                if isinstance(entry, dict):
                    (path, attrs), = entry.items()
                    attrs = attrs or {}
                else:
                    path = str(entry)
                ft = attrs.get("file_type", ftype)
                if not ft.startswith(("systemVerilogSource", "verilogSource")):
                    continue
                full = os.path.normpath(os.path.join(base, path))
                if attrs.get("is_include_file"):
                    inc = os.path.normpath(os.path.join(base, attrs.get("include_path", os.path.dirname(path))))
                    if inc not in self.incdirs:
                        self.incdirs.append(inc)
                elif full not in self.files:
                    self.files.append(full)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", required=True)
    ap.add_argument("--cores", action="append", default=[])
    ap.add_argument("--top", required=True)
    ap.add_argument("--target", default="sim")
    ap.add_argument("--tool", default="vcs")
    ap.add_argument("--flag", action="append", default=[])
    ap.add_argument("--define", action="append", default=[])
    ap.add_argument("--timescale")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args()
    cores, virtual = load_cores(a.root + a.cores)
    flags = {f"tool_{a.tool}": True, f"target_{a.target}": True}
    for f in a.flag:
        flags[f] = True
    r = Resolver(cores, virtual, flags)
    r.visit(vlnv_name(a.top), toplevel=True, target=a.target)
    with open(a.output, "w") as f:
        f.write(f"// Generated by fusesoc_flist.py from {a.top} (target {a.target}, tool {a.tool})\n")
        if a.timescale:
            f.write(f"-timescale={a.timescale}\n")
        for d in a.define:
            f.write(f"+define+{d}\n")
        rel = lambda p: os.path.relpath(p, os.path.dirname(os.path.abspath(a.output)))
        for d in r.incdirs:
            f.write(f"+incdir+{rel(d)}\n")
        for p in r.files:
            f.write(rel(p) + "\n")
    print(f"{a.output}: {len(r.files)} files, {len(r.incdirs)} incdirs, {len(r.done)} cores", file=sys.stderr)
    for m in r.missing:
        print(f"missing core: {m}", file=sys.stderr)


if __name__ == "__main__":
    main()
