"""Turn a corpus run's summary.json (durable corpus tooling, run_baseline.sh) into the Markdown tables
the README's "Measured on real projects" section and docs/evidence.md show.

    python tools/corpus_evidence.py SUMMARY.json [--full]

The compact table (default) lists the filelist cases: what was indexed, how long it took, what lint
and elaboration reported, and how big the topology and the class graph are. --full adds the
discovery-mode cases and the lint findings by code."""
from __future__ import annotations

import json
import sys


def row(c: dict) -> str:
    elab = "skipped" if c.get("elab_failed") or "elab_errors" not in c else f"{c['elab_errors']} / {c['elab_warnings']}"
    topo = f"{c['topo_nodes']} nodes, depth {c['topo_depth']}, {c['topo_connections']} connections" if c["topo_nodes"] else "—"
    return (f"| {c['name']} | {c['files']} | {c['classes']} | {c['index_s']:.2f} s | {c['stats_rss_mb']} MB | "
            f"{c['lint_total']} | {elab}{'' if elab == 'skipped' else f' ({c.get('elab_s', 0):.2f} s)'} | {topo} | {c['graph_nodes']} / {c['graph_edges']} |")


HEAD = ("| Case | Files | Classes | Index | RSS | Lint findings | Elab errors / warnings | Topology | Graph nodes / edges |\n"
        "|---|---|---|---|---|---|---|---|---|")


def main(argv: list[str]) -> None:
    full = "--full" in argv
    cases = json.load(open([a for a in argv if not a.startswith("--")][0]))
    crashes = [c["name"] for c in cases if c.get("crashes")]
    print(HEAD)
    for c in cases:
        if full or c["mode"] == "filelist":
            print(row(c))
    print()
    print(f"Crashes, tracebacks or timeouts: {', '.join(crashes) if crashes else 'none'}.")
    if full:
        print("\n| Case | Lint findings by code |\n|---|---|")
        for c in cases:
            codes = ", ".join(f"{k} {v}" for k, v in sorted(c["lint_by_code"].items(), key=lambda kv: -kv[1]))
            print(f"| {c['name']} | {codes or '—'} |")


if __name__ == "__main__":
    main(sys.argv[1:])
