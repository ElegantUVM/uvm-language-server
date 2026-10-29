#!/usr/bin/env python3
"""Summarize one run_baseline.sh result directory; optionally compare with an earlier summary.

    summarize.py RESULTS_DIR CASES_FILE [--compare OLD_SUMMARY.json] [--write summary.json]
"""

import argparse
import collections
import json
import os
import re
import sys


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def timing(path):
    """'/usr/bin/time -f "%e %M %x"' output: seconds, peak RSS MB, exit status."""
    text = read(path).strip().splitlines()
    for line in reversed(text):
        m = re.match(r"^([\d.]+) (\d+) (-?\d+)$", line.strip())
        if m:
            return float(m.group(1)), round(int(m.group(2)) / 1024), int(m.group(3))
    return None, None, None


def crashed(err_text):
    return "Traceback (most recent call last)" in err_text


def count_tree(node):
    if not node:
        return 0, 0
    n, depth = 1, 0
    for c in node.get("children", []):
        cn, cd = count_tree(c)
        n += cn
        depth = max(depth, cd)
    return n, depth + 1


def case_summary(res, name, mode):
    p = lambda cmd, ext: os.path.join(res, f"{name}.{cmd}.{ext}")
    s = {"name": name, "mode": mode}
    stats = load_json(p("stats", "json")) or {}
    secs, rss, rc = timing(p("stats", "time"))
    s.update(files=stats.get("files"), classes=stats.get("classes"), index_s=stats.get("seconds"),
             stats_wall_s=secs, stats_rss_mb=rss, stats_exit=rc)
    lint = load_json(p("lint", "json"))
    lsecs, lrss, lrc = timing(p("lint", "time"))
    s["lint_exit"] = lrc
    s["lint_rss_mb"] = lrss
    if isinstance(lint, list):
        s["lint_total"] = len(lint)
        s["lint_by_code"] = dict(collections.Counter(d["code"] or "?" for d in lint).most_common())
    elab = load_json(p("elab", "json"))
    esecs, erss, erc = timing(p("elab", "time"))
    if elab is not None:
        c = elab.get("counts", {})
        s.update(elab_s=elab.get("seconds"), elab_rss_mb=erss, elab_errors=c.get("errors"),
                 elab_warnings=c.get("warnings"), elab_failed=elab.get("error"),
                 elab_tops=len(elab.get("tops", [])),
                 elab_errors_by_code=dict(collections.Counter(
                     d["code"] for d in elab.get("diagnostics", []) if d["severity"] == "error").most_common()))
    else:
        skip = re.search(r"elaboration skipped: (.*)", read(p("elab", "err")))
        s["elab_skipped"] = skip.group(1)[:160] if skip else None
    topo = load_json(p("topology", "json"))
    if isinstance(topo, dict):
        n, depth = count_tree(topo.get("root"))
        s.update(topo_test=topo.get("test"), topo_tests=len(topo.get("tests", [])), topo_nodes=n,
                 topo_depth=depth, topo_connections=len(topo.get("connections", [])))
    graph = load_json(p("graph", "json"))
    if isinstance(graph, dict):
        s["graph_nodes"] = len(graph.get("nodes") or [])
        s["graph_edges"] = len(graph.get("edges") or [])
    s["crashes"] = [cmd for cmd in ("stats", "lint", "elab", "topology", "graph")
                    if crashed(read(p(cmd, "err")))]
    warns = [line for line in read(p("stats", "err")).splitlines() if line.startswith("warning:")]
    s["setup_warnings"] = len(warns)
    return s


def fmt(v):
    return "-" if v is None else str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("cases")
    ap.add_argument("--compare")
    ap.add_argument("--write")
    ap.add_argument("names", nargs="*", help="only these cases")
    a = ap.parse_args()
    rows = []
    for line in read(a.cases).splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, mode = line.split("|")[:2]
        if a.names and name not in a.names:
            continue
        rows.append(case_summary(a.results, name, mode))
    hdr = ("case", "mode", "files", "cls", "idx_s", "rssMB", "lint", "elab_s", "elab_E", "elab_W",
           "tests", "topo_n", "crash")
    out = [hdr]
    for s in rows:
        elab_e = fmt(s.get("elab_errors")) if "elab_errors" in s else ("skip" if s.get("elab_skipped") else "-")
        out.append((s["name"], s["mode"], fmt(s.get("files")), fmt(s.get("classes")), fmt(s.get("index_s")),
                    fmt(s.get("stats_rss_mb")), fmt(s.get("lint_total")), fmt(s.get("elab_s")), elab_e,
                    fmt(s.get("elab_warnings")), fmt(s.get("topo_tests")), fmt(s.get("topo_nodes")),
                    ",".join(s["crashes"]) or "no"))
    widths = [max(len(r[i]) for r in out) for i in range(len(hdr))]
    for r in out:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    print()
    for s in rows:
        bits = []
        if s.get("lint_by_code"):
            bits.append("lint " + ", ".join(f"{k}={v}" for k, v in s["lint_by_code"].items()))
        if s.get("elab_errors_by_code"):
            bits.append("elab errors " + ", ".join(f"{k}={v}" for k, v in list(s["elab_errors_by_code"].items())[:8]))
        if s.get("elab_skipped"):
            bits.append("elab skipped: " + s["elab_skipped"])
        if s.get("elab_failed"):
            bits.append("elab FAILED: " + s["elab_failed"][:120])
        if bits:
            print(f"{s['name']}: " + "; ".join(bits))
    if a.write:
        with open(a.write, "w") as f:
            json.dump(rows, f, indent=1)
    if a.compare:
        old = {r["name"]: r for r in (load_json(a.compare) or [])}
        print("\nchanges against", a.compare)
        keys = ("files", "classes", "lint_total", "elab_errors", "elab_warnings", "topo_tests", "topo_nodes",
                "crashes", "lint_by_code", "elab_errors_by_code")
        changed = False
        for s in rows:
            o = old.get(s["name"])
            if o is None:
                print(f"  {s['name']}: new case")
                changed = True
                continue
            diffs = [f"{k}: {o.get(k)} -> {s.get(k)}" for k in keys if o.get(k) != s.get(k)]
            for k in ("index_s", "elab_s", "stats_rss_mb"):
                ov, nv = o.get(k), s.get(k)
                if ov and nv and (nv > ov * 1.5 + 0.5 or nv < ov / 1.5 - 0.5):
                    diffs.append(f"{k}: {ov} -> {nv}")
            if diffs:
                changed = True
                print(f"  {s['name']}:\n    " + "\n    ".join(diffs))
        if not changed:
            print("  none")


if __name__ == "__main__":
    sys.exit(main())
