"""The semantic layer: cross-file resolution on top of per-root FileIndex data."""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field

from .indexer import EOL_RE, Indexer, argument_spans, mask
from .model import (
    ClassInfo, ConfigDbCall, ConnectCall, CreateCall, DesignUnit, Diag, FileIndex, MacroDef,
    Method, OverrideCall, Range, SeqStart, Typedef, TypeRef, Variable,
)
from .project import ProjectConfig, SourceSet, build_source_set, is_bundled, is_sv_file, norm
from .uvm import (
    BUILTIN_CLASSES, BUILTIN_MEMBERS, COMPONENT_ROLES, ROLE_OF_BASE, builtin_params,
    looks_like_config,
)

BUILTIN_TYPES = {"string", "bit", "logic", "reg", "int", "integer", "byte", "shortint", "longint", "time",
                 "real", "realtime", "shortreal", "chandle", "event", "void"}
TYPE_HEAD_RE = re.compile(r"^\s*(?:(\w+)\s*::\s*)?(\w+)\s*(#\s*\((.*)\))?\s*$", re.DOTALL)


@dataclass
class Spec:
    """A resolved class type: an indexed class or a built-in UVM class name."""

    name: str
    cls: ClassInfo | None = None
    args: list[str] = field(default_factory=list)
    via_typedef: Typedef | None = None

    @property
    def key(self) -> str:
        return self.cls.qualname if self.cls else self.name


@dataclass
class ChainEntry:
    spec: Spec
    bindings: dict[str, str]  # type parameter -> argument text for this level


@dataclass(frozen=True)
class Scope:
    """Where a type is written, which decides what its names refer to."""

    ctx: ClassInfo | None = field(default=None, compare=False)
    package: str | None = None
    imports: tuple[str, ...] = ()
    near: Range | None = None
    qualname: str | None = None  # ctx's, which tells scopes apart

    @classmethod
    def of(cls, ctx: ClassInfo | None, package=None, imports=None, near=None) -> "Scope":
        return cls(ctx, package if package is not None else ctx.package if ctx else None,
                   tuple(imports if imports is not None else ctx.imports if ctx else ()),
                   near or (ctx.range if ctx else None), ctx.qualname if ctx else None)


class Bound(str):
    """A type argument's text and the scope that wrote it, where its names resolve: a
    specialization's arguments mean what they meant where it was written, not in the class.
    `formal` is how a generic class's own body writes it when that names the class's parameters,
    which no specialization binds there: `T` for T's default, `holder #(T)` for holder #(int)."""

    def __new__(cls, text: str, scope: Scope, spec: Spec | None = None, formal: str | None = None):
        out = super().__new__(cls, text)
        out.scope, out.spec, out.formal = scope, spec, formal
        return out

    # Immutable, and its scope holds a class: copies share it.
    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def __reduce__(self):
        return Bound, (str(self), self.scope, self.spec, self.formal)


def _carry(value: str, source: str) -> str:
    """`value`, derived from `source`, resolving where `source` was written."""
    return value if isinstance(value, Bound) or not isinstance(source, Bound) else Bound(value, source.scope)


def _arg_key(arg: str):
    """What a type argument means: its text, scope and display, or the type it already names."""
    if not isinstance(arg, Bound):
        return arg
    spec = arg.spec
    return arg, arg.scope, arg.formal, spec and (spec.key, tuple(map(_arg_key, spec.args)))


def _bind(text: str, bindings: dict[str, str]) -> str:
    """`text` with `bindings` substituted, resolving where `text` was written, and keeping how a
    generic class's body writes it when the bindings are its own parameters."""
    value = substitute_params(text, bindings)
    if isinstance(value, Bound) or not isinstance(text, Bound):
        return value
    shown = value
    if any(getattr(v, "formal", None) for v in bindings.values()):
        shown = substitute_params(text, {k: getattr(v, "formal", None) or v for k, v in bindings.items()})
    return Bound(value, text.scope, formal=shown if shown != value else None)


@dataclass
class CallSet:
    creates: list[CreateCall] = field(default_factory=list)
    config_db: list[ConfigDbCall] = field(default_factory=list)
    overrides: list[OverrideCall] = field(default_factory=list)
    connects: list[ConnectCall] = field(default_factory=list)
    seq_starts: list[SeqStart] = field(default_factory=list)


def split_args(text: str) -> list[str]:
    out = [text[a:b].strip() for a, b in argument_spans(mask(text), 0, len(text))]
    if out and not out[-1]:
        out.pop()
    return out


def parse_type_text(text: str) -> tuple[str | None, str, list[str]] | None:
    """'pkg::cls #(a, b)' -> ('pkg', 'cls', ['a', 'b'])."""
    text = re.sub(r"^\s*virtual\s+", "", text or "")
    m = TYPE_HEAD_RE.match(text)
    if not m:
        return None
    return m.group(1), m.group(2), split_args(m.group(4)) if m.group(4) else []


def substitute_params(text: str, bindings: dict[str, str]) -> str:
    if not bindings or text in bindings:
        return bindings.get(text, text)
    previous = ""

    def replace(m):
        nonlocal previous
        token = m.group()
        # Qualified names, named selectors and literal contents belong to other scopes.
        value = bindings.get(token, token) if previous not in (".", "::", "'") else token
        previous = token
        return value

    return re.sub(r'"(?:\\.|[^"\\])*"|\\\S+|::|[A-Za-z_$][\w$]*|\S', replace, text)


def bind_params(params: list[tuple[str, str]], args: list[str], outer: dict[str, str],
                bind=None, scope: Scope | None = None) -> dict[str, str]:
    """Map a class's parameters to the arguments of a specialization. `bind` puts bindings into
    an argument (as text by default); `scope` is where the class writes its defaults."""
    bind = bind or _bind
    out: dict[str, str] = {}
    positional = [a for a in args if not a.startswith(".")]
    named = {}
    for a in args:
        m = re.match(r"^\.(\w+)\((.*)\)$", a)
        if m:
            named[m.group(1)] = _carry(m.group(2), a)
    for i, (pname, default) in enumerate(params):
        if pname in named:
            val = bind(named[pname], outer)
        elif i < len(positional):
            val = bind(positional[i], outer)
        else:
            val = bind(Bound(default, scope), out) if scope else substitute_params(default, out)
        out[pname] = val
    return out


def _unseen(known: dict, by_file: dict, path: str, key, key_of) -> bool:
    """Whether a record of `path` with this key is not in `by_file` yet (and is now)."""
    seen = known.get((id(by_file), path))
    if seen is None:
        seen = known[(id(by_file), path)] = {key_of(r) for r in by_file.get(path, ())}
    if key in seen:
        return False
    seen.add(key)
    return True


def _never(cond: tuple[str, str], given: set[str], known: set[str]) -> bool:
    """Whether an `ifdef branch condition is false whatever order files are read in: `ifndef of a
    macro the configuration defines, or `ifdef of one nothing defines."""
    kind, name = cond
    if not name:
        return False  # an expression
    return name in given if kind == "ifndef" else name not in known


def _same_text(path: str, a: str | None, b: str | None) -> bool:
    """Whether two versions of a file are equal; None stands for the file on disk."""
    if a is None and b is None:
        return False  # changed on disk
    if a is None or b is None:
        try:
            with open(path, encoding="utf-8", errors="replace", newline="") as f:
                disk = f.read()
        except OSError:
            return False
        a, b = (disk if a is None else a), (disk if b is None else b)
    return a == b


class Workspace:
    def __init__(self):
        self.lock = threading.RLock()
        self.config = ProjectConfig()
        self.sources: SourceSet | None = None
        self.indexer: Indexer | None = None
        self.indices: dict[str, FileIndex] = {}
        self.ephemeral: set[str] = set()
        self.overrides: dict[str, str] = {}
        self.version = 0
        self.last_index_seconds = 0.0
        self._include_candidates: tuple[tuple, dict[str, list[str]],
                                         dict[str, tuple[int, int, int] | None]] | None = None
        self._chaining: set[int] = set()  # classes whose ancestors are being found
        # library_files by resolved path, for formatting outside the lock: replaced, never updated.
        self.library_real: frozenset[str] = frozenset()
        self._library_real_of: frozenset[str] | None = None
        self._clear_maps()

    # -- building ------------------------------------------------------------

    def _clear_maps(self):
        self._config_db_report = None
        self._effective_type_cache: dict = {}
        self._inlay_hint_cache: dict = {}
        self._bound_member_hints: dict = {}
        self.creates_by_file: dict[str, list[CreateCall]] = {}
        self.invocations_by_file: dict = {}
        self.directives_by_file: dict = {}
        self._signature_texts: dict = {}
        self._signature_cache: dict = {}
        self._highlight_cache: dict = {}
        self._folding_cache: dict = {}
        self.structures_by_file: dict = {}
        self.folds_by_file: dict = {}
        self.identifiers_by_file: dict[str, list[tuple[str, Range]]] = {}
        self.texts: dict[str, str] = {}
        self.lines: dict[str, list[str]] = {}
        self.classes: list[ClassInfo] = []
        self.classes_by_name: dict[str, list[ClassInfo]] = {}
        self.class_by_qual: dict[str, ClassInfo] = {}
        self.typedefs_by_name: dict[str, list[Typedef]] = {}
        self.units_by_name: dict[str, list[DesignUnit]] = {}
        self.units: list[DesignUnit] = []
        self.macros_by_name: dict[str, list[MacroDef]] = {}
        self.calls: dict[str, CallSet] = {}
        self.impls: dict[tuple[str, str], Method] = {}
        self.subclasses: dict[str, list[ClassInfo]] = {}
        self.by_path: dict[str, list] = {}
        self.library_files: set[str] = set()
        self.ignores: dict[str, dict[int, list[str]]] = {}  # file -> line -> lint codes ignored there
        self._role_cache: dict[str, str] = {}
        self._spec_cache: dict = {}
        self._formals: dict = {}
        self._visible_packages: dict = {}
        self._roots_by_file: dict[str, set[str]] | None = None
        self._include_sites: dict[str, list[Range]] | None = None
        self._including: dict[tuple[str, bool], DesignUnit | None] = {}

    def configure(self, config: ProjectConfig):
        with self.lock:
            self.config = config
            self.reindex_all()

    def reindex_all(self):
        with self.lock:
            self._include_candidates = None
            self._library_real_of = None  # links may have changed
            t0 = time.time()
            self.sources = build_source_set(self.config)
            s = self.sources
            self.indexer = Indexer(s.include_dirs, s.defines, use_shim=not s.has_real_uvm_macros,
                                   library_dirs=sorted({os.path.dirname(r) for r in s.library_roots}),
                                   library_guard=s.uvm_guard)
            self.indices = {}
            for root in self.sources.roots:
                self.indices[root] = self.indexer.parse(
                    root, self.overrides, library=root in self.sources.library_roots)
            self.ephemeral = set()
            for path in self.overrides:
                if not self.roots_for(path) and is_sv_file(path) and not is_bundled(path):
                    self.indices[path] = self.indexer.parse(path, self.overrides)
                    self.ephemeral.add(path)
            self._rebuild()
            self.last_index_seconds = time.time() - t0

    def lines_of(self, path: str) -> list[str]:
        """The indexed text of `path` split as slang counts lines (cached until the next rebuild)."""
        if path not in self.lines:
            self.lines[path] = EOL_RE.split(self.texts.get(path, ""))
        return self.lines[path]

    def roots_for(self, path: str) -> list[str]:
        return [r for r, idx in self.indices.items() if path in idx.files]

    def update_document(self, path: str, text: str | None) -> list[str]:
        """Re-parse whatever depends on `path`. Returns the affected roots."""
        path = norm(path)
        with self.lock:
            if self.indexer is None:
                self.reindex_all()
            before = self.overrides.get(path)
            if text is None:
                self.overrides.pop(path, None)
            else:
                self.overrides[path] = text
            roots = self.roots_for(path)
            if path in self.library_files and _same_text(path, before, text):
                return roots  # a library file opened or closed unchanged: skip re-parsing the library
            if not roots and is_sv_file(path) and not is_bundled(path):
                if text is None:
                    if path in self.ephemeral:
                        self.indices.pop(path, None)
                        self.ephemeral.discard(path)
                    self._rebuild()
                    return []
                roots = [path]
                self.ephemeral.add(path)
            for root in roots:
                lib = self.sources is not None and root in self.sources.library_roots
                self.indices[root] = self.indexer.parse(root, self.overrides, library=lib)
            self._rebuild()
            return roots

    def _rebuild(self):
        self._clear_maps()
        self.version += 1
        seen_decl: set[tuple[str, int, str]] = set()
        seen_ids: set[Range] = set()
        # A file's structures, calls and directives: the first root that reaches it adds its own;
        # another root (a shared header) adds only those not seen yet.
        owners: dict[str, str] = {}
        known: dict = {}
        for root, idx in self.indices.items():
            self.texts.update(idx.texts)
            for pair in idx.structures:
                path = pair.opening.path
                if owners.setdefault(path, root) == root or \
                        _unseen(known, self.structures_by_file, path, pair, lambda p: p):
                    self.structures_by_file.setdefault(path, []).append(pair)
            for fold in idx.folds:
                if owners.setdefault(fold.path, root) == root or \
                        _unseen(known, self.folds_by_file, fold.path, fold, lambda f: f):
                    self.folds_by_file.setdefault(fold.path, []).append(fold)
            for call in idx.invocations:
                path = call.head.path
                if owners.setdefault(path, root) == root or \
                        _unseen(known, self.invocations_by_file, path, call.opening, lambda c: c.opening):
                    self.invocations_by_file.setdefault(path, []).append(call)
            for rng in idx.directives:
                if owners.setdefault(rng.path, root) == root or \
                        _unseen(known, self.directives_by_file, rng.path, rng, lambda r: r):
                    self.directives_by_file.setdefault(rng.path, []).append(rng)
            if idx.library:
                self.library_files |= idx.files
            else:
                for name, ranges in idx.identifiers.items():
                    for rng in ranges:
                        # Named labels belong to the callee's formals, not the caller's members.
                        if rng in idx.named_argument_labels:
                            continue
                        if rng not in seen_ids:
                            seen_ids.add(rng)
                            self.identifiers_by_file.setdefault(rng.path, []).append((name, rng))
            for cls in idx.classes:
                key = (cls.range.path, cls.range.line, cls.name)
                if key in seen_decl:
                    continue  # the same file reached from two roots
                seen_decl.add(key)
                self.classes.append(cls)
                self.classes_by_name.setdefault(cls.name, []).append(cls)
                self.class_by_qual.setdefault(cls.qualname, cls)
                self.by_path.setdefault(cls.range.path, []).append(cls)
                self.calls[cls.qualname] = CallSet(list(cls.creates), list(cls.config_db),
                                                   list(cls.overrides), list(cls.connects),
                                                   list(cls.seq_starts))
            for td in idx.typedefs:
                key = (td.range.path, td.range.line, td.name)
                if key in seen_decl:
                    continue
                seen_decl.add(key)
                self.typedefs_by_name.setdefault(td.name, []).append(td)
                self.by_path.setdefault(td.range.path, []).append(td)
            for unit in idx.units:
                key = (unit.range.path, unit.range.line, unit.name)
                if key in seen_decl:
                    continue
                seen_decl.add(key)
                self.units.append(unit)
                self.units_by_name.setdefault(unit.name, []).append(unit)
                self.by_path.setdefault(unit.range.path, []).append(unit)
            for m in idx.macros:
                lst = self.macros_by_name.setdefault(m.name, [])
                if not any(x.range == m.range for x in lst):
                    lst.append(m)
            for path, lines in idx.ignores.items():
                self.ignores.setdefault(path, lines)  # a file two roots include: the same text
        for identifiers in self.identifiers_by_file.values():
            identifiers.sort(key=lambda pair: (pair[1].line, pair[1].col))
        files = frozenset(self.library_files)
        if files != self._library_real_of:
            self.library_real, self._library_real_of = frozenset(map(os.path.realpath, files)), files
        # A definition in an `ifdef branch that cannot be in effect (UVM's empty stubs under
        # `ifdef UVM_EMPTY_MACROS) is left out, unless nothing else defines the macro. A macro
        # defined only in such branches (QUESTA under `ifdef MODEL_TECH) is not defined either.
        given = set(self.sources.defines) if self.sources else set()
        live, known = self.macros_by_name, None
        while known != set(live) | given:
            known = set(live) | given
            live = {name: kept for name, defs in self.macros_by_name.items()
                    if (kept := [m for m in defs if not any(_never(c, given, known) for c in m.requires)])}
        for name, defs in self.macros_by_name.items():
            self.macros_by_name[name] = live.get(name) or defs
        for cls in self.classes:
            for td in cls.typedefs:
                self.typedefs_by_name.setdefault(td.name, []).append(td)
        # Out-of-block method bodies contribute to their class.
        for idx in self.indices.values():
            for ob in idx.out_of_block:
                cls = self.find_class(ob.class_name, ob.package)
                if cls is None:
                    continue
                self.impls[(cls.qualname, ob.method.name)] = ob.method
                cs = self.calls[cls.qualname]
                cs.creates += ob.creates
                cs.config_db += ob.config_db
                cs.overrides += ob.overrides
                cs.connects += ob.connects
                cs.seq_starts += ob.seq_starts
        for cls in self.classes:
            if not cls.library:
                for cr in self.calls_of(cls).creates:
                    if cr.kind == "factory" and cr.range.path not in self.library_files:
                        self.creates_by_file.setdefault(cr.range.path, []).append(cr)
            base = self.base_spec(cls)
            if base is not None:
                self.subclasses.setdefault(base.key, []).append(cls)

    # -- lookup --------------------------------------------------------------

    def all_indices(self):
        return list(self.indices.values())

    def structures_in(self, path: str) -> list:
        """A file's structural pairs."""
        self._collect_library_structures(path)
        return self.structures_by_file.get(path, [])

    def folds_in(self, path: str) -> list:
        self._collect_library_structures(path)
        return self.folds_by_file.get(path, [])

    def _collect_library_structures(self, path: str):
        """A library root's pairs and folds are collected when one of its files first asks: user
        code never needs them."""
        if path not in self.library_files:
            return
        for root in self.roots_for(path):
            idx = self.indices[root]
            if idx.structures_pending:
                idx.structures, idx.folds = self.indexer.structures(root, self.overrides)
                idx.structures_pending = False
                known: dict = {}
                for pair in idx.structures:
                    if _unseen(known, self.structures_by_file, pair.opening.path, pair, lambda p: p):
                        self.structures_by_file.setdefault(pair.opening.path, []).append(pair)
                for fold in idx.folds:
                    if _unseen(known, self.folds_by_file, fold.path, fold, lambda f: f):
                        self.folds_by_file.setdefault(fold.path, []).append(fold)

    def visible_packages(self, imports) -> set[str]:
        """What `imports` make visible, with what their packages re-export (IEEE 1800 26.6):
        'p' for all of package p, 'p::x' for its x alone."""
        key = tuple(imports)
        if key not in self._visible_packages:
            out, todo = set(), list(imports)
            while todo:
                p = todo.pop()
                if p in out:
                    continue
                out.add(p)
                package, _, name = p.partition("::")
                for u in self.units_by_name.get(package, ()):
                    if u.kind != "package":
                        continue
                    for e in u.exports:
                        for q in (u.imports if e == "*" else [e]):
                            if not name or q.endswith("::" + name):
                                todo.append(q)
                            elif "::" not in q:
                                todo.append(f"{q}::{name}")
            self._visible_packages[key] = out
        return self._visible_packages[key]

    @staticmethod
    def import_order(name: str, package: str | None, imports) -> list[list[str]]:
        """Where `name` is looked up, nearest first: the own package, packages that import it by
        name (`import p::name`), then wildcard imports. A nearer one hides the farther ones."""
        return [[package] if package else [], [p.split("::")[0] for p in imports if p.endswith("::" + name)],
                [p for p in imports if "::" not in p]]

    def unit_at(self, rng: Range, packages: bool = False) -> DesignUnit | None:
        """The module, interface or program (or package) around a position, in its file or
        around the `include that brings the file in."""
        unit = next((u for u in self.by_path.get(rng.path, ()) if isinstance(u, DesignUnit)
                     and (packages or u.kind != "package") and u.decl_range.contains(rng.line, rng.col)), None)
        return unit if unit is not None else self._unit_including(rng.path, packages)

    def _unit_including(self, path: str, packages: bool) -> DesignUnit | None:
        if self._include_sites is None:
            self._include_sites = {}
            for idx in self.indices.values():
                for sites in idx.includes.values():
                    for target, site in sites:
                        self._include_sites.setdefault(target, []).append(site)
        key = (path, packages)
        if key not in self._including:
            self._including[key] = None  # an include cycle ends here
            self._including[key] = next(filter(None, (self.unit_at(site, packages)
                                                      for site in self._include_sites.get(path, ()))), None)
        return self._including[key]

    def _visible(self, cands: list, package: str | None, imports, near: Range | None):
        """The one declaration of `cands` that a reference at `near`, in `package` with `imports`,
        sees, or None when there is none or the index cannot tell which. An import by name comes
        before wildcard ones. Outside a package, a module's own declarations come first and $unit
        ones compiled with `near` before others."""
        if not cands:
            return None

        name = cands[0].name

        def imported(imports):  # by name, by wildcard
            _, named, wildcard = self.import_order(name, None, imports)
            by_name = self.visible_packages([f"{p}::{name}" for p in named])
            packages = self.visible_packages(wildcard)
            return [c for c in cands if f"{c.package}::{name}" in by_name], \
                [c for c in cands if c.package in packages or f"{c.package}::{name}" in packages]

        named, wildcard = imported(imports)
        if package:
            levels = [[c for c in cands if c.package == package], named, wildcard]
        elif near is None:
            levels = [[c for c in cands if c.package is None], named, wildcard]
        else:
            unit = self.unit_at(near)
            outside = [(c, self.unit_at(c.range)) for c in cands if c.package is None]
            local = [c for c, u in outside if u is not None and u is unit]
            in_unit = [c for c, u in outside if u is None]
            together = [c for c in in_unit if self._compiled_together(c.range.path, near.path)]
            elsewhere = [c for c in in_unit if all(c is not t for t in together)]
            if unit:
                # The module's own imports come before $unit declarations, its $unit imports after;
                # a $unit import by name is a $unit declaration.
                own_named, own = imported(imports[len(unit.unit_imports):])
                unit_named, unit_wildcard = imported(imports[:len(unit.unit_imports)])
                levels = [local, own_named, own, together + unit_named, unit_wildcard, elsewhere]
            else:
                levels = [together + named, wildcard, elsewhere]
        for found in levels:
            if len(found) > 1 and near is not None:
                # Same-named declarations of another testbench, which is not compiled with `near`.
                found = [c for c in found if self._compiled_together(c.range.path, near.path)] or found
            if found:
                return found[0] if len(found) == 1 else None
        return None

    def _compiled_together(self, a: str, b: str) -> bool:
        if self._roots_by_file is None:
            self._roots_by_file = {}
            for root, idx in self.indices.items():
                for f in idx.files:
                    self._roots_by_file.setdefault(f, set()).add(root)
        return not self._roots_by_file.get(a, set()).isdisjoint(self._roots_by_file.get(b, ()))

    def find_class(self, name: str, package: str | None = None,
                   imports: list[str] | tuple = (), strict: bool = False,
                   near: Range | None = None) -> ClassInfo | None:
        cands = self.classes_by_name.get(name)
        if not cands:
            return None
        if strict:
            return self._visible(cands, package, imports, near)
        if len(cands) == 1:
            return cands[0]
        for pref in (p for level in self.import_order(name, package, imports) for p in level):
            for c in cands:
                if c.package == pref:
                    return c
        user = [c for c in cands if not c.library]
        return (user or cands)[0]

    def find_typedef(self, name: str, package: str | None = None, owner_class: str | None = None,
                     imports: list[str] | tuple = (), strict: bool = False,
                     near: Range | None = None) -> Typedef | None:
        cands = self.typedefs_by_name.get(name)
        if not cands:
            return None
        if strict:
            owned = [t for t in cands if t.owner_class == owner_class and t.package == package] if owner_class else []
            if owned:
                return self._visible(owned, package, (), near)
            return self._visible([t for t in cands if t.owner_class is None], package, imports, near)
        if owner_class:
            for t in cands:
                if t.owner_class == owner_class:
                    return t
        pkg_level = [t for t in cands if t.owner_class is None]
        for pref in (p for level in self.import_order(name, package, imports) for p in level):
            for t in pkg_level:
                if t.package == pref:
                    return t
        return (pkg_level or cands)[0] if not owner_class or pkg_level else None

    def find_unit(self, name: str, kind: str | None = None) -> DesignUnit | None:
        for u in self.units_by_name.get(name, []):
            if kind is None or u.kind == kind:
                return u
        return None

    def resolve_spec(self, name: str, scope: str | None = None, args: list[str] | None = None,
                     ctx: ClassInfo | None = None, depth: int = 0, *,
                     package: str | None = None, imports: list[str] | None = None, strict: bool = False,
                     near: Range | None = None, members: bool = True) -> Spec | None:
        """Resolve a type name (following typedefs) to an indexed or built-in class. A strict
        lookup sees only what is visible at `near` (by default the context class). `members`
        includes the typedefs the context class declares, inherits or is nested in."""
        if depth > 8:
            return None
        package = package if package is not None else ctx.package if ctx else None
        imports = imports if imports is not None else ctx.imports if ctx else ()
        site = near or (ctx.range if ctx else None)
        near = site if strict else None
        args = list(args or [])
        if args:
            here = Scope.of(ctx, package, imports, site)
            args = [a if isinstance(a, Bound) else Bound(a, here) for a in args]
        cache_key = (name, scope, tuple(map(_arg_key, args)), ctx.qualname if ctx else None,
                     package, tuple(imports), strict, near, members)
        if depth == 0 and cache_key in self._spec_cache:
            return self._spec_cache[cache_key]
        result: Spec | None = None
        settled = False  # a class scope's typedef hides the names outside it
        if scope and scope in self.class_by_qual or (scope and self.classes_by_name.get(scope)):
            # Class-scoped typedef such as outer::inner_t.
            owner = self.class_by_qual.get(scope) or self.find_class(scope, package, imports, strict, near)
            td = self.find_typedef(name, owner.package, owner.name, strict=strict, near=owner.range) if owner else None
            if td and td.target:
                result = self._spec_from_typedef(td, owner, depth, strict)
        elif ctx is not None and not scope and members:
            found, result = self._class_typedef(ctx, name, depth, strict)
            settled = found and strict
        if result is None and not settled:
            cls = self.find_class(name, scope or package, [scope] if scope else imports, strict, near)
            if cls is not None and (scope is None or cls.package == scope or not cls.package):
                result = Spec(name=cls.name, cls=cls, args=args)
        if result is None and not settled:
            td = self.find_typedef(name, scope or package, ctx.name if ctx else None,
                                   [scope] if scope else imports, strict, near)
            if strict and scope and td and td.package != scope and td.owner_class != scope:
                td = None
            if td is not None and td.target is not None:
                result = self._spec_from_typedef(td, ctx, depth, strict)
        if result is None and not settled and name in BUILTIN_CLASSES:
            result = Spec(name=name, args=args)
        if depth == 0:
            self._spec_cache[cache_key] = result
        return result

    def _class_typedef(self, cls: ClassInfo, name: str, depth: int, strict: bool) -> tuple[bool, Spec | None]:
        """Whether code in `cls` sees a member `name` that is a typedef (its own, a base class's or an
        enclosing class's), and the class type it names."""
        if not any(t.owner_class for t in self.typedefs_by_name.get(name, ())):
            return False, None
        for c in [cls, *self.outer_classes(cls)]:
            hit = self.find_member(c, name, strict=strict)
            if hit is not None:
                td = hit[1]
                if not isinstance(td, Typedef):
                    return False, None
                # `typedef box box;` names the box outside the class.
                outside = td.target is not None and td.target.scope is None and td.target.name == name
                inner = self.resolve_type_ref(td.target, hit[0], hit[2], strict, depth + 1,
                                              members=not outside) if td.target else None
                return True, Spec(inner.name, inner.cls, inner.args, td) if inner is not None else None
        return False, None

    def outer_classes(self, cls: ClassInfo) -> list[ClassInfo]:
        """The classes a nested class is declared in, innermost first."""
        out, cur = [], cls
        while cur is not None and cur.outer_class and len(out) < 16:
            cur = self.find_class(cur.outer_class, cur.package)
            if cur is not None:
                out.append(cur)
        return out

    def _spec_from_typedef(self, td: Typedef, owner: ClassInfo | None, depth, strict=False) -> Spec | None:
        """A typedef's target, resolved where the typedef is written: in `owner`, the class that
        declares it, or else in its package, which sees no class's member typedefs."""
        inner = self.resolve_spec(td.target.name, td.target.scope, td.target.args,
                                  owner if td.owner_class else None, depth + 1,
                                  package=td.package, imports=td.imports, strict=strict, near=td.range)
        if inner is not None:
            return Spec(name=inner.name, cls=inner.cls, args=inner.args, via_typedef=td)
        return None

    def resolve_type_text(self, text: str, ctx: ClassInfo | None = None, strict: bool = False, depth: int = 0,
                          members: bool = True) -> Spec | None:
        """Resolve type text written in `ctx`; a bound argument resolves where it was written."""
        where = {}
        if isinstance(text, Bound):
            if text.spec is not None:
                return text.spec
            ctx = text.scope.ctx
            where = {"package": text.scope.package, "imports": text.scope.imports, "near": text.scope.near}
        parsed = parse_type_text(text)
        if not parsed:
            return None
        scope, name, args = parsed
        return self.resolve_spec(name, scope, args, ctx, depth, strict=strict, members=members, **where)

    def resolve_type_ref(self, ref: TypeRef, ctx: ClassInfo | None = None, bindings: dict[str, str] | None = None,
                         strict: bool = False, depth: int = 0, **where) -> Spec | None:
        """Resolve an indexed type reference with the type parameters in scope bound. The structured
        reference keeps scopes such as `cls #(4)::inner_t` that the type text cannot. Outside a
        class, `where` gives the package, imports and position that see the reference."""
        bindings = bindings or {}
        if ref.scope is None and ref.name in bindings:
            return self.resolve_type_text(bindings[ref.name], ctx, strict, depth)
        if bindings and ctx is not None and ref.scope is None and depth < 8 and \
                any(t.owner_class for t in self.typedefs_by_name.get(ref.name, ())):
            # A member typedef of a specialization (rsrc_t) names its target with the parameters bound.
            hit = self.find_member(ctx, ref.name, bindings, strict)
            td = hit[1] if hit is not None else None
            if isinstance(td, Typedef) and td.target is not None and \
                    not (td.target.scope is None and td.target.name == ref.name):
                inner = self.resolve_type_ref(td.target, hit[0], hit[2], strict, depth + 1)
                return Spec(inner.name, inner.cls, inner.args, td) if inner is not None else None
        scope = ref.scope
        if scope in bindings:  # T::inner_t
            outer = self.resolve_type_text(bindings[scope], ctx, strict, depth)
            if outer is not None and outer.cls is not None:
                scope = outer.cls.qualname
            else:
                parsed = parse_type_text(bindings[scope])
                scope = "::".join(p for p in parsed[:2] if p) if parsed else scope
        args = [self._bind_arg(a, ctx, bindings, strict, depth) for a in ref.args]
        return self.resolve_spec(ref.name, scope, args, ctx, depth, strict=strict, **where)

    def _bind_arg(self, arg: str, ctx: ClassInfo | None, bindings: dict[str, str], strict: bool, depth: int) -> str:
        """A type argument written in `ctx` with its parameters bound. In `pair #(T)`, pair means
        what it means in ctx and T's value what it meant where it was written. A typedef member of
        the class (`this_type`) means nothing elsewhere: it becomes the type it names."""
        value = substitute_params(arg, bindings)
        if ctx is None or isinstance(value, Bound):
            return value
        if value != arg:
            return self._bind_type(Bound(arg, Scope.of(ctx)), bindings, strict, depth, members=True)
        if not any(t.owner_class for t in self.typedefs_by_name.get(arg, ())):
            return value
        hit = self.find_member(ctx, arg, bindings, strict)
        if hit is None or not isinstance(hit[1], Typedef):
            return value
        spec = self.member_type_spec(hit[0], hit[1], hit[2], strict, depth + 1)
        if spec is None or spec.cls is None:
            return value
        text = spec.cls.name + ("#(" + ",".join(spec.args) + ")" if spec.args else "")
        return Bound(text, Scope.of(ctx), spec)

    def _bind_type(self, text: str, bindings: dict[str, str], strict: bool = False, depth: int = 0,
                   members: bool = False) -> str:
        """Bound type text with `bindings` put in. In `pair #(T)`, pair means what it means where
        the text is written and T's value what it meant where that was written, so a composite
        becomes the type it names."""
        value = _bind(text, bindings)
        if not isinstance(text, Bound) or value is text or value.spec is not None or str(value) == text:
            return value
        parsed = parse_type_text(text)
        if not parsed or not parsed[2]:
            return value
        where = text.scope
        args = [self._bind_type(Bound(a, where), bindings, strict, depth, members) for a in parsed[2]]
        spec = self.resolve_spec(parsed[1], parsed[0], args, where.ctx, depth, package=where.package,
                                 imports=where.imports, strict=strict, near=where.near, members=members)
        return Bound(value, where, spec, value.formal) if spec is not None else value

    def base_spec(self, cls: ClassInfo, bindings: dict[str, str] | None = None, strict: bool = False) -> Spec | None:
        """The base class; `extends BASE` follows what the type parameter BASE is bound to
        (uvm_reg_sequence, uvm_port_base), else its default. Strict: only a base visible there."""
        ref = cls.base
        if ref is None:
            return None
        if ref.scope is None and not ref.args and any(p.is_type and p.name == ref.name for p in cls.params):
            bound = (bindings or {}).get(ref.name)
            if bound is None:
                bound = next(p.default for p in cls.params if p.name == ref.name)
            return self.resolve_type_text(bound, cls, strict, members=False)
        spec = self.resolve_spec(ref.name, ref.scope, ref.args, cls, strict=strict, members=False)
        if strict and (spec is None or spec.cls is None) and cls.package is None and ref.scope is None:
            # Outside a package, a $unit import in another file of the compilation unit (-mfcu)
            # can make the base visible; the index cannot see it, so take the only candidate.
            cands = [c for c in self.classes_by_name.get(ref.name, ())
                     if c.library or self._compiled_together(c.range.path, cls.range.path)]
            if len(cands) == 1:
                found = self.resolve_spec(ref.name, None, ref.args, cls, members=False)
                spec = found if found is not None and found.cls is cands[0] else None
            elif cands:
                spec = None
        return spec

    def chain(self, cls: ClassInfo, bindings: dict[str, str] | None = None, strict: bool = False) -> list[ChainEntry]:
        """The class followed by all its ancestors, with type parameter bindings. A strict chain
        ends at a base class the lookup cannot see or tell from another of its name."""
        entries = [ChainEntry(Spec(cls.name, cls), bindings if bindings is not None else self.formals(cls))]
        if id(cls) in self._chaining:
            return entries  # its ancestors depend on its own members: A extends B::t, B extends A::u
        self._chaining.add(id(cls))
        try:
            self._ancestors(cls, entries, strict)
        finally:
            self._chaining.discard(id(cls))
        return entries

    def _ancestors(self, cls: ClassInfo, entries: list[ChainEntry], strict: bool):
        seen = {cls.qualname}
        cur, bindings = cls, entries[0].bindings
        while cur is not None and cur.base is not None:
            base = self.base_spec(cur, bindings, strict)
            if base is None:
                entries.append(ChainEntry(Spec(cur.base.name), {}))
                break
            args = base.args or cur.base.args
            if base.cls is not None:
                if base.cls.qualname in seen:
                    break
                seen.add(base.cls.qualname)
                bindings = self.specialize(base.cls, args, bindings, strict)
                entries.append(ChainEntry(base, bindings))
                cur = base.cls
            else:
                name = base.name
                while name and name not in seen:
                    seen.add(name)
                    bindings = bind_params(builtin_params(name), args, bindings)
                    entries.append(ChainEntry(Spec(name), bindings))
                    parent = BUILTIN_CLASSES.get(name, (None, ""))[0]
                    args = [bindings.get(p, p) for p, _ in builtin_params(name)]
                    name = parent
                break

    def specialize(self, cls: ClassInfo, args: list[str], outer: dict[str, str] | None = None,
                   strict: bool = False) -> dict[str, str]:
        """cls's parameters bound to a specialization's arguments, in which `outer` binds the
        parameters of the class that writes them."""
        if not cls.params:
            return {}
        return bind_params([(p.name, p.default) for p in cls.params], args, outer or {},
                           lambda text, bindings: self._bind_type(text, bindings, strict),
                           Scope.of(cls) if len(args) < len(cls.params) else None)

    def formals(self, cls: ClassInfo) -> dict[str, str]:
        """A class's parameters as its own body sees them: unbound, resolving as their defaults."""
        key = id(cls)
        if key not in self._formals:
            here = Scope.of(cls)
            self._formals[key] = {p.name: Bound(p.default, here, formal=p.name) for p in cls.params}
        return dict(self._formals[key])

    def role(self, cls: ClassInfo) -> str:
        r = self._role_cache.get(cls.qualname)
        if r is not None:
            return r
        role = "interface" if cls.kind == "interface class" else "class"
        for entry in self.chain(cls):
            name = entry.spec.name
            if entry.spec.cls is cls and not cls.library:
                continue
            if name in ROLE_OF_BASE:
                role = ROLE_OF_BASE[name]
                break
        if role == "object" and looks_like_config(cls.name):
            role = "config"
        if cls.library and role in ("class", "object"):
            role = "uvm"
        self._role_cache[cls.qualname] = role
        return role

    def role_of_name(self, name: str) -> str:
        if name in ROLE_OF_BASE:
            r = ROLE_OF_BASE[name]
        else:
            chain_name = name
            r = "uvm" if name.startswith("uvm_") else "class"
            while chain_name:
                if chain_name in ROLE_OF_BASE:
                    r = ROLE_OF_BASE[chain_name]
                    break
                chain_name = BUILTIN_CLASSES.get(chain_name, (None, ""))[0]
        return r

    def is_component(self, cls: ClassInfo) -> bool:
        return self.role(cls) in COMPONENT_ROLES

    def find_member(self, cls: ClassInfo, name: str, bindings: dict[str, str] | None = None, strict: bool = False):
        """(owner, member, bindings) for `name` in cls or its ancestors."""
        for entry in self.chain(cls, bindings, strict):
            c = entry.spec.cls
            if name == "new" and strict and c is not cls:
                break  # constructors are not inherited
            if c is not None:
                for v in c.properties:
                    if v.name == name:
                        return c, v, entry.bindings
                for m in c.methods:
                    if m.name == name:
                        return c, self.impls.get((c.qualname, m.name), m) if m.is_prototype else m, entry.bindings
                for td in c.typedefs:
                    if td.name == name:
                        return c, td, entry.bindings
                for cname, r in c.constraints + c.covergroups:
                    if cname == name:
                        return c, ("constraint" if (cname, r) in c.constraints else "covergroup", cname, r), entry.bindings
            else:
                t = BUILTIN_MEMBERS.get(entry.spec.name, {}).get(name)
                if t is not None:
                    return entry.spec.name, t, entry.bindings
        return None

    def member_type_spec(self, owner, member, bindings: dict[str, str], strict: bool = False,
                         depth: int = 0) -> Spec | None:
        ctx = owner if isinstance(owner, ClassInfo) else None
        if isinstance(member, Variable):
            if member.type is None:
                return None
            return self.resolve_type_ref(member.type, ctx, bindings, strict, depth)
        if isinstance(member, Method):
            parsed = parse_type_text(member.return_type)
            if parsed:
                # As a property's type: a written specialization (holder #(T)) keeps the bindings.
                ref = TypeRef(parsed[1], member.return_type, parsed[0], parsed[2])
                return self.resolve_type_ref(ref, ctx, bindings, strict, depth)
            return None
        if isinstance(member, Typedef) and member.target is not None:
            return self.resolve_type_ref(member.target, ctx, bindings, strict, depth)
        if isinstance(member, str):  # built-in member type text
            parsed = parse_type_text(member)
            if parsed:
                return self.resolve_type_text(bindings.get(parsed[1], member), ctx, strict, depth)
        return None

    def calls_of(self, cls: ClassInfo) -> CallSet:
        return self.calls.get(cls.qualname, CallSet())

    def calls_in_chain(self, cls: ClassInfo) -> CallSet:
        """Calls made by the class and everything it inherits, base first."""
        out = CallSet()
        for entry in reversed(self.chain(cls)):
            if entry.spec.cls is not None:
                cs = self.calls_of(entry.spec.cls)
                out.creates += cs.creates
                out.config_db += cs.config_db
                out.overrides += cs.overrides
                out.connects += cs.connects
                out.seq_starts += cs.seq_starts
        return out

    def symbols_in(self, path: str) -> list:
        return self.by_path.get(path, [])

    def enclosing(self, path: str, line: int, col: int):
        """(class, method, unit) enclosing a position."""
        cls_hit, meth_hit, unit_hit = None, None, None
        for sym in self.symbols_in(path):
            if isinstance(sym, ClassInfo) and sym.decl_range.contains(line, col):
                if cls_hit is None or sym.decl_range.line >= cls_hit.decl_range.line:
                    cls_hit = sym
            elif isinstance(sym, DesignUnit) and sym.decl_range.contains(line, col):
                unit_hit = sym
        if cls_hit is not None:
            for m in cls_hit.methods:
                if m.decl_range.contains(line, col):
                    meth_hit = m
        if meth_hit is None:
            for (qual, _), m in self.impls.items():
                if m.decl_range.path == path and m.decl_range.contains(line, col):
                    meth_hit = m
                    cls_hit = self.class_by_qual.get(qual)
                    break
        if cls_hit is None and unit_hit is None:  # a file a package or module includes
            unit_hit = self.unit_at(Range(path, line, col, line, col), packages=True)
        if meth_hit is None and unit_hit is not None:
            for m in unit_hit.methods:
                if m.decl_range.path == path and m.decl_range.contains(line, col):
                    meth_hit = m
        return cls_hit, meth_hit, unit_hit

    def files(self) -> set[str]:
        out: set[str] = set()
        for idx in self.indices.values():
            out |= idx.files
        return out

    def user_diagnostics(self) -> list[Diag]:
        """Parse diagnostics of user roots, each once, never inside a library file."""
        out, seen = [], set()
        for idx in self.indices.values():
            if idx.library:
                continue
            for d in idx.diagnostics:
                key = (d.range.path, d.range.line, d.range.col, d.message)
                if key not in seen and d.range.path not in self.library_files:
                    seen.add(key)
                    out.append(d)
        return out

    def stats(self) -> dict:
        user = [c for c in self.classes if not c.library]
        files = self.files()
        s = self.sources
        return {
            "classes": len(user),
            "libraryClasses": len(self.classes) - len(user),
            "files": len(files - self.library_files),
            "libraryFiles": len(files & self.library_files),
            "roots": len(self.indices),
            "mode": s.mode if s else "none",
            "realUvm": bool(s and s.has_real_uvm_macros),
            "uvm": {"source": s.uvm_source if s else "none",
                    "version": s.uvm_version if s else None,
                    "dir": s.uvm_dir if s else None},
            "seconds": round(self.last_index_seconds, 3),
        }
