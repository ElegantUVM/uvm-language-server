"""Plain data produced by the indexer and consumed by the semantic layer.

Everything here is derived from a single parse of one root file (plus the files
it includes) and holds no references to pyslang objects, so it can be cached,
compared and serialised freely.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def utf8_prefix(line: str, col: int) -> str:
    """The text before UTF-8 byte column `col` (slang's unit), never splitting a character."""
    return line.encode("utf-8")[:col].decode("utf-8", errors="replace")


@dataclass(frozen=True)
class Range:
    """A span in a file. Lines and columns are 0-based, like LSP."""

    path: str
    line: int
    col: int
    end_line: int
    end_col: int

    def contains(self, line: int, col: int) -> bool:
        if line < self.line or line > self.end_line:
            return False
        if line == self.line and col < self.col:
            return False
        if line == self.end_line and col > self.end_col:
            return False
        return True

    def to_json(self) -> dict:
        return {"file": self.path, "line": self.line, "col": self.col,
                "endLine": self.end_line, "endCol": self.end_col}


@dataclass
class TypeRef:
    """A reference to a named type, e.g. `pkg::uvm_driver #(alu_item)`."""

    name: str
    text: str
    scope: str | None = None
    args: list[str] = field(default_factory=list)
    range: Range | None = None


@dataclass
class Variable:
    """A class property, method argument, local variable or module signal."""

    name: str
    kind: str  # property | argument | local | port | signal
    type_text: str
    type: TypeRef | None
    range: Range
    decl_text: str
    qualifiers: list[str] = field(default_factory=list)
    generated: bool = False  # produced by a macro expansion (e.g. p_sequencer)
    doc: str = ""
    direction: str = ""  # explicit or inherited formal direction
    dimensions: str = ""  # unpacked formal dimensions
    default: str | None = None  # an argument or parameter's default value


@dataclass
class Method:
    name: str
    kind: str  # function | task
    return_type: str
    signature: str
    range: Range
    decl_range: Range
    args: list[Variable] = field(default_factory=list)
    locals: list[Variable] = field(default_factory=list)
    scope_identifiers: set[str] | None = None  # possible type shadows; None when names may be implicit
    qualifiers: list[str] = field(default_factory=list)
    is_prototype: bool = False  # extern / pure virtual declaration without a body
    non_ansi: bool = False  # arguments declared in the body cannot have default values
    generated: bool = False
    doc: str = ""
    # Where a body is, for edits; empty for one a macro writes.
    body_items: list[Range] = field(default_factory=list)  # top-level statements, declarations left out
    super_calls: list[str] = field(default_factory=list)  # super.X(...) -> 'X', C::X(...) -> 'C::X'
    forwarded_phase: str | None = None  # a body that only forwards its phase argument: 'super' or a base name
    calls: list[tuple[str, bool]] | None = None  # (name, explicit call); None when not fully visible

    @property
    def first_stmt(self) -> Range | None:
        return self.body_items[0] if self.body_items else None


@dataclass
class Typedef:
    name: str
    target_text: str
    target: TypeRef | None
    range: Range
    decl_text: str
    package: str | None = None
    owner_class: str | None = None
    imports: list[str] = field(default_factory=list)


@dataclass
class CreateCall:
    """`x = T::type_id::create("x", this)` or `x = new(...)`."""

    kind: str  # factory | new
    type: TypeRef | None  # None for a bare `new` whose type comes from the target
    target: str | None  # full left-hand side text
    target_base: str | None  # first identifier of the left-hand side
    inst_name: str | None  # string literal passed as the name, when it is a literal
    name_expr: str | None
    parent_expr: str | None
    method: str
    conditional: bool
    condition: str | None
    in_loop: bool
    range: Range
    nargs: int = 0


@dataclass(slots=True)
class CallSite:
    """Source-spelled invocation context; declarations still own the signature."""

    head: Range
    segments: tuple[tuple[str, tuple[str, ...], str], ...]
    opening: Range
    closing: Range | None
    boundary: Range | None  # the enclosing statement of a call missing its `)`
    target: str | None = None  # assignment target of a bare new
    commas: tuple[tuple[int, int], ...] = ()  # where each top-level comma ends
    names: tuple[str | None, ...] = ()  # each argument's name, when any is named


@dataclass
class ConfigDbCall:
    op: str  # set | get | exists | wait_modified
    db: str  # uvm_config_db | uvm_resource_db
    type_text: str
    context: str
    inst: str
    inst_literal: bool
    field_name: str
    field_literal: bool
    value: str
    owner: str
    method: str
    conditional: bool
    range: Range
    optional: bool = False  # a get the code goes on without: void'(get(...)), if (!get(...)) x = default;
    field_range: Range | None = None
    method_range: Range | None = None


@dataclass
class OverrideCall:
    kind: str  # type | inst
    original: str
    override: str
    inst_path: str | None
    owner: str
    method: str
    range: Range


@dataclass
class ConnectCall:
    """`a.b.port.connect(c.export)`, or a port alias `ap = m_monitor.ap`."""

    kind: str  # connect | alias
    source: str
    target: str
    owner: str
    method: str
    conditional: bool
    range: Range


@dataclass
class SeqStart:
    """`seq.start(sequencer)` - the receiver may or may not be a sequence."""

    receiver: str
    sequencer: str
    owner: str
    method: str
    range: Range


@dataclass
class Param:
    name: str
    default: str
    is_type: bool
    is_local: bool = False  # a localparam cannot be assigned in #(...)
    type_text: str = ""  # a value parameter's declared type


@dataclass
class ClassInfo:
    name: str
    package: str | None
    kind: str  # class | interface class
    range: Range  # the name token
    decl_range: Range  # whole declaration
    is_virtual: bool = False
    params: list[Param] = field(default_factory=list)
    base: TypeRef | None = None
    implements: list[TypeRef] = field(default_factory=list)
    properties: list[Variable] = field(default_factory=list)
    methods: list[Method] = field(default_factory=list)
    typedefs: list[Typedef] = field(default_factory=list)
    constraints: list[tuple[str, Range]] = field(default_factory=list)
    covergroups: list[tuple[str, Range]] = field(default_factory=list)
    registration: str | None = None  # component | object (factory registration)
    creates: list[CreateCall] = field(default_factory=list)
    config_db: list[ConfigDbCall] = field(default_factory=list)
    overrides: list[OverrideCall] = field(default_factory=list)
    connects: list[ConnectCall] = field(default_factory=list)
    seq_starts: list[SeqStart] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    outer_class: str | None = None
    library: bool = False
    doc: str = ""
    extends_args: str | None = None  # `extends base(args)`
    # Where the class is, for edits; None when a macro wrote that part.
    header_end: Range | None = None  # the `;` that ends the header
    end_kw: Range | None = None  # endclass
    registration_macro: str | None = None  # the macro written in the class that declares type_id
    registration_range: Range | None = None  # that macro call, `uvm_component_utils(T)
    registration_arg: str | None = None  # the class it registers, as the macro was given it

    @property
    def qualname(self) -> str:
        prefix = f"{self.package}::" if self.package else ""
        if self.outer_class:
            prefix += f"{self.outer_class}::"
        return prefix + self.name

    @property
    def path(self) -> str:
        return self.range.path


@dataclass
class OutOfBlockMethod:
    """A method body defined outside its class: `task cls::run_phase(...)`."""

    class_name: str
    package: str | None
    method: Method
    creates: list[CreateCall] = field(default_factory=list)
    config_db: list[ConfigDbCall] = field(default_factory=list)
    overrides: list[OverrideCall] = field(default_factory=list)
    connects: list[ConnectCall] = field(default_factory=list)
    seq_starts: list[SeqStart] = field(default_factory=list)


@dataclass
class Instance:
    module: str
    name: str
    range: Range


@dataclass
class DesignUnit:
    """A module, interface, program or package."""

    name: str
    kind: str  # module | interface | program | package
    range: Range
    decl_range: Range
    params: list[Param] = field(default_factory=list)
    ports: list[Variable] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    methods: list[Method] = field(default_factory=list)
    instances: list[Instance] = field(default_factory=list)
    config_db: list[ConfigDbCall] = field(default_factory=list)
    run_tests: list[tuple[str, Range]] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    unit_imports: list[str] = field(default_factory=list)  # the $unit ones before it, which start imports
    exports: list[str] = field(default_factory=list)  # re-exported packages; * for export *::*
    library: bool = False
    doc: str = ""


@dataclass
class MacroDef:
    name: str
    params: str | None
    range: Range
    text: str
    requires: tuple[tuple[str, str], ...] = ()  # the `ifdef/`ifndef NAME branches it is inside


@dataclass
class Diag:
    range: Range
    message: str
    severity: str  # error | warning | info | hint
    code: str
    source: str = "slang"
    related: list[tuple[Range, str]] = field(default_factory=list)  # slang notes
    data: dict | None = None  # what a quick fix needs; sent to the client with the diagnostic


@dataclass(frozen=True, slots=True)
class StructuralPair:
    opening: Range
    closing: Range


@dataclass
class FileIndex:
    """Everything extracted from one root file and the files it includes."""

    root: str
    files: set[str] = field(default_factory=set)
    texts: dict[str, str] = field(default_factory=dict)  # indexed user text, for edit anchors
    classes: list[ClassInfo] = field(default_factory=list)
    typedefs: list[Typedef] = field(default_factory=list)
    units: list[DesignUnit] = field(default_factory=list)
    out_of_block: list[OutOfBlockMethod] = field(default_factory=list)
    macros: list[MacroDef] = field(default_factory=list)
    diagnostics: list[Diag] = field(default_factory=list)
    includes: dict[str, list[tuple[str, Range]]] = field(default_factory=dict)
    identifiers: dict[str, list[Range]] = field(default_factory=dict)
    structures: list[StructuralPair] = field(default_factory=list)
    invocations: list[CallSite] = field(default_factory=list)
    directives: list[Range] = field(default_factory=list)  # includes disabled preprocessor text
    structures_pending: bool = False  # a library root's structures, collected on first use
    named_argument_labels: set[Range] = field(default_factory=set)
    ignores: dict[str, dict[int, list[str]]] = field(default_factory=dict)  # file -> line -> lint codes ignored there
    library: bool = False
