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

from .lints import (
    UTILS_RE, body_of, ctor_call_problem, ctor_problem, hides_macro, misregistered, near_misses, new_target,
    param_problem, registration_for, skipped_super, swapped_macro, unknown_macro_lines,
)
from .elab import SOURCE as ELAB_SOURCE
from .indexer import EOL_RE, IGNORE_RE, MASK_RE, NOT_EOL_RE, ignore_codes, ignore_directives, mask
from .model import Range
from .project import discover_files, is_bundled, parse_filelist
from .views import canonical_type, config_db_report, mismatched

log = logging.getLogger("uvmls")

RAW_EOL_RE = re.compile(rb"\r\n|\r|\n")


# -- text ------------------------------------------------------------------------


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
    nav: object = None  # Navigator, to find what a name in the code is

    def not_editable(self, path: str) -> str | None:
        """Why a fix may not edit a file, or None. The library is never edited, nor a file outside
        the workspace folders (other than the one asked about)."""
        if path in self.ws.library_files or is_bundled(path):
            return f"{os.path.basename(path)} is part of the UVM library"
        if path != self.path and not any(path.startswith(os.path.join(r, "")) for r in self.client.roots):
            return f"{path} is outside the workspace"
        return None


def member_indent(ft: FileText, cls_span: tuple[int, int], header_line: int) -> tuple[str, str]:
    """(indentation of a class's members, one indentation unit): that of the first line with text
    after the header, when it is deeper than the class's; else the class's plus one unit."""
    end_line = ft.line_at(cls_span[1])
    cls_indent = indent_of(ft.line(ft.line_at(cls_span[0])))
    indent = None
    for n in range(header_line + 1, end_line):
        if ft.line(n).strip():
            ind = indent_of(ft.line(n))
            indent = ind if len(ind) > len(cls_indent) else None
            break
    unit = indent_unit(indent or cls_indent, mask(ft.text[cls_span[0]:cls_span[1]]))
    return indent or cls_indent + unit, unit


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
    indent, _ = member_indent(ft, cls_span, line)
    nxt = ft.line(line + 1)
    eol = ft.eol(line + 1)
    gap = eol if nxt.strip() and not mask(nxt).strip().startswith("endclass") else ""
    at = ft.starts[line + 1]
    return TextChange(ft.path, at, at, indent + item + eol + gap)


def call_args(masked: str) -> tuple[int, list[tuple[int, int]]] | None:
    """For the masked text of a call that ends with `)`: the offset of its `(` and the span of each
    argument (commas inside brackets do not split)."""
    close = len(masked.rstrip()) - 1
    if close < 0 or masked[close] != ")":
        return None
    depth, start = 0, None
    for i in range(close, -1, -1):
        c = masked[i]
        if c in ")]}":
            depth += 1
        elif c in "([{":
            depth -= 1
            if depth == 0:
                start = i
                break
    if start is None or masked[start] != "(":
        return None
    args, begin, depth = [], start + 1, 0
    for i in range(start + 1, close):
        c = masked[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            args.append((begin, i))
            begin = i + 1
    if args or masked[begin:close].strip():
        args.append((begin, close))
    return start, args


IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
UTILS_END_RE = re.compile(r"`(uvm_(?:object|component)(?:_abstract)?_utils_end)\b")


def macro_defined(ws, name: str) -> bool:
    """Whether the UVM in use (or the project) defines a macro."""
    if name in ws.macros_by_name:
        return True
    from .navigation import shim_macros
    return not (ws.sources and ws.sources.has_real_uvm_macros) and name in shim_macros()


def sees_uvm_pkg(ws, cls) -> bool:
    """Whether the UVM macros (which name uvm_*_registry unqualified) compile in a class: it
    imports uvm_pkg, or another class of its package (of its file, outside one) is registered
    through them."""
    if "uvm_pkg" in cls.imports:
        return True
    return any(c is not cls and not c.library and c.registration_macro and UTILS_RE.match(c.registration_macro)
               and (c.package == cls.package if cls.package else c.path == cls.path) for c in ws.classes)


def method_of(ws, cls, name: str):
    """A class's own method by name: its prototype when the body is out of the class."""
    return next((m for m in cls.methods if m.name == name), None) or ws.impls.get((cls.qualname, name))


def _create_no_parent(ctx: Context, d) -> list[Fix]:
    """`T::type_id::create("x")` of a component: pass `this` (a component's method) or null."""
    ws, data = ctx.ws, d.data or {}
    cls = ws.class_by_qual.get(data.get("cls"))
    at = list(data.get("at") or ())
    cr = cls and next((c for c in ws.calls_of(cls).creates if c.kind == "factory"
                       and c.range.path == d.range.path and [c.range.line, c.range.col] == at), None)
    if cr is None or cr.parent_expr is not None:
        return []
    ft = ctx.files.get(cr.range.path)
    span = ft and ft.span(cr.range)
    if span is None:
        return []
    masked = mask(ft.text[span[0]:span[1]])
    parsed = call_args(masked)
    if parsed is None or "type_id::create" not in masked or len(parsed[1]) != cr.nargs:
        return []
    meth = method_of(ws, cls, cr.method)
    this = ws.is_component(cls) and not (meth and "static" in meth.qualifiers)
    parent = "this" if this else "null"
    said = "'this'" if this else "null"
    start, args = parsed
    if len(args) == 1:
        arg = masked[args[0][0]:args[0][1]]
        named = re.match(r"\s*\.\s*([A-Za-z_][A-Za-z0-9_$]*)", arg)
        if arg.lstrip().startswith(".") and (named is None or named.group(1) != "name"):
            return []
        pos = span[0] + args[0][0] + len(arg.rstrip())
        text = f", .parent({parent})" if named else f", {parent}"
        fix = Fix(f"Pass {said} as the parent", [TextChange(ft.path, pos, pos, text)])
    elif not args and cr.target and IDENT_RE.fullmatch(cr.target):
        pos = span[0] + start + 1
        fix = Fix(f'Pass "{cr.target}" as the name and {said} as the parent',
                  [TextChange(ft.path, pos, pos, f'"{cr.target}", {parent}')])
    else:
        return []
    # A component's create cannot compile without a parent: `this` is the one fix, and harmless.
    fix.preferred = fix.safe = this
    return [fix]


def _create_unregistered(ctx: Context, d) -> list[Fix]:
    """`T::type_id::create(...)` of a class T that is not registered: add the utils macro to T."""
    ws = ctx.ws
    t = ws.class_by_qual.get((d.data or {}).get("target"))
    if t is None or t.library or t.is_virtual or t.registration is not None:
        return []
    if ws.role(t) in ("class", "interface"):  # not known to be a uvm_object
        return []
    if hides_macro(unknown_macro_lines(ws), t):
        return []
    macro, arg = registration_for(ws, t)
    if not macro_defined(ws, macro) or not sees_uvm_pkg(ws, t):
        return []
    item = f"`{macro}({arg})"
    title = f"Add {item} to class '{t.name}'" + ("" if t.path == d.range.path else f" ({os.path.basename(t.path)})")
    outside = ctx.not_editable(t.path)
    if outside:
        return [Fix(title, disabled=f"'{t.name}' is declared outside the workspace ({t.path})")]
    ft = ctx.files.get(t.path)
    span = ft and ft.span(t.decl_range)
    # A class with field macros needs a _begin/_end block around them: which, is the author's call.
    if span is None or "`uvm_field_" in mask(ft.text[span[0]:span[1]]):
        return []
    change = insert_member(ft, t, item)
    return [Fix(title, [change], preferred=True, safe=t.path == ctx.path)] if change else []


def masked_body(ws, ft: FileText, cls) -> tuple[int, str] | None:
    """(offset, masked text) of a class declaration, with its nested classes blanked out too."""
    span = ft.span(cls.decl_range)
    if span is None:
        return None
    masked = mask(ft.text[span[0]:span[1]])
    for inner in ws.classes:
        if inner.outer_class == cls.name and inner.package == cls.package and inner.path == cls.path:
            sub = ft.span(inner.decl_range)
            if sub and span[0] <= sub[0] <= sub[1] <= span[1]:
                a, b = sub[0] - span[0], sub[1] - span[0]
                masked = masked[:a] + NOT_EOL_RE.sub(" ", masked[a:b]) + masked[b:]
    return span[0], masked


def creates_of(ws, cls) -> list:
    """Every factory create() of a class, anywhere in the user's code."""
    out = []
    for c in ws.classes:
        if c.library:
            continue
        for cr in ws.calls_of(c).creates:
            if cr.kind != "factory" or cr.type is None or \
                    (cr.type.name != cls.name and cr.type.name not in ws.typedefs_by_name):
                continue
            spec = ws.resolve_spec(cr.type.name, cr.type.scope, cr.type.args, c)
            if spec is not None and spec.cls is cls:
                out.append(cr)
    return out


def _utils_mismatch(ctx: Context, d) -> list[Fix]:
    """A component registered with `uvm_object_utils, or the reverse: swap object and component in the
    macro's name (and in the matching _end)."""
    ws = ctx.ws
    cls = ws.class_by_qual.get((d.data or {}).get("cls"))
    kind = cls and not cls.library and misregistered(ws, cls)
    old = cls.registration_macro if kind else None
    new = swapped_macro(old, kind) if old else None
    if not new or cls.registration_range is None or not macro_defined(ws, new):
        return []
    ft = ctx.files.get(cls.registration_range.path)
    span = ft and ft.span(cls.registration_range)
    if span is None or not ft.text.startswith("`" + old, span[0]):
        return []
    changes = [TextChange(ft.path, span[0] + 1, span[0] + 1 + len(old), new)]
    title = f"Change `{old} to `{new}"
    if old.endswith("_begin"):
        body = masked_body(ws, ft, cls)
        m = body and UTILS_END_RE.search(body[1], span[1] - body[0])
        end_new = f"uvm_{kind}_utils_end"
        if not m or not macro_defined(ws, end_new):
            return []
        if m.group(1) != end_new:
            changes.append(TextChange(ft.path, body[0] + m.start(1), body[0] + m.end(1), end_new))
            title += f" and `{m.group(1)} to `{end_new}"
    # As a component, its create() needs a parent: a create() without one stops compiling.
    safe = kind == "object" or all(cr.nargs >= 2 for cr in creates_of(ws, cls))
    return [Fix(title, changes, preferred=True, safe=safe)]


def _factory_ctor(ctx: Context, d) -> list[Fix]:
    """A registered class the factory cannot construct: add the constructor it calls, or give an
    object's name argument the default that new() needs."""
    ws = ctx.ws
    cls = ws.class_by_qual.get((d.data or {}).get("cls"))
    problem = cls and not cls.library and ctor_problem(ws, cls)
    if not problem or problem[0] == "other":
        return []
    ft = ctx.files.get(cls.path)
    if ft is None:
        return []
    if problem[0] == "name-default":
        ctor = next(m for m in cls.methods if m.name == "new")
        arg = ctor.args[0]
        span = None if ctor.is_prototype or ctor.generated else ft.span(arg.range)
        if span is None or ft.text[span[0]:span[1]] != arg.name:
            return []
        return [Fix(f"Give '{arg.name}' a default value: \"{cls.name}\"",
                    [TextChange(ft.path, span[1], span[1], f' = "{cls.name}"')], preferred=True, safe=True)]
    return _add_constructor(ctx, ft, cls)


def _add_constructor(ctx: Context, ft: FileText, cls) -> list[Fix]:
    """The constructor the factory calls, right after the registration block."""
    ws = ctx.ws
    component = cls.registration == "component"
    base = ws.base_spec(cls)
    if cls.extends_args is not None or cls.registration_range is None or base is None:
        return []
    if base.cls is not None and not base.cls.library:  # it must take super.new(...) too
        bctor = next((m for m in base.cls.methods if m.name == "new"), None)
        if bctor is None or ctor_call_problem(ws, base.cls, bctor.args, 2 if component else 1):
            return []
    body, reg = masked_body(ws, ft, cls), ft.span(cls.registration_range)
    if body is None or reg is None or cls.header_end is None:
        return []
    end = reg[1]
    if cls.registration_macro.endswith("_begin"):
        m = UTILS_END_RE.search(body[1], reg[1] - body[0])
        if not m:
            return []
        end = body[0] + m.end()
    line = ft.line_at(end)
    if re.search(r"\bendclass\b", mask(ft.text[end:ft.ends[line]])) or line + 1 >= len(ft.starts):
        return []
    indent, unit = member_indent(ft, (body[0], body[0] + len(body[1])), cls.header_end.line)
    if component:
        title, lines = "Add a constructor: new(string name, uvm_component parent)", [
            "function new(string name, uvm_component parent);", f"{unit}super.new(name, parent);", "endfunction"]
    else:
        title, lines = f'Add a constructor: new(string name = "{cls.name}")', [
            f'function new(string name = "{cls.name}");', f"{unit}super.new(name);", "endfunction"]
    if ft.line(line + 1).strip():
        at, before = line + 1, ft.eol(line + 1)
    elif line + 2 < len(ft.starts):
        at, before = line + 2, ""
    else:
        return []
    eol = ft.eol(at)
    nxt = ft.line(at)
    after = eol if nxt.strip() and not mask(nxt).strip().startswith("endclass") else ""
    text = before + "".join(indent + ln + eol for ln in lines) + after
    return [Fix(title, [TextChange(ft.path, ft.starts[at], ft.starts[at], text)], preferred=True, safe=True)]


ASSIGN_RE = re.compile(r"(?<![=!<>])=(?!=)")


def _create_new(ctx: Context, d) -> list[Fix]:
    """`x = new(name, parent)` of a registered component: create it through the factory instead."""
    ws, data = ctx.ws, d.data or {}
    cls = ws.class_by_qual.get(data.get("cls"))
    at = list(data.get("at") or ())
    cr = cls and next((c for c in ws.calls_of(cls).creates if c.kind == "new"
                       and c.range.path == d.range.path and [c.range.line, c.range.col] == at), None)
    made = cr and new_target(ws, cls, cr)
    ft = ctx.files.get(cr.range.path) if made else None
    span = ft and ft.span(cr.range)
    if span is None:
        return []
    masked = mask(ft.text[span[0]:span[1]])
    eq = ASSIGN_RE.search(masked)
    news = list(re.finditer(r"\bnew\b", masked[eq.end():])) if eq else []
    if len(news) != 1:
        return []
    parsed = call_args(masked)
    if parsed is None or len(parsed[1]) != 2:
        return []
    for a, b in parsed[1]:
        arg = masked[a:b].lstrip()
        if arg.startswith(".") and not re.match(r"\.\s*(name|parent)\s*\(", arg):
            return []  # constructor-specific names do not bind to the factory's formals
    start = span[0] + eq.end() + news[0].start()
    scoped = re.search(r"::\s*$", masked[:eq.end() + news[0].start()])
    text = "type_id::create" if scoped else f"{made[1]}::type_id::create"
    if bool(scoped) != (cr.type is not None):
        return []
    # Preferred, not safe: an override of the class applies from now on, which is the point.
    return [Fix(f"Create '{cr.target}' with {made[1]}::type_id::create",
                [TextChange(ft.path, start, start + 3, text)], preferred=True)]


def _utils_param(ctx: Context, d) -> list[Fix]:
    """A parameterized class registered with `uvm_*_utils, or a test with `uvm_*_param_utils: the other
    macro, and for a bare class name the parameters with it."""
    ws = ctx.ws
    cls = ws.class_by_qual.get((d.data or {}).get("cls"))
    wrong = cls and not cls.library and cls.registration_range and param_problem(ws, cls)
    if not wrong or not macro_defined(ws, wrong[0]):
        return []
    old, new = cls.registration_macro, wrong[0]
    ft = ctx.files.get(cls.registration_range.path)
    span = ft and ft.span(cls.registration_range)
    if span is None or not ft.text.startswith("`" + old, span[0]):
        return []
    masked = mask(ft.text[span[0]:span[1]])
    parsed = call_args(masked)
    if parsed is None or len(parsed[1]) != 1:
        return []
    a, b = parsed[1][0]
    arg = ft.text[span[0] + a:span[0] + b].strip()
    changes = [TextChange(ft.path, span[0] + 1, span[0] + 1 + len(old), new)]
    params = [p.name for p in cls.params if not p.is_local]
    if params and arg == cls.name:
        arg = f"{cls.name} #(" + ", ".join(params) + ")"
        lead = len(masked[a:b]) - len(masked[a:b].lstrip())
        changes.append(TextChange(ft.path, span[0] + a + lead, span[0] + a + lead + len(cls.name), arg))
    # Preferred, not safe: which name the class is registered under changes.
    return [Fix(f"Change to `{new}({arg})", changes, preferred=True)]


def _phase_no_super(ctx: Context, d) -> list[Fix]:
    """A phase method that skips its base's: call super's first, or right after the statements that
    set factory overrides or config_db values (the base's build_phase creates what they are for)."""
    ws, data = ctx.ws, d.data or {}
    cls, name = ws.class_by_qual.get(data.get("cls")), data.get("method")
    skipped = cls and skipped_super(ws, cls, name)
    meth = body_of(ws, cls, name) if skipped else None
    if meth is None or skipped[0].qualname != data.get("base") or meth.generated or not meth.args:
        return []
    call = f"super.{name}({meth.args[0].name});"
    calls = ws.calls_of(cls)
    marks = [c.range for c in calls.overrides if c.method == name] + \
        [c.range for c in calls.config_db if c.method == name and c.op == "set"]
    marks = [r for r in marks if r.path == meth.range.path and meth.decl_range.contains(r.line, r.col)]
    if marks:
        last = max(marks, key=lambda r: (r.line, r.col))
        stmt = next((it for it in meth.body_items if it.contains(last.line, last.col)), None)
        title = f"Call {call[:-1]} after the factory overrides"
    else:
        stmt, title = meth.body_items[0], f"Call {call[:-1]} first"
    ft = ctx.files.get(meth.range.path)
    span = ft.span(stmt) if ft and stmt else None
    if span is None:
        return []
    first, last_line = ft.line_at(span[0]), ft.line_at(span[1])
    indent = indent_of(ft.line(first))
    if marks:  # after the statement: on a line of its own when nothing follows it on its line
        rest = ft.text[span[1]:ft.ends[last_line]]
        if not mask(rest).strip() and not opens_comment(rest) and last_line + 1 < len(ft.starts):
            at, text = ft.starts[last_line + 1], indent + call + ft.eol(last_line + 1)
        else:
            at, text = span[1], " " + call
    elif not ft.text[ft.starts[first]:span[0]].strip():  # before it, on a line of its own
        at, text = ft.starts[first], indent + call + ft.eol(first)
    else:
        at, text = span[0], call + " "
    # Preferred, not safe: the base's phase runs from now on, which changes what is built.
    return [Fix(title, [TextChange(ft.path, at, at, text)], preferred=True)]


INTEGRAL_RE = re.compile(r"^(bit|logic|reg|int|integer|byte|shortint|longint|time)"
                         r"(\s+(signed|unsigned))?(\s*\[[^\]]*\])*$")
CHAIN_RE = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")


def _config_db_get(ctx: Context, d):
    """(report, field row, get entry, file text, call span, masked call, argument spans) for the get a
    config_db finding is about, or None when the index or the text no longer has it."""
    data = d.data or {}
    report = config_db_report(ctx.ws)
    row = next((r for r in report["fields"] if r["field"] == data.get("field")), None)
    at = list(data.get("at") or ())
    g = row and next((e for e in row["gets"] if e["file"] == d.range.path and [e["line"], e["col"]] == at), None)
    if g is None:
        return None
    ft = ctx.files.get(g["file"])
    span = ft and ft.span(Range(g["file"], g["line"], g["col"], g["endLine"], g["endCol"]))
    masked = mask(ft.text[span[0]:span[1]]) if span else ""
    parsed = call_args(masked)
    if parsed is None or len(parsed[1]) < 3:
        return None
    return report, row, g, ft, span, masked, parsed[1]


def show_config_db(ctx: Context, field: str) -> list[Fix]:
    command = "uvm.showConfigDb"
    return [Fix(f"Show '{field}' in the config_db cross-reference", command=(command, [{"field": field}]))] \
        if command in ctx.client.commands else []


def _config_db_no_set(ctx: Context, d) -> list[Fix]:
    """A get nothing sets, when a set of a close name looks like a typo: use that name."""
    found = _config_db_get(ctx, d)
    if found is None:
        return []
    report, row, g, ft, span, masked, args = found
    near = near_misses(report, g) if not row["sets"] else []
    a, b = args[2 if g["db"] == "uvm_config_db" else 1]
    literal = ft.text[span[0] + a:span[0] + b]
    lead = len(literal) - len(literal.lstrip())
    fixes = []
    if 0 < len(near) <= 3 and literal.strip() == f'"{row["field"]}"':
        start = span[0] + a + lead + 1
        for name, entry in near:
            fixes.append(Fix(f"Change '{row['field']}' to '{name}' (set in {os.path.basename(entry['file'])}:"
                             f"{entry['line'] + 1})", [TextChange(ft.path, start, start + len(row["field"]), name)],
                             preferred=len(near) == 1))
    return fixes + show_config_db(ctx, row["field"])


def _config_db_type_mismatch(ctx: Context, d) -> list[Fix]:
    """A get of another type than the one the field is set with: get it with that type, when the
    variable it reads into has it (or both are integral types, which convert)."""
    found = _config_db_get(ctx, d)
    if found is None:
        return []
    report, row, g, ft, span, masked, args = found
    fixes = show_config_db(ctx, row["field"])
    if g not in mismatched(row):
        return []
    want, written = row["sets"][0]["canon"], row["sets"][0]["type"]
    a, b = args[3 if g["db"] == "uvm_config_db" else 2]
    var = ft.text[span[0] + a:span[0] + b].strip()
    nav = ctx.nav
    if nav is None:
        from .navigation import Navigator
        nav = Navigator(ctx.ws)
    end = span[0] + a + len(ft.text[span[0] + a:span[0] + b].rstrip()) - 1
    line, col = ft.position(end)
    hits = nav.resolve(ft.path, line, col, ft.text) if CHAIN_RE.match(var) else []
    target = next((t for t in hits if hasattr(t.obj, "type_text")), None)
    if target is None:
        return fixes
    owner = target.owner if hasattr(target.owner, "params") else None
    same = canonical_type(ctx.ws, target.obj.type_text, owner) == want
    if not same and not (INTEGRAL_RE.match(written) and INTEGRAL_RE.match(g["type"])):
        return fixes  # a class handle is an inout argument: another type would not compile
    db = re.search(rf"\b{g['db']}\s*#\s*\(", masked)
    inner = _paren_span(masked, db.end() - 1) if db else None
    if inner is None:
        return fixes
    return [Fix(f"Change to {g['db']} #({written}) (the type '{row['field']}' is set with)",
                [TextChange(ft.path, span[0] + inner[0], span[0] + inner[1], written)], preferred=same)] + fixes


def _paren_span(masked: str, open_at: int) -> tuple[int, int] | None:
    """The span inside the parentheses that open at `open_at`."""
    depth = 0
    for i in range(open_at, len(masked)):
        if masked[i] == "(":
            depth += 1
        elif masked[i] == ")":
            depth -= 1
            if depth == 0:
                return open_at + 1, i
    return None


# -- slang's diagnostics -----------------------------------------------------------


def _typo(ctx: Context, d) -> list[Fix]:
    """A misspelt keyword, name or member that slang suggests another name for: use that name."""
    data = d.data or {}
    wrong, right = data.get("from"), data.get("to")
    ft = ctx.files.get(d.range.path)
    span = ft and ft.span(d.range)
    if not wrong or not right or span is None or ft.text[span[0]:span[1]] != wrong:
        return []  # the text changed, or the name comes from a macro
    # Missing build inputs can make valid names look misspelt, even in another source file.
    if any(x.code in ("CouldNotOpenIncludeFile", "UnknownDirective") for x in ctx.ws.user_diagnostics()):
        return []
    if d.code == "TypoKeyword" and any(
            r.path == ft.path and (r.line, r.col) != (d.range.line, d.range.col)
            for idx in ctx.ws.indices.values() if ft.path in idx.files
            for r in idx.identifiers.get(wrong, ())):
        return []
    if any(c.decl_range.path == ft.path and c.decl_range.contains(d.range.line, d.range.col)
           for c in ctx.ws.classes_by_name.get(right, ())):
        return []
    return [Fix(f"Change '{wrong}' to '{right}'", [TextChange(ft.path, span[0], span[1], right)], preferred=True)]


def include_dirs_for(ws, name: str, near: str) -> list[str]:
    """The directories below the workspace folders that `include "name" would find a file in, and
    that are not on the include path already; those nearest the including file first."""
    name = name.replace("\\", "/")
    if not name or os.path.isabs(name) or ".." in name.split("/"):
        return []
    tail = os.sep + os.path.normpath(name)
    base = os.path.basename(tail)
    files = discover_files(ws.config.workspace_roots, ws.config.exclude, match=lambda fn: fn == base)
    have = set(ws.sources.include_dirs) if ws.sources else set()
    dirs = [d for d in dict.fromkeys(f[:-len(tail)] for f in files if f.endswith(tail)) if d not in have]
    return sorted(dirs, key=lambda d: (-len(os.path.commonpath([d, near])), d))


def setting_value(ws, path: str) -> str:
    """How uvm.includeDirs says a directory: relative to the first workspace folder (which relative
    paths in it are read against) when inside it, else absolute."""
    roots = ws.config.workspace_roots
    if roots and (path == roots[0] or path.startswith(os.path.join(roots[0], ""))):
        return os.path.relpath(path, roots[0]).replace(os.sep, "/")
    return path


def add_incdir(ft: FileText, path: str) -> tuple[TextChange, str] | None:
    """An edit that adds +incdir+ for a directory to a filelist: after its last +incdir+ (or
    -incdir) line, else before its first line of anything but comments; and the option written."""
    rel = os.path.relpath(path, os.path.dirname(ft.path)).replace(os.sep, "/")
    if re.search(r"\s|[\"'$]", rel):
        return None  # the filelist would need quoting, which simulators read differently
    option = f"+incdir+{rel}"
    masked = mask(ft.text)  # its // and /* */ comments; # ones are left to the line test
    last = first = None
    for n in range(len(ft.starts)):
        line = masked[ft.starts[n]:ft.ends[n]].strip()
        if re.match(r"(\+incdir\+|-incdir\b)", line, re.IGNORECASE):
            last = n
        elif first is None and line and not line.startswith("#"):
            first = n
    if last is not None:
        at, indent = ft.ends[last], indent_of(ft.line(last))
        return TextChange(ft.path, at, at, ft.eol(last) + indent + option), option
    at = ft.starts[first] if first is not None else len(ft.text)
    eol = ft.eol(first)
    if first is None and ft.text and not ft.text.endswith(("\n", "\r")):
        return TextChange(ft.path, at, at, eol + option + eol), option
    return TextChange(ft.path, at, at, option + eol), option


def _filelist_for(ws, path: str) -> str | None:
    """The configured filelist an include directory for a file goes in: the first that lists the
    file, else the first."""
    lists = ws.config.filelists if ws.sources and ws.sources.mode == "filelist" else []
    return next((fl for fl in lists if path in parse_filelist(fl).files), lists[0] if lists else None)


def _missing_include(ctx: Context, d) -> list[Fix]:
    """An `include slang cannot open, of a file that is in the workspace: add the directory that
    holds it, as the include names it, to uvm.includeDirs (through the editor), or to the filelist."""
    dirs = include_dirs_for(ctx.ws, (d.data or {}).get("include") or "", d.range.path)[:3]
    filelist = _filelist_for(ctx.ws, d.range.path)
    command = "uvm.addIncludeDir"
    fixes = []
    for path in dirs:
        if command in ctx.client.commands:
            value = setting_value(ctx.ws, path)
            fixes.append(Fix(f"Add '{value}' to uvm.includeDirs", command=(command, [{"value": value}])))
        ft = filelist and ctx.files.get(filelist)
        added = ft and add_incdir(ft, path)
        if added:
            fixes.append(Fix(f"Add {added[1]} to {os.path.basename(filelist)}", [added[0]]))
    if len(fixes) == 1 and fixes[0].command:
        fixes[0].preferred = True  # a setting of this editor's; a filelist is the build's, not ours to change unasked
    return fixes


# -- the ignore directive ------------------------------------------------------------


def _in_comment_or_string(text: str, offset: int) -> bool:
    """Whether a comment or string literal that starts before an offset goes on past it."""
    for m in MASK_RE.finditer(text):
        if m.start() >= offset:
            return False
        if m.end() > offset:
            return True
    return False


def _ignore(ctx: Context, d) -> list[Fix]:
    """`// uvm-lint: ignore {code}` for the line a lint finding starts on: added to a directive about
    that line, when there is one; else after the code, or, when the line ends in a comment, on a line
    of its own above it."""
    code = d.code
    ft = ctx.files.get(d.range.path)
    at = ft and ft.offset(d.range.line, d.range.col)  # the finding: code, so not in a comment
    if at is None or not re.fullmatch(r"[\w-]+", code or ""):
        return []
    n, title = d.range.line, f"Ignore '{code}' on this line"
    rest = ft.text[at:ft.ends[n]]
    parts = list(MASK_RE.finditer(rest))
    if any(m.group(0)[0] == '"' and not m.group("close") for m in parts):
        return []  # a string the line does not close
    tail = next((m for m in parts if m.group(0)[0] != '"' and not rest[m.end():].strip()), None)
    directive = tail and IGNORE_RE.match(tail.group(0))
    if directive:  # after the code: add the code to it
        if code in ignore_codes(directive):
            return []
        end = at + tail.start() + directive.end(1)
        return [Fix(title, [TextChange(ft.path, end, end, f", {code}")])]
    if n in ctx.ws.ignores.get(ft.path, {}):  # one on a line of its own above: the nearest
        above = [x for x in ignore_directives(ft.text) if x.target == n and x.line < n]
        if above:
            if code in above[-1].codes:
                return []
            return [Fix(title, [TextChange(ft.path, above[-1].end, above[-1].end, f", {code}")])]
    text = f"// uvm-lint: ignore {code}"
    if tail is None:
        end = at + len(rest.rstrip())
        return [Fix(title, [TextChange(ft.path, end, end, "  " + text)])]
    # The line ends in a comment (a // one, or a block comment, closed or not): a line of its own
    # above it. Not when the line starts inside a block comment, or a string continued from the line
    # before, that ends before the finding: a line put there would be part of it.
    start = ft.starts[n]
    if ("*/" in ft.text[start:at] or (n > 0 and ft.line(n - 1).endswith("\\"))) \
            and _in_comment_or_string(ft.text, start):
        return []
    return [Fix(title, [TextChange(ft.path, start, start, indent_of(ft.line(n)) + text + ft.eol(n))])]


FIXERS = {
    **{(source, code): _typo for source in ("slang", ELAB_SOURCE)
       for code in ("TypoKeyword", "TypoIdentifier", "UnknownMember")},
    **{(source, "CouldNotOpenIncludeFile"): _missing_include for source in ("slang", ELAB_SOURCE)},
    ("uvm", "config-db-no-set"): _config_db_no_set,
    ("uvm", "config-db-type-mismatch"): _config_db_type_mismatch,
    ("uvm", "phase-no-super"): _phase_no_super,
    ("uvm", "utils-param"): _utils_param,
    ("uvm", "create-new"): _create_new,
    ("uvm", "factory-ctor"): _factory_ctor,
    ("uvm", "utils-mismatch"): _utils_mismatch,
    ("uvm", "create-unregistered"): _create_unregistered,
    ("uvm", "create-no-parent"): _create_no_parent,
}


def fixes_for(ctx: Context, d) -> list[Fix]:
    """The quick fixes for a current diagnostic (a model.Diag), each checked against the index and
    the text; a fix that would edit a file it may not is dropped, or disabled with the reason."""
    fixer = FIXERS.get((d.source, d.code))
    return _checked(ctx, d, fixer) if fixer else []


def ignore_fixes(ctx: Context, d) -> list[Fix]:
    """The fix that ignores a lint finding on its line. slang's diagnostics have none: a compile
    error cannot be waived. Never preferred, never applied by fix-all."""
    return _checked(ctx, d, _ignore) if d.source == "uvm" else []


def _checked(ctx: Context, d, fixer) -> list[Fix]:
    try:
        found = fixer(ctx, d)
    except Exception:  # a fix must never break the request
        log.exception("quick fix for %s", d.code)
        return []
    out = []
    for fix in found:
        fix.disabled = fix.disabled or next(
            (r for r in map(ctx.not_editable, {c.path for c in fix.changes}) if r), None)
        if fix.disabled is None:
            out.append(fix)
        elif ctx.client.disabled_support:
            fix.changes, fix.preferred, fix.safe = [], False, False
            out.append(fix)
    return out


SAFE_CODES = {"create-no-parent", "create-unregistered", "utils-mismatch", "factory-ctor"}  # can have a safe fix


def _clash(a: TextChange, b: TextChange) -> bool:
    """Two edits of a file that cannot both apply: they overlap, or start at the same place."""
    return a.path == b.path and (a.start == b.start or (a.start < b.end and b.start < a.end))


def fix_all(ctx: Context, diags, code: str | None = None) -> tuple[list[TextChange], list]:
    """The safe fixes of the findings (all, or those of one code) in the file asked about, merged:
    an edit two findings share is made once; an edit that clashes with one already taken (an earlier
    finding's) is left for the next pass. Returns the edits and the findings they fix."""
    taken: list[TextChange] = []
    fixed = []
    for d in sorted(diags, key=lambda d: (d.range.line, d.range.col)):
        if d.source != "uvm" or d.code not in SAFE_CODES or (code and d.code != code):
            continue
        fix = next((f for f in fixes_for(ctx, d) if f.safe and f.changes and not f.disabled), None)
        if fix is None or any(c.path != ctx.path for c in fix.changes):
            continue
        new = [c for c in fix.changes if c not in taken]
        if any(_clash(c, t) for c in new for t in taken):
            continue
        taken += new
        fixed.append(d)
    return taken, fixed
