"""Merge uvmls logs from one workspace into a single SARIF run, without dependencies."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path


def merge_logs(logs: list[dict]) -> dict:
    merged = copy.deepcopy(logs[0])
    run = merged["runs"][0]
    run["results"] = []
    rules = {}
    severity = {"note": 0, "warning": 1, "error": 2}
    for log in logs:
        if log["version"] != "2.1.0" or len(log["runs"]) != 1:
            raise ValueError("expected one SARIF 2.1.0 run per log")
        source = log["runs"][0]
        if source["originalUriBaseIds"] != run["originalUriBaseIds"]:
            raise ValueError("logs must use the same workspace")
        driver = source["tool"]["driver"]
        if (driver["name"], driver["version"]) != (run["tool"]["driver"]["name"], run["tool"]["driver"]["version"]):
            raise ValueError("logs must use the same tool and version")
        for rule in driver["rules"]:
            code = rule["id"]
            if code not in rules:
                rules[code] = copy.deepcopy(rule)
            else:
                existing = rules[code]
                existing["defaultConfiguration"]["level"] = max(
                    existing["defaultConfiguration"]["level"], rule["defaultConfiguration"]["level"], key=severity.get)
                existing["properties"]["tags"] = sorted(set(existing["properties"]["tags"] + rule["properties"]["tags"]))
        run["results"].extend(copy.deepcopy(source["results"]))
    ordered = sorted(rules)
    indices = {code: i for i, code in enumerate(ordered)}
    run["tool"]["driver"]["rules"] = [rules[code] for code in ordered]
    for result in run["results"]:
        result["ruleIndex"] = indices[result["ruleId"]]
    return merged


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    args = parser.parse_args()
    merged = merge_logs([json.loads(path.read_text(encoding="utf-8")) for path in args.logs])
    args.output.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"problems={len(merged['runs'][0]['results'])}")


if __name__ == "__main__":
    main()
