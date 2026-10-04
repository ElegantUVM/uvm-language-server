"""Turn slang syntax trees into the plain data in model.py.

The indexer works on syntax, not on an elaborated design. That keeps it fast
(the whole UVM library parses and indexes in well under a second), lets it work
on code that does not compile yet, and means a change to one file only needs
that file's root to be re-parsed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace

import pyslang as ps
from pyslang import parsing as P
from pyslang import syntax as S

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
DEFINE_RE = re.compile(r"^[ \t]*`define[ \t]+([A-Za-z_]\w*)(\([^)]*\))?(.*)$", re.MULTILINE)
QUALIFIER_TOKENS = {
    TK.RandKeyword: "rand", TK.RandCKeyword: "randc", TK.StaticKeyword: "static",
    TK.LocalKeyword: "local", TK.ProtectedKeyword: "protected", TK.ConstKeyword: "const",
    TK.VirtualKeyword: "virtual", TK.PureKeyword: "pure", TK.ExternKeyword: "extern",
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


def _shim_text() -> str:
    with open(SHIM_FILE, encoding="utf-8") as f:
        return f.read()


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
    lines = []
    for c in comments:
        c = c.strip()
        if c.startswith("//"):
            lines.append(c[2:].lstrip("/").strip())
        elif c.startswith("/*"):
            body = c[2:-2] if c.endswith("*/") else c[2:]
            body = body.lstrip("*")
            for ln in body.splitlines():
                lines.append(ln.strip().lstrip("*").strip())
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


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


# ----------------------------------------------------------------------------


class Indexer:
    def __init__(self, include_dirs: list[str], defines: dict[str, str], use_shim: bool):
        self.include_dirs = list(include_dirs)
        self.defines = dict(defines)
        self.use_shim = use_shim
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
        po.predefines = [f"{k}={v}" if v != "" else k for k, v in self.defines.items()]
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
        return Extractor(tree, sm, root, library, overrides).run()


class Extractor:
    def __init__(self, tree, sm, root: str, library: bool, overrides: dict[str, str]):
        self.tree = tree
        self.sm = sm
        self.root = root
        self.library = library
        self.overrides = overrides
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

    def doc_of(self, node) -> str:
        first = node.getFirstToken()
        if first is None:
            return ""
        comments: list[str] = []
        newlines = 0
        for tr in reversed(list(first.trivia)):
            k = tr.kind
            if k in (P.TriviaKind.LineComment, P.TriviaKind.BlockComment):
                if newlines > 1 and comments:
                    break
                if newlines > 1 and not comments:
                    break
                comments.insert(0, slang_text(tr.getRawText))
                newlines = 0
            elif k == P.TriviaKind.EndOfLine:
                newlines += 1
            elif k == P.TriviaKind.Whitespace:
                continue
            else:
                break
        return clean_doc(comments)

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
        for m in nodes(self.tree.root.members):
            self.unit_member(m, None, imports, None)
        self.collect_diagnostics()
        self.collect_includes()
        self.collect_macros()
        if not self.library:
            self.collect_identifiers()
        return self.out

    def unit_member(self, m, package: str | None, imports: list[str], unit: DesignUnit | None):
        k = m.kind
        if k == K.PackageDeclaration:
            self.design_unit(m, "package")
        elif k in (K.ModuleDeclaration, K.InterfaceDeclaration, K.ProgramDeclaration):
            kind = {K.ModuleDeclaration: "module", K.InterfaceDeclaration: "interface",
                    K.ProgramDeclaration: "program"}[k]
            self.design_unit(m, kind)
        elif k == K.ClassDeclaration:
            self.class_decl(m, package, list(imports), None)
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
                          doc=self.doc_of(m))
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
                self.unit_member(item, package, imports, unit)
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

    def params_from_decl(self, decl) -> list[Param]:
        out = []
        if decl is None:
            return out
        if decl.kind == K.TypeParameterDeclaration:
            for d in nodes(decl.declarators):
                default = compact(d.assignment.type) if d.assignment is not None else ""
                out.append(Param(d.name.valueText, default, True))
        elif decl.kind == K.ParameterDeclaration:
            for d in nodes(decl.declarators):
                default = compact(d.initializer.expr) if d.initializer is not None else ""
                out.append(Param(d.name.valueText, default, False))
        return out

    def params(self, plist) -> list[Param]:
        out: list[Param] = []
        if plist is None:
            return out
        for decl in nodes(plist.declarations):
            out += self.params_from_decl(decl)
        return out

    # -- classes -------------------------------------------------------------

    def class_decl(self, n, package: str | None, imports: list[str], outer: str | None):
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
            library=self.library, doc=self.doc_of(n),
        )
        if n.extendsClause is not None:
            cls.base = self.type_ref_from_name(n.extendsClause.baseName)
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
            meth, sink = self.method_with_body(item.declaration, self.qualifiers(item.qualifiers), cls.name)
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
                                               None, r, text, ["localparam"], gen))

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

    def prototype(self, proto, quals: list[str], decl_node) -> Method | None:
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
                        args.append(v)
        return Method(name=tok.valueText, kind=kind, return_type=ret, signature=pretty(proto),
                      range=r, decl_range=self.node_range(decl_node) or r, args=args,
                      qualifiers=quals, is_prototype=True, generated=gen, doc=self.doc_of(decl_node))

    def method(self, decl, quals, owner) -> Method | None:
        meth, _ = self.method_with_body(decl, quals, owner)
        return meth

    def method_with_body(self, decl, quals, owner) -> tuple[Method | None, Sink]:
        sink = Sink()
        if decl.kind not in FUNC_DECLS:
            return None, sink
        meth = self.prototype(decl.prototype, quals, decl)
        if meth is None:
            return None, sink
        meth.is_prototype = False
        if not self.library:
            ctx = BodyCtx(owner, meth.name)
            for st in decl.items:
                self.walk(st, ctx, sink)
            meth.locals = sink.locals
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
                in_loop=ctx.in_loop, range=r))
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
                    method=ctx.method, conditional=ctx.conditional, range=r))
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
                in_loop=ctx.in_loop, range=r))
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
            self.out.diagnostics.append(Diag(Range(path, line, col, end_line, end_col), msg, severity, code))

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

    def collect_macros(self):
        for path in sorted(self.out.files):
            text = self.overrides.get(path)
            if text is None:
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except OSError:
                    continue
            for m in DEFINE_RE.finditer(text):
                line = text.count("\n", 0, m.start())
                col = m.start(1) - (text.rfind("\n", 0, m.start(1)) + 1)
                self.out.macros.append(MacroDef(
                    m.group(1), m.group(2)[1:-1] if m.group(2) else None,
                    Range(path, line, col, line, col + len(m.group(1))),
                    m.group(0).strip()))

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
            if path is None:
                continue
            key = (loc.buffer.id, loc.offset)
            if key in seen:
                continue
            seen.add(key)
            line, col = sm.getLineNumber(loc) - 1, sm.getColumnNumber(loc) - 1
            name = tok.valueText
            ids.setdefault(name, []).append(Range(path, line, col, line, col + len(name)))
