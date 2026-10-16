"""The code parses on Python 3.11, the oldest version pyproject.toml declares. On 3.11 the
interpreter itself is the check; on newer ones these tests catch the newer syntax that slips in most
easily. Running the whole suite on 3.11 is the full check (README, Tests)."""

import ast
import io
import os
import sys
import tokenize
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
SOURCES = sorted(p for d in ("uvmls", "tests", "tools") for p in (SERVER / d).rglob("*.py"))
SOURCES += sorted((SERVER.parent / "tools").rglob("*.py"))
OLDEST = (3, 11)


def newer_syntax(source: str) -> list[str]:
    """What in a module Python 3.11 cannot parse, as "line N: what"."""
    try:
        ast.parse(source, feature_version=OLDEST)
    except SyntaxError as e:
        return [f"line {e.lineno}: {e.msg}"]
    if sys.version_info < (3, 12):
        return []
    return [f"line {line}: {what}" for line, what in sorted(_pep701(source))]


def _pep701(source: str) -> set[tuple[int, str]]:
    """Where an f-string's replacement field has what only Python 3.12 allows there (PEP 701), and
    ast.parse(feature_version=...) does not check: a backslash, a comment, a line break in a
    single-quoted f-string, or the quote of an f-string around it (which used to end that f-string)."""
    found = set()
    fstrings = []  # the f-strings open at a token, outermost first: [quote, open braces]
    prev = None
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.FSTRING_START:
            around = fstrings
        elif tok.type in (tokenize.FSTRING_MIDDLE, tokenize.FSTRING_END):
            around = fstrings[:-1]  # its own text, or its own format spec
        else:
            around = [f for f in fstrings if f[1]]
        if around:
            line = tok.start[0]
            single = any(len(quote) == 1 for quote, _ in around)
            if "\\" in tok.string or (tok.start[0] > prev.end[0] and prev.type != tokenize.NL):
                found.add((line, "a backslash in an f-string expression"))
            if tok.type == tokenize.COMMENT:
                found.add((line, "a comment in an f-string expression"))
            if single and (tok.start[0] > prev.end[0] or tok.end[0] > tok.start[0]):
                found.add((line, "a line break in a single-quoted f-string expression"))
            if any(quote in tok.string for quote, _ in around):
                found.add((line, "the quote of the f-string around it"))
        if tok.type == tokenize.FSTRING_START:
            fstrings.append([tok.string.lstrip("rRfF"), 0])
        elif tok.type == tokenize.FSTRING_END:
            fstrings.pop()
        elif fstrings and tok.type == tokenize.OP and tok.string in ("{", "}"):
            fstrings[-1][1] += 1 if tok.string == "{" else -1
        prev = tok
    return found


def test_sources_parse_on_the_oldest_python():
    found = {os.path.relpath(p, SERVER): newer_syntax(p.read_text(encoding="utf-8")) for p in SOURCES}
    assert "uvmls/server.py" in found and {path: what for path, what in found.items() if what} == {}


def test_newer_syntax_is_found():
    for source in ["f\"{re.sub(r'\\s+', '', t)}\"",  # a backslash
                   "f\"{line_no(text, 'a\"')}\"",  # the outer quote
                   "f\"{x!r:>{f\"{w}\"}}\"",  # the outer quote, nested in a format spec
                   "f'''{x  # why\n}'''",  # a comment
                   "f'''{a + \\\nb}'''",  # a line continuation
                   "f'{a\n+ b}'",  # a line break in a single-quoted f-string
                   "type T = int"]:
        assert newer_syntax(source), source
    for source in ["f\"\\n{x}\" + rf'\\d{x}' + f'{{x}} {x:\\\\>5}'",  # escapes outside the expression
                   "f\"{'#'} {x:#x} {d['k']} {f'{x}'} {(lambda y: y)(x)} {x=}\"",
                   "f\"\"\"{\"a\"} {a +\nb}\"\"\"",  # a triple-quoted f-string takes quotes and line breaks
                   "f'ab\\\ncd{x}'"]:  # a line continuation in the text
        assert newer_syntax(source) == [], source
