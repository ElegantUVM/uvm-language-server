"""Turn slang syntax trees into the plain data in model.py.

The indexer works on syntax, not on an elaborated design. That keeps it fast
(the whole UVM library parses and indexes in well under a second), lets it work
on code that does not compile yet, and means a change to one file only needs
that file's root to be re-parsed.
"""

from __future__ import annotations

import bisect
import os
import re
from dataclasses import dataclass, replace

import pyslang as ps
from pyslang import parsing as P
from pyslang import syntax as S

from . import naturaldocs
from .model import (
    ClassInfo, ConfigDbCall, ConnectCall, CreateCall, DesignUnit, Diag, FileIndex,
    Instance, MacroDef, Method, OutOfBlockMethod, OverrideCall, Param, Range,
    SeqStart, TypeRef, Typedef, Variable,
)

K = S.SyntaxKind
TK = P.TokenKind
Token = P.Token
Node = S.SyntaxNode

SHIM_DIR = os.path.join(os.path.dirname(__file__), "shim")
SHIM_FILE = os.path.join(SHIM_DIR, "uvm_macros.svh")
SHIM_BUFFER_NAME = "<uvm-macro-shim>"
UVM_MACROS_PRELUDE = "<uvm-macros-prelude>"

FUNC_DECLS = (K.FunctionDeclaration, K.TaskDeclaration)
LOOP_KINDS = {
    K.ForLoopStatement, K.ForeachLoopStatement, K.LoopStatement, K.DoWhileStatement,
    K.ForeverStatement,
}
BUILTIN_TYPE_KINDS = {
    K.IntType, K.IntegerType, K.LogicType, K.BitType, K.RegType, K.ByteType,
    K.ShortIntType, K.LongIntType, K.TimeType, K.RealType, K.RealTimeType,
    K.ShortRealType, K.StringType, K.VoidType, K.EventType, K.CHandleType,
    K.ImplicitType, K.EnumType, K.StructType, K.UnionType, K.PropertyType,
    K.SequenceType, K.Untyped, K.TypeReference,
}
WORDISH = re.compile(r"[A-Za-z0-9_$]")
MACRO_DIRECTIVE_RE = re.compile(  # a `define, or a conditional around one
    r"^[ \t]*`(?:define[ \t]+(?P<name>[A-Za-z_]\w*)(?P<params>\([^)]*\))?.*"
    r"|(?P<cond>ifdef|ifndef|elsif|else|endif)\b[ \t]*(?P<arg>\w*).*)$", re.MULTILINE)
NEGATED = {"ifdef": "ifndef", "ifndef": "ifdef"}
EOL_RE = re.compile(r"\r\n|\r|\n")  # as slang counts lines
MASK_RE = re.compile(r'//[^\r\n]*|/\*.*?(?:\*/|\Z)|"(?P<body>(?:\\.|[^"\\\r\n])*)(?P<close>"?)', re.S)
NOT_EOL_RE = re.compile(r"[^\r\n]")
IGNORE_RE = re.compile(r"//\s*uvm-lint:\s*ignore\s+([\w-]+(?:\s*,\s*[\w-]+)*)")  # at a line comment's start
QUALIFIER_TOKENS = {
    TK.RandKeyword: "rand", TK.RandCKeyword: "randc", TK.StaticKeyword: "static",
    TK.LocalKeyword: "local", TK.ProtectedKeyword: "protected", TK.ConstKeyword: "const",
    TK.VirtualKeyword: "virtual", TK.PureKeyword: "pure", TK.ExternKeyword: "extern",
}
LINE_COMMENT, BLOCK_COMMENT = P.TriviaKind.LineComment, P.TriviaKind.BlockComment
END_OF_LINE, WHITESPACE = P.TriviaKind.EndOfLine, P.TriviaKind.Whitespace
RULE_CHARS = "-=*#~_+/\\"  # what a separator line in a comment is made of
TYPEDEF_KINDS = (K.TypedefDeclaration, K.ForwardTypedefDeclaration)
FAILURE_CALLS = {"$fatal", "$error", "uvm_report_fatal", "uvm_report_error"}
FAILURE_RE = re.compile(r"fatal|error", re.IGNORECASE)
DECLARATION_KINDS = {  # the block items that are not statements
    K.DataDeclaration, K.TypedefDeclaration, K.ForwardTypedefDeclaration,
    K.ParameterDeclarationStatement, K.PackageImportDeclaration, K.LetDeclaration,
    K.PortDeclaration,
}
CONFIG_DB_OPS = {"set", "get", "exists", "wait_modified"}
RESOURCE_DB_OPS = {"set": "set", "read_by_name": "get", "read_by_type": "get",
                   "set_default": "set", "set_override": "set"}


def slang_text(get, *args) -> str:
    """Text from slang as str. Source bytes that are not UTF-8 (a Latin-1 comment or string, say)
    become U+FFFD instead of raising: pybind11 decodes strictly, and keeps the bytes in the error."""
    try:
        return get(*args)
    except UnicodeDecodeError as e:
        return e.object.decode("utf-8", "replace")


def _continued(text: str, start: int) -> bool:
    """Whether the line starting at `start` goes on from the one before it (a `define body)."""
    end = start - 1  # the newline before it
    if end > 0 and text[end - 1] == "\r":
        end -= 1
    return end > 0 and text[end - 1] == "\\"


def mask(text: str) -> str:
    """`text` with comments and the insides of string literals blanked out, newlines kept, so a
    scan for `;`, brackets or keywords sees only code; offsets do not change."""
    def blank(m: re.Match) -> str:
        if m.group(0)[0] == '"':
            return '"' + NOT_EOL_RE.sub(" ", m.group("body")) + m.group("close")
        return NOT_EOL_RE.sub(" ", m.group(0))
    return MASK_RE.sub(blank, text)


@dataclass(frozen=True)
class IgnoreDirective:
    """`// uvm-lint: ignore code, ...`: lint findings with those codes that start on its target line
    are not reported."""

    line: int
    target: int | None  # its own line after code; else the next line with code (None: there is none)
    codes: tuple[str, ...]
    end: int  # offset in the text right after its last code, where another one is added


def ignore_codes(m: re.Match) -> tuple[str, ...]:
    return tuple(re.split(r"\s*,\s*", m.group(1)))


def ignore_directives(text: str) -> list[IgnoreDirective]:
    """The ignore directives in a file's text. Only a line comment is one: the same words in a
    string or a block comment are not. An `ifdef around one does not matter, since a finding can
    only be on a line that is compiled, and one on a line of its own never reaches past a line of
    code (an `endif included)."""
    if "uvm-lint" not in text:
        return []
    starts = [0] + [m.end() for m in EOL_RE.finditer(text)]
    code = [i for i, line in enumerate(EOL_RE.split(mask(text))) if line.strip()]  # lines with code
    out = []
    for m in MASK_RE.finditer(text):
        found = IGNORE_RE.match(m.group(0)) if m.group(0).startswith("//") else None
        if found:
            line = bisect.bisect_right(starts, m.start()) - 1
            i = bisect.bisect_left(code, line)  # a line comment ends its line: code on it is before it
            target = code[i] if i < len(code) else None
            out.append(IgnoreDirective(line, target, ignore_codes(found), m.start() + found.end(1)))
    return out


def _shim_text() -> str:
    with open(SHIM_FILE, encoding="utf-8") as f:
        return f.read()


DID_YOU_MEAN_RE = re.compile(r"^did you mean '([A-Za-z_][A-Za-z0-9_$]*)'\?$")


def fix_data(code: str, diag, notes=()) -> dict | None:
    """What a quick fix needs from one of slang's diagnostics: the include it could not open, or
    the name it suggests instead of a misspelt one (TypoKeyword/TypoIdentifier say it in their
    arguments, UnknownMember in a "did you mean" note)."""
    if code not in ("CouldNotOpenIncludeFile", "TypoKeyword", "TypoIdentifier", "UnknownMember"):
        return None
    try:
        args = [str(a) for a in diag.args]
    except UnicodeDecodeError:  # a name that is not UTF-8: no edit can match it
        return None
    if code == "CouldNotOpenIncludeFile" and args:
        return {"include": args[0]}
    if code in ("TypoKeyword", "TypoIdentifier") and len(args) >= 2:
        return {"from": args[0], "to": args[1]}
    if code == "UnknownMember" and args:
        to = [m.group(1) for m in map(DID_YOU_MEAN_RE.match, notes) if m]
        if len(to) == 1:
            return {"from": args[0], "to": to[0]}
    return None


def _uses_unknown_uvm_macro(tree) -> bool:
    """True when the file uses a UVM macro that nothing it includes defines."""
    return any(d.code == ps.Diags.UnknownDirective and d.args and
               str(d.args[0]).startswith(("`uvm_", "`UVM_")) for d in tree.diagnostics)


# ----------------------------------------------------------------------------
# Token / text helpers


def iter_tokens(node):
    """All tokens under a node, in source order."""
    stack = [node]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        if isinstance(n, Token):
            yield n
            continue
        children = [c for c in n]
        stack.extend(reversed(children))


def compact(node) -> str:
    """Source text without comments, with spaces only where needed."""
    out: list[str] = []
    prev = ""
    for tok in iter_tokens(node):
        raw = slang_text(getattr, tok, "rawText")
        if not raw:
            continue
        if prev and WORDISH.match(prev[-1]) and WORDISH.match(raw[0]):
            out.append(" ")
        out.append(raw)
        prev = raw
    return "".join(out)


def pretty(node) -> str:
    """Source text without comments, keeping single spaces where the source had any."""
    out: list[str] = []
    first = True
    for tok in iter_tokens(node):
        raw = slang_text(getattr, tok, "rawText")
        if not raw:
            continue
        if not first and any(t.kind in (P.TriviaKind.Whitespace, P.TriviaKind.EndOfLine,
                                          P.TriviaKind.LineComment, P.TriviaKind.BlockComment)
                             for t in tok.trivia):
            out.append(" ")
        out.append(raw)
        first = False
    return "".join(out)


def nodes(seq):
    """Syntax nodes of a (possibly comma-separated) list, without the separator tokens."""
    return [x for x in (seq or []) if isinstance(x, Node)]


def unwrap(expr):
    """Strip the property/sequence/argument wrappers slang puts around expressions."""
    while expr is not None and expr.kind in (
        K.OrderedArgument, K.NamedArgument, K.SimplePropertyExpr, K.SimpleSequenceExpr,
        K.ParenthesizedExpression,
    ):
        expr = getattr(expr, "expr", None)
    return expr


def string_literal(expr) -> str | None:
    expr = unwrap(expr)
    if expr is not None and expr.kind == K.StringLiteralExpression:
        return slang_text(getattr, expr.literal, "valueText")
    return None


def forwarded_phase(decl, meth) -> str | None:
    """The receiver of a phase body consisting solely of M(phase), with no work in its argument."""
    if meth.name not in ("build_phase", "connect_phase") or len(meth.args) != 1 or len(decl.items) != 1:
        return None
    st = decl.items[0]
    call = unwrap(st.expr) if st.kind == K.ExpressionStatement else None
    if call is None or call.kind != K.InvocationExpression or call.arguments is None:
        return None
    args = nodes(call.arguments.parameters)
    segs = flatten_name(call.left)
    if len(args) != 1 or compact(unwrap(args[0])) != meth.args[0].name or len(segs) != 2:
        return None
    if segs[1].name == meth.name and (segs[0].name == "super" and segs[1].sep == "." or segs[1].sep == "::"):
        return segs[0].name
    return None


def name_token(node):
    """The identifying token of a name-like node."""
    if node is None:
        return None
    k = node.kind
    if k in (K.IdentifierName, K.ClassName, K.IdentifierSelectName):
        return node.identifier
    if k == K.ScopedName:
        return name_token(node.right)
    if k == K.ConstructorName:
        return node.keyword
    if k in (K.SystemName,):
        return node.systemIdentifier
    return None


@dataclass
class Segment:
    name: str
    args: list[str]
    sep: str  # separator before this segment: '', '::' or '.'
    node: object


def flatten_name(node) -> list[Segment]:
    """`a::b #(T)::c.d` -> [a, b#(T), c, d] with their separators."""
    if node is None:
        return []
    k = node.kind
    if k == K.ScopedName:
        left = flatten_name(node.left)
        right = flatten_name(node.right)
        if right:
            right[0].sep = node.separator.rawText
        return left + right
    if k == K.ClassName:
        args = []
        pva = node.parameters
        if pva is not None:
            for p in nodes(pva.parameters):
                if p.kind == K.NamedParamAssignment:
                    expr = compact(p.expr) if p.expr is not None else ""
                    args.append(f".{p.name.valueText}({expr})")
                else:
                    args.append(compact(getattr(p, "expr", p)))
        return [Segment(node.identifier.valueText, args, "", node)]
    tok = name_token(node)
    if tok is not None:
        return [Segment(tok.valueText, [], "", node)]
    return [Segment(compact(node), [], "", node)]


def get_type_class(expr) -> str | None:
    """`X::get_type()` or `X::type_id::get()` -> 'X'."""
    expr = unwrap(expr)
    if expr is None or expr.kind != K.InvocationExpression:
        return None
    segs = flatten_name(expr.left)
    if len(segs) >= 2 and segs[-1].name == "get_type":
        return segs[-2].name
    if len(segs) >= 3 and segs[-1].name == "get" and segs[-2].name == "type_id":
        return segs[-3].name
    return None


def clean_doc(comments: list[str]) -> str:
    """Comment text without the comment marks, separator lines (`//-----`) and extra blank lines."""
    lines: list[str] = []
    for c in comments:
        c = c.strip()
        if c.startswith("//"):
            if IGNORE_RE.match(c):
                continue  # a lint waiver, not documentation
            parts = [c.lstrip("/").strip()]
        elif c.startswith("/*"):
            body = (c[2:-2] if c.endswith("*/") else c[2:]).lstrip("*")
            parts = [ln.strip().lstrip("*").strip() for ln in body.splitlines()]
        else:
            continue
        for ln in parts:
            if len(ln) >= 3 and not ln.strip(RULE_CHARS):
                continue  # a separator line
            if ln or (lines and lines[-1]):
                lines.append(ln)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _raw_length(trivia) -> int:
    """Length in bytes of a piece of trivia as it is in the file."""
    try:
        return len(trivia.getRawText().encode())
    except UnicodeDecodeError as e:
        return len(e.object)


@dataclass
class BodyCtx:
    owner: str
    method: str
    conditional: bool = False
    condition: str | None = None
    in_loop: bool = False

    def cond(self, text: str) -> "BodyCtx":
        c = text if not self.condition else f"{self.condition} && {text}"
        return replace(self, conditional=True, condition=c)


class Sink:
    """Where calls found in a procedural body are recorded."""

    def __init__(self):
        self.creates: list[CreateCall] = []
        self.config_db: list[ConfigDbCall] = []
        self.overrides: list[OverrideCall] = []
        self.connects: list[ConnectCall] = []
        self.seq_starts: list[SeqStart] = []
        self.run_tests: list[tuple[str, Range]] = []
        self.locals: list[Variable] = []
        self.super_calls: list[str] = []


# ----------------------------------------------------------------------------


class Indexer:
    def __init__(self, include_dirs: list[str], defines: dict[str, str], use_shim: bool,
                 library_dirs=(), library_guard: str | None = None):
        self.include_dirs = list(include_dirs)
        self.defines = dict(defines)
        self.use_shim = use_shim
        self.library_dirs = tuple(os.path.join(d, "") for d in library_dirs)
        # Predefined in user roots: one that includes uvm_pkg.sv itself does not parse the library
        # again (its own root indexes it), as user code.
        self.library_guard = library_guard
        self._shim = _shim_text() if use_shim else ""
        self._real_macros = not use_shim and any(
            os.path.isfile(os.path.join(d, "uvm_macros.svh")) for d in self.include_dirs)

    def parse(self, root: str, overrides: dict[str, str] | None = None,
              library: bool = False) -> FileIndex:
        overrides = overrides or {}
        sm = ps.SourceManager()
        for path, text in overrides.items():
            if path != root:
                sm.assignText(path, text)
        po = P.PreprocessorOptions()
        dirs = list(self.include_dirs)
        if self.use_shim:
            dirs.append(SHIM_DIR)
        po.additionalIncludePaths = dirs
        predefines = [f"{k}={v}" if v != "" else k for k, v in self.defines.items()]
        if self.library_guard and not library:
            predefines.append(self.library_guard)
        po.predefines = predefines  # a copy goes to slang: assign the whole list
        bag = ps.Bag([po])
        buffers = []
        if self.use_shim and not library:
            buffers.append(sm.assignText(SHIM_BUFFER_NAME, self._shim))
        try:
            if root in overrides:
                buffers.append(sm.assignText(root, overrides[root]))
            else:
                buffers.append(sm.readSource(root))
        except Exception as e:  # unreadable file
            idx = FileIndex(root=root, library=library)
            idx.diagnostics.append(Diag(Range(root, 0, 0, 0, 0), f"cannot read file: {e}", "error", "io"))
            return idx
        tree = S.SyntaxTree.fromBuffers(buffers, sm, bag)
        if self._real_macros and not library and _uses_unknown_uvm_macro(tree):
            # Simulators usually have the UVM macros in scope already (from an earlier file in
            # the compilation unit, or -uvm), so predefine the real ones, as the shim would be.
            prelude = sm.assignText(UVM_MACROS_PRELUDE, '`include "uvm_macros.svh"\n')
            tree = S.SyntaxTree.fromBuffers([prelude] + buffers, sm, bag)
        return Extractor(tree, sm, root, library, overrides, self.library_dirs).run()


class Extractor:
    def __init__(self, tree, sm, root: str, library: bool, overrides: dict[str, str],
                 library_dirs: tuple[str, ...] = ()):
        self.tree = tree
        self.sm = sm
        self.root = root
        self.library = library
        self.overrides = overrides
        self.library_dirs = library_dirs
        self.out = FileIndex(root=root, library=library)
        self._paths: dict[int, str | None] = {}

    # -- locations -----------------------------------------------------------

    def path_of(self, buffer) -> str | None:
        bid = buffer.id
        if bid not in self._paths:
            p = self.sm.getFullPath(buffer)
            p = str(p) if p else ""
            if not p or p.startswith("<") or p.endswith(SHIM_BUFFER_NAME) or \
                    os.path.normpath(os.path.abspath(p)) == SHIM_FILE:
                self._paths[bid] = None
            else:
                self._paths[bid] = os.path.normpath(os.path.abspath(p))
        return self._paths[bid]

    def file_loc(self, loc):
        """(location in a real file, generated-by-macro flag)."""
        sm = self.sm
        if not sm.isMacroLoc(loc):
            return loc, False
        if sm.isMacroArgLoc(loc):
            # Written by the user at the call site, e.g. T in `uvm_component_utils(T).
            orig = sm.getFullyOriginalLoc(loc)
            if not sm.isMacroLoc(orig) and self.path_of(orig.buffer):
                return orig, False
        # Spelled inside a macro body: attribute it to the expansion site.
        return sm.getFullyExpandedLoc(loc), True

    def make_range(self, start, end=None) -> tuple[Range | None, bool]:
        start, gen = self.file_loc(start)
        path = self.path_of(start.buffer)
        if path is None:
            return None, gen
        sm = self.sm
        line, col = sm.getLineNumber(start) - 1, sm.getColumnNumber(start) - 1
        if end is not None:
            end, _ = self.file_loc(end)
            if end.buffer == start.buffer and end.offset >= start.offset:
                return Range(path, line, col, sm.getLineNumber(end) - 1,
                             sm.getColumnNumber(end) - 1), gen
        return Range(path, line, col, line, col), gen

    def tok_range(self, tok) -> tuple[Range | None, bool]:
        if isinstance(tok, Node):
            tok = name_token(tok)
            if tok is None:
                return None, False
        r = tok.range
        return self.make_range(r.start, r.end)

    def plain_range(self, tok) -> Range | None:
        """A token's range, or None when a macro wrote it: nothing may edit generated text."""
        if tok is None or self.sm.isMacroLoc(tok.location):
            return None
        r = tok.range
        return self.make_range(r.start, r.end)[0]

    def macro_call(self, loc) -> tuple[str | None, object | None]:
        """(name, range) of the outermost macro call whose expansion holds `loc`."""
        sm = self.sm
        name, rng = None, None
        while sm.isMacroLoc(loc):
            name = slang_text(sm.getMacroName, loc)
            rng = sm.getExpansionRange(loc)
            loc = rng.start
        return name, rng

    def span(self, node) -> Range | None:
        """A node's range in its file, where a token a macro wrote counts as the whole macro call
        (node_range ends such a node where the call starts)."""
        first, last = node.getFirstToken(), node.getLastToken()
        if first is None or last is None:
            return None
        start, _ = self.file_loc(first.location)
        end = last.range.end
        if self.sm.isMacroLoc(end) and not self.sm.isMacroArgLoc(end):
            end = self.macro_call(end)[1].end
        else:
            end, _ = self.file_loc(end)
        rng, _ = self.make_range(start, end)
        return rng

    def node_range(self, node) -> Range | None:
        first, last = node.getFirstToken(), node.getLastToken()
        if first is None:
            return None
        start, _ = self.file_loc(first.location)
        end, _ = self.file_loc(last.range.end) if last is not None else (start, False)
        path = self.path_of(start.buffer)
        if path is None:
            return None
        sm = self.sm
        line, col = sm.getLineNumber(start) - 1, sm.getColumnNumber(start) - 1
        if end.buffer == start.buffer and end.offset >= start.offset:
            return Range(path, line, col, sm.getLineNumber(end) - 1, sm.getColumnNumber(end) - 1)
        return Range(path, line, col, line, col)

    def comment_blocks(self, token):
        """The comment blocks in a token's leading trivia, nearest first, each with whether it is
        right above what follows it. A blank line ends a block; a comment that ends the line
        before (`x = 1; // note`) belongs to that line."""
        trivia = list(token.trivia)
        cur: list[str] = []
        adjacent, newlines, complete = False, 0, True
        for tr in reversed(trivia):
            k = tr.kind  # enum members: `is` is the fast comparison
            if k is LINE_COMMENT or k is BLOCK_COMMENT:
                if cur and newlines > 1:
                    yield cur, adjacent
                    cur = []
                if not cur:
                    adjacent = newlines <= 1
                cur.insert(0, slang_text(tr.getRawText))
                newlines = 0
            elif k is END_OF_LINE:
                newlines += 1
            elif k is not WHITESPACE:
                complete = False
                break
        if cur and newlines == 0 and not (complete and self.sm.getFullyExpandedLoc(
                token.location).offset == sum(_raw_length(t) for t in trivia)):
            cur.pop(0)  # not at the start of the file: it follows code on its line
        if cur:
            yield cur, adjacent

    def doc_of(self, node, name: str, before=()) -> str:
        """The comment documenting a declaration: the block right above it, or else a NaturalDocs
        topic naming it (`// Function: name`) further up, past blank lines and the typedefs
        `before` it (nearest first)."""
        for i, n in enumerate((node, *before)):
            first = n.getFirstToken()
            if first is None:
                break
            for comments, adjacent in self.comment_blocks(first):
                text = clean_doc(comments)
                if not text:
                    continue  # a separator line
                about = naturaldocs.topic(text, name) if name in text else None
                if about is not None:
                    return about
                return naturaldocs.untitled(text) if adjacent and i == 0 else ""
        return ""

    # -- type references -----------------------------------------------------

    def type_ref_from_name(self, node) -> TypeRef | None:
        if node is None:
            return None
        segs = flatten_name(node)
        if not segs:
            return None
        last = segs[-1]
        scope = "::".join(s.name for s in segs[:-1]) if len(segs) > 1 else None
        tok = name_token(node)
        rng = self.tok_range(tok)[0] if tok is not None else None
        return TypeRef(name=last.name, text=compact(node), scope=scope, args=last.args, range=rng)

    def type_ref(self, dtype) -> TypeRef | None:
        if dtype is None or dtype.kind in BUILTIN_TYPE_KINDS:
            return None
        if dtype.kind == K.NamedType:
            return self.type_ref_from_name(dtype.name)
        if dtype.kind == K.VirtualInterfaceType:
            tok = dtype.name
            rng = self.tok_range(tok)[0]
            return TypeRef(name=tok.valueText, text=compact(dtype), range=rng)
        return None

    # -- top level -----------------------------------------------------------

    def run(self) -> FileIndex:
        self.out.files.add(self.root)
        imports: list[str] = []
        before: list = []  # the typedefs right before a member, nearest first
        for m in nodes(self.tree.root.members):
            self.unit_member(m, None, imports, None, before)
            before = [m, *before] if m.kind in TYPEDEF_KINDS else []
        self.collect_diagnostics()
        self.collect_includes()
        self.collect_macros()
        if not self.library:
            self.collect_identifiers()
        return self.out

    def unit_member(self, m, package: str | None, imports: list[str], unit: DesignUnit | None,
                    before=()):
        k = m.kind
        if k == K.PackageDeclaration:
            self.design_unit(m, "package")
        elif k in (K.ModuleDeclaration, K.InterfaceDeclaration, K.ProgramDeclaration):
            kind = {K.ModuleDeclaration: "module", K.InterfaceDeclaration: "interface",
                    K.ProgramDeclaration: "program"}[k]
            self.design_unit(m, kind)
        elif k == K.ClassDeclaration:
            self.class_decl(m, package, list(imports), None, before)
        elif k == K.TypedefDeclaration:
            td = self.typedef(m, package, None)
            if td:
                self.out.typedefs.append(td)
                if unit is not None:
                    self.enum_members(m, unit)
        elif k == K.PackageImportDeclaration:
            for item in nodes(m.items):
                imports.append(item.package.valueText)
                if unit is not None:
                    unit.imports.append(item.package.valueText)
        elif k in FUNC_DECLS:
            name_node = m.prototype.name
            if name_node.kind == K.ScopedName:
                self.out_of_block(m, package)
            elif unit is not None:
                meth = self.method(m, [], unit.name)
                if meth:
                    unit.methods.append(meth)

    def design_unit(self, m, kind: str):
        hdr = m.header
        rng, _ = self.tok_range(hdr.name)
        if rng is None:
            return
        unit = DesignUnit(name=hdr.name.valueText, kind=kind, range=rng,
                          decl_range=self.node_range(m) or rng, library=self.library,
                          doc=self.doc_of(m, hdr.name.valueText))
        unit.params = self.params(hdr.parameters)
        for imp in nodes(hdr.imports):
            for item in nodes(imp.items):
                unit.imports.append(item.package.valueText)
        ports = getattr(hdr, "ports", None)
        if ports is not None and ports.kind == K.AnsiPortList:
            for p in nodes(ports.ports):
                if p.kind == K.ImplicitAnsiPort:
                    dtype = getattr(p.header, "dataType", None)
                    v = self.variable(p.declarator, "port", dtype, compact(p), [])
                    if v:
                        unit.ports.append(v)
        package = unit.name if kind == "package" else None
        imports = list(unit.imports)
        before: list = []
        for item in nodes(m.members):
            ik = item.kind
            if ik in (K.DataDeclaration, K.NetDeclaration):
                for d in nodes(item.declarators):
                    v = self.variable(d, "signal", item.type, pretty(item), [])
                    if v:
                        unit.variables.append(v)
            elif ik == K.ClockingDeclaration and item.blockName is not None:
                r, _ = self.tok_range(item.blockName)
                if r:
                    unit.variables.append(Variable(item.blockName.valueText, "clocking", "clocking",
                                                   None, r, f"clocking {item.blockName.valueText}"))
            elif ik == K.ModportDeclaration:
                for mp in nodes(item.items):
                    r, _ = self.tok_range(mp.name)
                    if r:
                        unit.variables.append(Variable(mp.name.valueText, "modport", "modport", None,
                                                       r, f"modport {compact(mp)}"))
            elif ik == K.HierarchyInstantiation:
                for inst in nodes(item.instances):
                    decl = getattr(inst, "decl", None)
                    if decl is not None and decl.name is not None:
                        r, _ = self.tok_range(decl.name)
                        if r:
                            unit.instances.append(Instance(item.type.valueText, decl.name.valueText, r))
            elif ik in (K.InitialBlock, K.FinalBlock, K.AlwaysBlock, K.AlwaysCombBlock,
                        K.AlwaysFFBlock, K.AlwaysLatchBlock):
                sink = Sink()
                self.walk(item.statement, BodyCtx(unit.name, "initial"), sink)
                unit.config_db += sink.config_db
                unit.run_tests += sink.run_tests
            elif ik == K.ParameterDeclarationStatement:
                for p in self.params_from_decl(item.parameter):
                    unit.params.append(p)
            else:
                self.unit_member(item, package, imports, unit, before)
            before = [item, *before] if ik in TYPEDEF_KINDS else []
        self.out.units.append(unit)

    def enum_members(self, td_node, unit: DesignUnit):
        t = td_node.type
        if t is None or t.kind != K.EnumType:
            return
        for mem in nodes(t.members):
            r, _ = self.tok_range(mem.name)
            if r:
                unit.variables.append(Variable(mem.name.valueText, "enum_member", td_node.name.valueText,
                                               None, r, f"{td_node.name.valueText}::{compact(mem)}"))

    def params_from_decl(self, decl, local: bool = False) -> list[Param]:
        out = []
        if decl is None:
            return out
        local = local or decl.keyword.kind == TK.LocalParamKeyword
        if decl.kind == K.TypeParameterDeclaration:
            for d in nodes(decl.declarators):
                default = compact(d.assignment.type) if d.assignment is not None else ""
                out.append(Param(d.name.valueText, default, True, local))
        elif decl.kind == K.ParameterDeclaration:
            for d in nodes(decl.declarators):
                default = compact(d.initializer.expr) if d.initializer is not None else ""
                out.append(Param(d.name.valueText, default, False, local, compact(decl.type)))
        return out

    def params(self, plist) -> list[Param]:
        out: list[Param] = []
        if plist is None:
            return out
        local = False
        for decl in nodes(plist.declarations):
            keyword = getattr(decl, "keyword", None)
            if keyword is not None and keyword.kind in (TK.LocalParamKeyword, TK.ParameterKeyword):
                local = keyword.kind == TK.LocalParamKeyword
            out += self.params_from_decl(decl, local)
        return out

    # -- classes -------------------------------------------------------------

    def class_decl(self, n, package: str | None, imports: list[str], outer: str | None,
                   before=()):
        rng, generated = self.tok_range(n.name)
        if rng is None:
            return
        voi = n.virtualOrInterface
        kind = "interface class" if voi is not None and voi.kind == TK.InterfaceKeyword else "class"
        cls = ClassInfo(
            name=n.name.valueText, package=package, kind=kind, range=rng,
            decl_range=self.node_range(n) or rng,
            is_virtual=voi is not None and voi.kind == TK.VirtualKeyword,
            params=self.params(n.parameters), imports=imports, outer_class=outer,
            library=self.library, doc=self.doc_of(n, n.name.valueText, before),
        )
        cls.header_end, cls.end_kw = self.plain_range(n.semi), self.plain_range(n.endClass)
        if n.extendsClause is not None:
            cls.base = self.type_ref_from_name(n.extendsClause.baseName)
            if n.extendsClause.arguments is not None:
                cls.extends_args = compact(n.extendsClause.arguments)
        if n.implementsClause is not None:
            for iface in n.implementsClause.interfaces:
                if isinstance(iface, Node):
                    t = self.type_ref_from_name(iface)
                    if t:
                        cls.implements.append(t)
        for item in nodes(n.items):
            self.class_item(cls, item, package, imports)
        self.out.classes.append(cls)

    def qualifiers(self, toks) -> list[str]:
        out = []
        for t in toks or []:
            q = QUALIFIER_TOKENS.get(t.kind)
            if q:
                out.append(q)
        return out

    def class_item(self, cls: ClassInfo, item, package, imports):
        k = item.kind
        if k == K.ClassPropertyDeclaration:
            quals = self.qualifiers(item.qualifiers)
            decl = item.declaration
            dk = decl.kind
            if dk == K.DataDeclaration:
                quals += self.qualifiers(decl.modifiers)
                text = pretty(item)
                for d in nodes(decl.declarators):
                    v = self.variable(d, "property", decl.type, text, quals)
                    if v:
                        v.doc = self.doc_of(item, v.name)
                        cls.properties.append(v)
            elif dk == K.TypedefDeclaration:
                self.class_typedef(cls, decl, package)
            elif dk == K.ParameterDeclarationStatement:
                self.class_localparams(cls, decl)
        elif k == K.TypedefDeclaration:
            self.class_typedef(cls, item, package)
        elif k == K.ParameterDeclarationStatement:
            self.class_localparams(cls, item)
        elif k == K.ClassMethodDeclaration:
            # The item starts with the qualifiers (`virtual task ...`): the doc comment is on it.
            meth, sink = self.method_with_body(item.declaration, self.qualifiers(item.qualifiers),
                                               cls.name, item)
            if meth:
                cls.methods.append(meth)
                self.merge_sink(cls, sink)
        elif k == K.ClassMethodPrototype:
            meth = self.prototype(item.prototype, self.qualifiers(item.qualifiers), item)
            if meth:
                cls.methods.append(meth)
        elif k in (K.ConstraintDeclaration, K.ConstraintPrototype):
            r, _ = self.tok_range(item.name)
            if r:
                cls.constraints.append(((item.name.valueText if isinstance(item.name, Token) else compact(item.name)), r))
        elif k == K.CovergroupDeclaration:
            r, _ = self.tok_range(item.name)
            if r:
                cls.covergroups.append((item.name.valueText, r))
        elif k == K.ClassDeclaration:
            self.class_decl(item, package, imports, cls.name)

    def class_localparams(self, cls: ClassInfo, stmt):
        decl = stmt.parameter
        text = pretty(stmt)
        if decl is None:
            return
        for d in nodes(decl.declarators):
            tok = d.name
            r, gen = self.tok_range(tok)
            if r:
                cls.properties.append(Variable(tok.valueText, "parameter", compact(decl.type) if
                                               getattr(decl, "type", None) is not None else "type",
                                               None, r, text, ["localparam"], gen,
                                               default=compact(d.initializer.expr) if
                                               getattr(d, "initializer", None) is not None else None))

    def class_typedef(self, cls: ClassInfo, decl, package):
        td = self.typedef(decl, package, cls.name)
        if not td:
            return
        cls.typedefs.append(td)
        if td.name == "type_id" and td.target is not None:
            reg = td.target.name
            if reg in ("uvm_component_registry", "uvm_abstract_component_registry"):
                cls.registration = "component"
            elif reg in ("uvm_object_registry", "uvm_abstract_object_registry"):
                cls.registration = "object"
            name, rng = self.macro_call(decl.typedefKeyword.location)
            if rng is not None:
                cls.registration_macro = name
                cls.registration_range = self.make_range(rng.start, rng.end)[0]
            cls.registration_arg = td.target.args[0] if td.target.args else None

    def typedef(self, decl, package, owner_class) -> Typedef | None:
        if decl.kind != K.TypedefDeclaration:
            return None
        r, _ = self.tok_range(decl.name)
        if r is None:
            return None
        t = decl.type
        target = self.type_ref(t)
        target_text = compact(t)
        if t is not None and t.kind in (K.EnumType, K.StructType, K.UnionType):
            target_text = {K.EnumType: "enum", K.StructType: "struct", K.UnionType: "union"}[t.kind]
        return Typedef(decl.name.valueText, target_text, target, r, pretty(decl), package, owner_class)

    def variable(self, declarator, kind: str, dtype, text: str, quals: list[str]) -> Variable | None:
        tok = declarator.name
        r, gen = self.tok_range(tok)
        if r is None:
            return None
        type_text = compact(dtype) if dtype is not None else ""
        return Variable(tok.valueText, kind, type_text, self.type_ref(dtype), r, text, quals, gen)

    # -- methods -------------------------------------------------------------

    def prototype(self, proto, quals: list[str], decl_node, doc_node=None) -> Method | None:
        tok = name_token(proto.name)
        if tok is None:
            return None
        r, gen = self.tok_range(tok)
        if r is None:
            return None
        kind = "task" if proto.keyword.kind == TK.TaskKeyword else "function"
        ret = compact(proto.returnType) if proto.returnType is not None else ""
        args = []
        if proto.portList is not None:
            for port in nodes(proto.portList.ports):
                if port.kind == K.FunctionPort and port.declarator is not None:
                    v = self.variable(port.declarator, "argument", port.dataType, compact(port), [])
                    if v:
                        init = port.declarator.initializer
                        if init is not None and init.expr is not None:
                            v.default = compact(init.expr)
                        args.append(v)
        return Method(name=tok.valueText, kind=kind, return_type=ret, signature=pretty(proto),
                      range=r, decl_range=self.node_range(decl_node) or r, args=args,
                      qualifiers=quals, is_prototype=True, generated=gen,
                      doc=self.doc_of(decl_node if doc_node is None else doc_node, tok.valueText))

    def method(self, decl, quals, owner) -> Method | None:
        meth, _ = self.method_with_body(decl, quals, owner)
        return meth

    def method_with_body(self, decl, quals, owner, doc_node=None) -> tuple[Method | None, Sink]:
        sink = Sink()
        if decl.kind not in FUNC_DECLS:
            return None, sink
        meth = self.prototype(decl.prototype, quals, decl, doc_node)
        if meth is None:
            return None, sink
        meth.is_prototype = False
        meth.non_ansi = decl.prototype.portList is None
        if meth.non_ansi:
            for st in decl.items:
                if st.kind == K.PortDeclaration and st.header is not None:
                    for d in nodes(st.declarators):
                        v = self.variable(d, "argument", getattr(st.header, "dataType", None), compact(st), [])
                        if v:
                            meth.args.append(v)
        if not self.library and not self.in_library(meth.range.path):
            ctx = BodyCtx(owner, meth.name)
            for st in decl.items:
                self.walk(st, ctx, sink)
            meth.locals = sink.locals
            meth.super_calls = sink.super_calls
            meth.forwarded_phase = forwarded_phase(decl, meth)
            if not meth.generated:
                meth.header_end, meth.end_kw = self.plain_range(decl.semi), self.plain_range(decl.end)
                meth.body_items = [r for r in (self.span(st) for st in decl.items
                                               if st.kind not in DECLARATION_KINDS) if r is not None]
        return meth, sink

    def merge_sink(self, target, sink: Sink):
        target.creates += sink.creates
        target.config_db += sink.config_db
        target.overrides += sink.overrides
        target.connects += sink.connects
        target.seq_starts += sink.seq_starts

    def out_of_block(self, decl, package):
        segs = flatten_name(decl.prototype.name)
        if len(segs) < 2:
            return
        class_name = segs[-2].name
        meth, sink = self.method_with_body(decl, [], class_name)
        if meth is None:
            return
        ob = OutOfBlockMethod(class_name=class_name, package=package, method=meth)
        self.merge_sink(ob, sink)
        self.out.out_of_block.append(ob)

    # -- procedural bodies ---------------------------------------------------

    def walk(self, node, ctx: BodyCtx, sink: Sink):
        if node is None or not isinstance(node, Node):
            return
        k = node.kind
        if k == K.ConditionalStatement:
            cond = compact(node.predicate)
            self.walk(node.predicate, ctx, sink)
            self.walk(node.statement, ctx.cond(cond), sink)
            if node.elseClause is not None:
                self.walk(node.elseClause.clause, ctx.cond(f"!({cond})"), sink)
            return
        if k == K.CaseStatement:
            self.walk(node.expr, ctx, sink)
            sel = compact(node.expr)
            for item in node.items:
                if not isinstance(item, Node):
                    continue
                if item.kind == K.StandardCaseItem:
                    label = ", ".join(compact(e) for e in item.expressions if isinstance(e, Node))
                    self.walk(item.clause, ctx.cond(f"{sel} == {label}"), sink)
                else:
                    self.walk(getattr(item, "clause", None), ctx.cond(f"{sel}: default"), sink)
            return
        if k in LOOP_KINDS:
            ctx = replace(ctx, in_loop=True)
        elif k == K.InvocationExpression:
            self.on_call(node, ctx, sink)
        elif k == K.AssignmentExpression:
            self.on_assign(node, ctx, sink)
        elif k == K.DataDeclaration:
            text = pretty(node)
            for d in nodes(node.declarators):
                v = self.variable(d, "local", node.type, text, self.qualifiers(node.modifiers))
                if v:
                    sink.locals.append(v)
        for c in node:
            if isinstance(c, Node):
                self.walk(c, ctx, sink)

    def assignment_target(self, node) -> str | None:
        p = node.parent
        while p is not None and p.kind in (K.ParenthesizedExpression,):
            p = p.parent
        if p is None:
            return None
        if p.kind == K.AssignmentExpression and p.right is not None and \
                p.right.sourceRange.start.offset <= node.sourceRange.start.offset:
            return compact(p.left)
        if p.kind == K.EqualsValueClause and p.parent is not None and p.parent.kind == K.Declarator:
            return p.parent.name.valueText
        return None

    def on_call(self, node, ctx: BodyCtx, sink: Sink):
        segs = flatten_name(node.left)
        if not segs:
            return
        args = []
        if node.arguments is not None:
            args = [unwrap(a) for a in node.arguments.parameters if isinstance(a, Node)]
        last = segs[-1].name
        r = self.node_range(node)
        if r is None:
            return

        # super.build_phase(phase), uvm_component::build_phase(phase)
        if len(segs) >= 2 and (segs[-2].name == "super" and segs[-1].sep == "."
                               or segs[-1].sep == "::"):
            sink.super_calls.append(last if segs[-2].name == "super" else f"{segs[-2].name}::{last}")

        # T::type_id::create(name, parent)
        if last == "create" and len(segs) >= 3 and segs[-2].name == "type_id":
            tseg = segs[-3]
            scope = segs[-4].name if len(segs) >= 4 else None
            tref = TypeRef(name=tseg.name, text=compact(tseg.node), scope=scope, args=tseg.args,
                           range=self.tok_range(name_token(tseg.node))[0] if name_token(tseg.node) else None)
            target = self.assignment_target(node)
            sink.creates.append(CreateCall(
                kind="factory", type=tref, target=target,
                target_base=re.split(r"[.\[]", target)[0] if target else None,
                inst_name=string_literal(args[0]) if args else None,
                name_expr=compact(args[0]) if args else None,
                parent_expr=compact(args[1]) if len(args) > 1 else None,
                method=ctx.method, conditional=ctx.conditional, condition=ctx.condition,
                in_loop=ctx.in_loop, range=r, nargs=len(args)))
            return

        # uvm_config_db #(T)::set/get(...)
        if segs[0].name in ("uvm_config_db", "uvm_resource_db") and len(segs) >= 2:
            db = segs[0].name
            type_text = ", ".join(segs[0].args)
            op = None
            if db == "uvm_config_db" and last in CONFIG_DB_OPS:
                op = last
                ctx_arg, inst_arg, field_arg = (args + [None] * 4)[:3]
                value = compact(args[3]) if len(args) > 3 else ""
            elif db == "uvm_resource_db" and last in RESOURCE_DB_OPS:
                op = RESOURCE_DB_OPS[last]
                ctx_arg = None
                inst_arg, field_arg = (args + [None] * 3)[:2]
                value = compact(args[2]) if len(args) > 2 else ""
            if op:
                inst_lit = string_literal(inst_arg)
                field_lit = string_literal(field_arg)
                sink.config_db.append(ConfigDbCall(
                    op=op, db=db, type_text=type_text,
                    context=compact(ctx_arg) if ctx_arg is not None else "",
                    inst=inst_lit if inst_lit is not None else (compact(inst_arg) if inst_arg is not None else ""),
                    inst_literal=inst_lit is not None,
                    field_name=field_lit if field_lit is not None else (compact(field_arg) if field_arg is not None else ""),
                    field_literal=field_lit is not None, value=value, owner=ctx.owner,
                    method=ctx.method, conditional=ctx.conditional, range=r,
                    optional=op == "get" and self.goes_on_without(node)))
            return

        # Factory overrides
        if last in ("set_type_override", "set_inst_override") and len(segs) >= 3 and segs[-2].name == "type_id":
            override = get_type_class(args[0]) if args else None
            if override:
                inst = string_literal(args[1]) if last == "set_inst_override" and len(args) > 1 else None
                sink.overrides.append(OverrideCall("type" if last == "set_type_override" else "inst",
                                                   segs[-3].name, override, inst, ctx.owner, ctx.method, r))
            return
        if last in ("set_type_override_by_type", "set_inst_override_by_type"):
            types = [get_type_class(a) for a in args]
            classes = [t for t in types if t]
            if len(classes) >= 2:
                strings = [string_literal(a) for a in args]
                inst = next((s for s in strings if s is not None), None)
                if inst is None and last == "set_inst_override_by_type":
                    inst = next((compact(a) for a, t in zip(args, types) if not t), None)
                sink.overrides.append(OverrideCall("type" if "type_override" in last else "inst",
                                                   classes[0], classes[1], inst, ctx.owner, ctx.method, r))
            return
        if last in ("set_type_override_by_name", "set_type_override", "set_inst_override_by_name",
                    "set_inst_override"):
            strings = [string_literal(a) for a in args]
            if last == "set_type_override_by_name" or last == "set_type_override":
                if len(strings) >= 2 and strings[0] and strings[1]:
                    sink.overrides.append(OverrideCall("type", strings[0], strings[1], None,
                                                       ctx.owner, ctx.method, r))
            elif last == "set_inst_override" and len(strings) >= 3 and all(strings[:3]):
                # component method: (relative_inst_path, original, override)
                sink.overrides.append(OverrideCall("inst", strings[1], strings[2], strings[0],
                                                   ctx.owner, ctx.method, r))
            elif last == "set_inst_override_by_name" and len(strings) >= 3 and all(strings[:3]):
                sink.overrides.append(OverrideCall("inst", strings[0], strings[1], strings[2],
                                                   ctx.owner, ctx.method, r))
            return

        # port.connect(export)
        if last == "connect" and len(segs) >= 2 and len(args) == 1 and segs[-1].sep == ".":
            sink.connects.append(ConnectCall(
                "connect", ".".join(s.name for s in segs[:-1]), compact(args[0]), ctx.owner,
                ctx.method, ctx.conditional, r))
            return

        # seq.start(sequencer, ...)
        if last == "start" and len(segs) >= 2 and args and segs[-1].sep == ".":
            sink.seq_starts.append(SeqStart(".".join(s.name for s in segs[:-1]), compact(args[0]),
                                            ctx.owner, ctx.method, r))
            return

        if last == "run_test" and len(segs) == 1:
            name = string_literal(args[0]) if args else None
            sink.run_tests.append((name or "", r))

    def goes_on_without(self, call) -> bool:
        """Whether code that reads a value carries on when it is not there: the result is ignored
        (void'(...), a call statement), or the `if` it is tested in does something other than report
        an error when it is missing (a default value, say)."""
        p = call.parent
        while p is not None and p.kind == K.ParenthesizedExpression:
            p = p.parent
        if p is None or p.kind in (K.VoidCastedCallStatement, K.ExpressionStatement):
            return p is not None
        negated = p.kind == K.UnaryLogicalNotExpression
        if negated:
            p = p.parent
            while p is not None and p.kind == K.ParenthesizedExpression:
                p = p.parent
        pred = p.parent if p is not None and p.kind == K.ConditionalPattern else None
        stmt = pred.parent if pred is not None else None
        if stmt is None or stmt.kind != K.ConditionalStatement or len(nodes(pred.conditions)) != 1:
            return False
        missing = stmt.statement if negated else (stmt.elseClause.clause if stmt.elseClause else None)
        return missing is None or not self.reports_failure(missing)

    def reports_failure(self, node) -> bool:
        """Whether a statement reports an error: $fatal, $error, uvm_report_fatal/_error, or a macro
        whose name says so (`uvm_fatal, `uvm_error, `DV_CHECK_FATAL, ...)."""
        sm = self.sm
        for tok in iter_tokens(node):
            if tok.kind in (TK.SystemIdentifier, TK.Identifier) and tok.valueText in FAILURE_CALLS:
                return True
            if sm.isMacroLoc(tok.location):
                name = self.macro_call(tok.location)[0]
                if name and FAILURE_RE.search(name):
                    return True
        return False

    def on_assign(self, node, ctx: BodyCtx, sink: Sink):
        rhs = unwrap(node.right)
        if rhs is None:
            return
        r = self.node_range(node)
        if r is None:
            return
        lhs_text = compact(node.left)
        if rhs.kind == K.NewClassExpression:
            sn = rhs.scopedNew
            tref = None
            if sn is not None and sn.kind == K.ScopedName:
                segs = flatten_name(sn)
                if len(segs) >= 2:
                    tseg = segs[-2]
                    tref = TypeRef(name=tseg.name, text=compact(tseg.node), args=tseg.args)
            args = []
            if rhs.argList is not None:
                args = [unwrap(a) for a in rhs.argList.parameters if isinstance(a, Node)]
            sink.creates.append(CreateCall(
                kind="new", type=tref, target=lhs_text, target_base=re.split(r"[.\[]", lhs_text)[0],
                inst_name=string_literal(args[0]) if args else None,
                name_expr=compact(args[0]) if args else None,
                parent_expr=compact(args[1]) if len(args) > 1 else None,
                method=ctx.method, conditional=ctx.conditional, condition=ctx.condition,
                in_loop=ctx.in_loop, range=r, nargs=len(args)))
        elif rhs.kind == K.ScopedName and node.left.kind == K.IdentifierName:
            segs = flatten_name(rhs)
            if all(s.sep in ("", ".") for s in segs) and len(segs) >= 2:
                sink.connects.append(ConnectCall("alias", lhs_text, compact(rhs), ctx.owner,
                                                 ctx.method, ctx.conditional, r))

    # -- whole-tree passes ---------------------------------------------------

    def collect_diagnostics(self):
        eng = ps.DiagnosticEngine(self.sm)
        eng.setWarningOptions(["default"])  # slang's default warning set, as its driver (and elab) use
        for d in self.tree.diagnostics:
            sev = eng.getSeverity(d.code, d.location)
            if sev == ps.DiagnosticSeverity.Ignored:
                continue
            loc, _ = self.file_loc(d.location)
            path = self.path_of(loc.buffer)
            if path is None:
                continue
            sm = self.sm
            line, col = sm.getLineNumber(loc) - 1, sm.getColumnNumber(loc) - 1
            end_line, end_col = line, col + 1
            for sr in d.ranges:
                s, _ = self.file_loc(sr.start)
                e, _ = self.file_loc(sr.end)
                if s.buffer == loc.buffer and e.buffer == loc.buffer and s.offset <= loc.offset <= e.offset:
                    end_line, end_col = sm.getLineNumber(e) - 1, sm.getColumnNumber(e) - 1
                    break
            code = str(d.code).replace("DiagCode(", "").rstrip(")")
            severity = {ps.DiagnosticSeverity.Error: "error", ps.DiagnosticSeverity.Fatal: "error",
                        ps.DiagnosticSeverity.Warning: "warning"}.get(sev, "info")
            msg = slang_text(eng.formatMessage, d)
            if code in ("UnknownDirective", "CouldNotOpenIncludeFile"):
                severity = "warning"
                msg += " (add its directory to uvm.includeDirs or a filelist)"
            self.out.diagnostics.append(Diag(Range(path, line, col, end_line, end_col), msg, severity, code,
                                             data=fix_data(code, d)))

    def collect_includes(self):
        earlier: dict[str, str] = {}
        for inc in self.tree.getIncludeDirectives():
            name = slang_text(getattr, inc, "path")
            # A header skipped by its include guard has no buffer: it is the one included before.
            target = self.path_of(inc.buffer.id) if inc.buffer else earlier.get(name)
            syn = inc.syntax
            if target is None or syn is None:
                continue
            earlier.setdefault(name, target)
            r = self.node_range(syn)
            if r is None:
                continue
            self.out.files.add(target)
            self.out.includes.setdefault(r.path, []).append((target, r))

    def in_library(self, path: str) -> bool:
        """A file of the UVM library that a user root reaches (its own root indexes it)."""
        return path != self.root and path.startswith(self.library_dirs)

    def collect_macros(self):
        """Every file's `define macros, and its lint ignore directives, from its text (an open
        editor's, else the disk's)."""
        for path in sorted(self.out.files):
            if not self.library and self.in_library(path):
                continue  # the library's own root records those
            text = self.overrides.get(path)
            if text is None:
                try:
                    with open(path, encoding="utf-8-sig", errors="replace") as f:
                        text = f.read()
                except OSError:
                    continue
            if not self.library:
                self.collect_ignores(path, text)
            line, pos = 0, 0
            branches: list[tuple[list, list]] = []  # per open `ifdef: (tested, what this branch needs)
            for m in MACRO_DIRECTIVE_RE.finditer(text):
                line += text.count("\n", pos, m.start())
                pos = m.start()
                if _continued(text, pos):
                    continue  # inside a multi-line `define
                cond, arg = m.group("cond"), (m.group("cond"), m.group("arg"))
                if cond is None:
                    name, params = m.group("name"), m.group("params")
                    col = m.start("name") - pos
                    self.out.macros.append(MacroDef(
                        name, params[1:-1] if params else None, Range(path, line, col, line, col + len(name)),
                        m.group(0).strip(), tuple(c for _, needs in branches for c in needs)))
                elif cond in NEGATED:
                    branches.append(([arg], [arg]))
                elif branches and cond == "elsif":
                    tested = branches[-1][0] + [("ifdef", arg[1])]
                    branches[-1] = (tested, [(NEGATED[k], n) for k, n in tested[:-1]] + tested[-1:])
                elif branches and cond == "else":
                    branches[-1] = (branches[-1][0], [(NEGATED[k], n) for k, n in branches[-1][0]])
                elif branches and cond == "endif":
                    branches.pop()

    def collect_ignores(self, path: str, text: str):
        lines: dict[int, list[str]] = {}
        for d in ignore_directives(text):
            if d.target is not None:
                codes = lines.setdefault(d.target, [])
                codes += [c for c in d.codes if c not in codes]
        if lines:
            self.out.ignores[path] = lines

    def collect_identifiers(self):
        ids = self.out.identifiers
        sm = self.sm
        seen = set()
        for tok in iter_tokens(self.tree.root):
            if tok.kind != TK.Identifier:
                continue
            loc = tok.location
            if sm.isMacroLoc(loc):
                if not sm.isMacroArgLoc(loc):
                    continue
                loc = sm.getFullyOriginalLoc(loc)
                if sm.isMacroLoc(loc):
                    continue
            path = self.path_of(loc.buffer)
            if path is None or self.in_library(path):
                continue
            key = (loc.buffer.id, loc.offset)
            if key in seen:
                continue
            seen.add(key)
            line, col = sm.getLineNumber(loc) - 1, sm.getColumnNumber(loc) - 1
            name = tok.valueText
            ids.setdefault(name, []).append(Range(path, line, col, line, col + len(name)))
