"""SARIF 2.1.0 for the CLI's lint and elaboration diagnostics."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import quote

from . import __version__
from .model import Diag, Range, utf8_prefix
from .project import relative_or_absolute


INFORMATION_URI = "https://example.invalid/uvmls"
LEVELS = {"error": "error", "warning": "warning", "info": "note", "hint": "note"}
SEVERITY = {"note": 0, "warning": 1, "error": 2}
DESCRIPTIONS = {
    "create-unregistered": "A factory create targets a UVM class without registration.",
    "utils-mismatch": "Object/component registration disagrees with the class's UVM role.",
    "create-no-parent": "A component factory create omits its parent; an error when the indexed registry "
                        "requires it, otherwise a warning.",
    "create-virtual": "A factory create targets a virtual class.",
    "unknown-base": "A non-UVM base class was not found in the indexed sources (info).",
    "config-db-no-set": "No matching field is set in the indexed sources; optional or conditional gets are info, "
                        "required gets are warnings. Dynamic field names in sets suppress this check.",
    "config-db-type-mismatch": "A get uses a different type from the field's sets. Type names with competing "
                              "declarations are treated as unknown and produce no mismatch warning. Matching "
                              "is static, so runtime scopes and values can remain uncertain.",
    "factory-ctor": "A registered class's constructor cannot accept the calls its UVM factory makes (error). "
                    "The check follows the selected UVM library and defines.",
    "create-new": "Direct `new(name, parent)` of a registered project component bypasses factory overrides (warning).",
    "utils-param": "Name registration collides across used specializations, or a non-parameterized test uses a "
                   "param-utils macro that does not register its name (warning).",
    "phase-no-super": "A nonempty `build_phase` or `connect_phase` override skips work in a project base class (warning).",
}


def _column(lines: list[str], line: int, col: int) -> int:
    if not 0 <= line < len(lines):
        return col + 1
    # slang counts UTF-8 bytes; SARIF consumers use the run's declared character units.
    return len(utf8_prefix(lines[line], col).encode("utf-16-le")) // 2 + 1


def build_log(root: str | Path, diagnostics: list[Diag], fallback: str | None = None) -> dict:
    """`fallback` locates diagnostics without a file (global elaboration failures): code scanning
    rejects results without a location. Pass the filelist that produced them."""
    root = Path(root).resolve()
    rules, results, texts = {}, [], {}
    for d in diagnostics:
        level = LEVELS[d.severity]
        if d.code not in rules:
            rules[d.code] = {"id": d.code,
                             "shortDescription": {"text": DESCRIPTIONS.get(d.code, d.code)},
                             "defaultConfiguration": {"level": level},
                             "properties": {"tags": []}}
        rule = rules[d.code]
        rule["defaultConfiguration"]["level"] = max(rule["defaultConfiguration"]["level"], level, key=SEVERITY.get)
        rule["properties"]["tags"] = sorted(set(rule["properties"]["tags"]) | {d.source})
        result = {"ruleId": d.code, "level": level, "message": {"text": d.message}}
        span = d.range
        # Some elaboration failures are global: point at the filelist's start, not a made-up source line.
        located = span.path or fallback
        if located:
            path = Path(located).resolve()
            relative = Path(relative_or_absolute(path, root)).as_posix()
            if path.is_relative_to(root):
                artifact = {"uri": quote(relative), "uriBaseId": "SRCROOT"}
            else:
                artifact = {"uri": path.as_uri()}
            if path not in texts:
                try:
                    # Text reads normalize CR/LF; other whitespace is not a source line break.
                    texts[path] = path.read_text(encoding="utf-8", errors="replace").split("\n")
                except OSError:
                    texts[path] = []
            lines = texts[path]
            if not span.path:
                span = Range(located, 0, 0, 0, 0)
            region = {"startLine": span.line + 1, "startColumn": _column(lines, span.line, span.col)}
            if (span.end_line, span.end_col) > (span.line, span.col):
                region.update(endLine=span.end_line + 1, endColumn=_column(lines, span.end_line, span.end_col))
            result["locations"] = [{"physicalLocation": {"artifactLocation": artifact, "region": region}}]
            line = lines[span.line] if 0 <= span.line < len(lines) else ""
            identity = [relative, d.code, " ".join(line.split())]
            if not d.range.path:  # every global failure sits on the filelist's first line
                identity.append(d.message)
            identity = json.dumps(identity, ensure_ascii=False)
            result["partialFingerprints"] = {"primaryLocationLineHash": hashlib.sha256(identity.encode("utf-8")).hexdigest()}
        results.append(result)
    ordered = sorted(rules)
    indices = {code: i for i, code in enumerate(ordered)}
    for result in results:
        result["ruleIndex"] = indices[result["ruleId"]]
    return {"$schema": "https://json.schemastore.org/sarif-2.1.0.json", "version": "2.1.0", "runs": [{
        "columnKind": "utf16CodeUnits",
        "tool": {"driver": {"name": "uvmls", "version": __version__, "informationUri": INFORMATION_URI,
                            "rules": [rules[code] for code in ordered]}},
        "originalUriBaseIds": {"SRCROOT": {"uri": root.as_uri().removesuffix("/") + "/"}},
        "results": results,
    }]}


def write_log(destination: str, root: str, diagnostics: list[Diag], fallback: str | None = None) -> None:
    text = json.dumps(build_log(root, diagnostics, fallback), indent=2, ensure_ascii=False) + "\n"
    if destination == "-":
        sys.stdout.write(text)
    else:
        Path(destination).write_text(text, encoding="utf-8")
