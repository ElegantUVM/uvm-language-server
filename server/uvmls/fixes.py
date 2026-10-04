"""Quick fixes for UVM lint findings and for some of slang's diagnostics.

Pure: no LSP types, so the language server, the CLI and other front ends can share it. A fix is
computed when it is asked for, from the current text of every file it edits (an open editor's
text, else the file on disk): it checks its finding against the index again, and every place it
edits against that text, and offers nothing when either no longer holds. Edits are code-point
offsets into that text.
"""

from __future__ import annotations

import bisect
import logging
import os
import re
from dataclasses import dataclass, field

from .model import Range
from .project import is_bundled

log = logging.getLogger("uvmls")

EOL_RE = re.compile(r"\r\n|\r|\n")
RAW_EOL_RE = re.compile(rb"\r\n|\r|\n")
MASK_RE = re.compile(r'//[^\r\n]*|/\*.*?(?:\*/|\Z)|"(?P<body>(?:\\.|[^"\\\r\n])*)(?P<close>"?)', re.S)
NOT_EOL_RE = re.compile(r"[^\r\n]")


# -- text ------------------------------------------------------------------------


def mask(text: str) -> str:
    """`text` with comments and the insides of string literals blanked out, newlines kept, so a
    scan for `;`, brackets or keywords sees only code; offsets do not change."""
    def blank(m: re.Match) -> str:
        if m.group(0)[0] == '"':
            return '"' + NOT_EOL_RE.sub(" ", m.group("body")) + m.group("close")
        return NOT_EOL_RE.sub(" ", m.group(0))
    return MASK_RE.sub(blank, text)


def opens_comment(text: str) -> bool:
    """Whether a block comment starts in `text` (one line) and does not end there."""
    return any(m.group(0).startswith("/*") and not (len(m.group(0)) > 3 and m.group(0).endswith("*/"))
               for m in MASK_RE.finditer(text))


def indent_of(line: str) -> str:
    return line[:len(line) - len(line.lstrip(" \t"))]


def indent_unit(ref_indent: str, masked: str) -> str:
    """One level of indentation: a tab where the reference line indents with tabs, else the
    smallest indentation of a line of code (2 to 8 spaces; 2 when no line is indented)."""
    if ref_indent.startswith("\t"):
        return "\t"
    widths = [len(m.group(1)) for m in re.finditer(r"^( +)\S", masked, re.M)]
    return " " * max(2, min(min(widths, default=2), 8))


class FileText:
    """The text of one file as it is now, and where index positions are in it."""

    def __init__(self, path: str, text: str, version: int | None = None, raw: bytes | None = None,
                 bom: int = 0):
        self.path, self.text, self.version = path, text, version
        self._raw = raw  # the bytes on disk; None for editor text (slang parsed its UTF-8 encoding)
        self._raw_lines: list[bytes] | None = None
        self._bom = bom  # slang counts a byte order mark in line 0's columns; editors leave it out
        self.starts, self.ends = [0], []
        for m in EOL_RE.finditer(text):
            self.ends.append(m.start())
            self.starts.append(m.end())
        self.ends.append(len(text))

    @classmethod
    def from_disk(cls, path: str) -> "FileText | None":
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError:
            return None
        bom = 3 if raw.startswith(b"\xef\xbb\xbf") else 0
        return cls(path, raw[bom:].decode("utf-8", "replace"), None, raw[bom:], bom)

    def line(self, n: int) -> str:
        return self.text[self.starts[n]:self.ends[n]]

    def line_at(self, offset: int) -> int:
        return bisect.bisect_right(self.starts, offset) - 1

    def eol(self, n: int | None = None) -> str:
        """The line end of line n; for the last line (or no line), the file's first, else LF."""
        if n is not None and n + 1 < len(self.starts):
            return self.text[self.ends[n]:self.starts[n + 1]]
        m = EOL_RE.search(self.text)
        return m.group(0) if m else "\n"

    def _raw_line(self, n: int) -> bytes:
        if self._raw is None:
            return self.line(n).encode("utf-8", "surrogatepass")
        if self._raw_lines is None:
            self._raw_lines = RAW_EOL_RE.split(self._raw)
        return self._raw_lines[n] if n < len(self._raw_lines) else b""

    def offset(self, line: int, col: int) -> int | None:
        """Where an index position (line, column in UTF-8 bytes, as slang counts) is in the text;
        None when the text has no such position."""
        if not 0 <= line < len(self.starts):
            return None
        if line == 0:
            col -= self._bom
        raw = self._raw_line(line)
        if not 0 <= col <= len(raw):
            return None
        return self.starts[line] + len(raw[:col].decode("utf-8", "replace"))

    def span(self, r: Range) -> tuple[int, int] | None:
        start, end = self.offset(r.line, r.col), self.offset(r.end_line, r.end_col)
        return None if start is None or end is None or end < start else (start, end)

    def position(self, offset: int) -> tuple[int, int]:
        """(line, column in code points) of an offset."""
        line = self.line_at(offset)
        return line, offset - self.starts[line]


class FileTexts:
    """Current file texts for one request: open editors' (with their version), else the disk's."""

    def __init__(self, open_docs: dict[str, tuple[str, int | None]] | None = None):
        self.open = open_docs or {}
        self._cache: dict[str, FileText | None] = {}

    def get(self, path: str) -> FileText | None:
        if path not in self._cache:
            doc = self.open.get(path)
            self._cache[path] = FileText(path, doc[0], doc[1]) if doc else FileText.from_disk(path)
        return self._cache[path]


# -- fixes -----------------------------------------------------------------------


@dataclass(frozen=True)
class TextChange:
    path: str
    start: int  # code-point offsets into the file's current text
    end: int
    text: str


@dataclass
class Fix:
    title: str
    changes: list[TextChange] = field(default_factory=list)
    preferred: bool = False  # the one edit that resolves the finding: what Auto Fix applies
    safe: bool = False  # one possible edit, in the finding's own file, harmless: fix-all may apply it
    command: tuple[str, list] | None = None  # (command, arguments) the editor runs instead
    disabled: str | None = None  # why it cannot be applied


@dataclass
class Client:
    """What the editor offers: workspace folders, greyed-out actions, commands it can run."""

    roots: list[str] = field(default_factory=list)
    disabled_support: bool = False
    commands: frozenset[str] = frozenset()


@dataclass
class Context:
    ws: object  # Workspace
    files: FileTexts
    client: Client
    path: str  # the file the fixes are asked for

    def not_editable(self, path: str) -> str | None:
        """Why a fix may not edit a file, or None. The library is never edited, nor a file outside
        the workspace folders (other than the one asked about)."""
        if path in self.ws.library_files or is_bundled(path):
            return f"{os.path.basename(path)} is part of the UVM library"
        if path != self.path and not any(path.startswith(os.path.join(r, "")) for r in self.client.roots):
            return f"{path} is outside the workspace"
        return None


def insert_member(ft: FileText, cls, item: str) -> TextChange | None:
    """An edit that makes `item` (one line, without its line end) the first item of a class body,
    right after the header: on the next line at the members' indentation, with a blank line before
    the next member; or on the header's own line when code (or an unclosed block comment) follows
    it there."""
    if cls.header_end is None or cls.end_kw is None:
        return None
    semi, cls_span = ft.offset(cls.header_end.line, cls.header_end.col), ft.span(cls.decl_range)
    if semi is None or cls_span is None or ft.text[semi:semi + 1] != ";":
        return None
    line = ft.line_at(semi)
    rest = ft.text[semi + 1:ft.ends[line]]
    if mask(rest).strip() or opens_comment(rest):
        return TextChange(ft.path, semi + 1, semi + 1, " " + item)
    if line + 1 >= len(ft.starts):
        return None
    end_line = ft.line_at(cls_span[1])
    cls_indent = indent_of(ft.line(ft.line_at(cls_span[0])))
    indent = None
    for n in range(line + 1, end_line):
        if ft.line(n).strip():
            ind = indent_of(ft.line(n))
            indent = ind if len(ind) > len(cls_indent) else None
            break
    if indent is None:
        indent = cls_indent + indent_unit(cls_indent, mask(ft.text[cls_span[0]:cls_span[1]]))
    nxt = ft.line(line + 1)
    eol = ft.eol(line + 1)
    gap = eol if nxt.strip() and not mask(nxt).strip().startswith("endclass") else ""
    at = ft.starts[line + 1]
    return TextChange(ft.path, at, at, indent + item + eol + gap)
