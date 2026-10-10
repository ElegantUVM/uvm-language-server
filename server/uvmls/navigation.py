"""Editor features that resolve the code under the cursor: definition, hover,
references, completion, symbols and type hierarchy."""

from __future__ import annotations

import os
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field, replace

from . import naturaldocs
from .indexer import SHIM_FILE
from .model import ClassInfo, DesignUnit, MacroDef, Method, Range, Typedef, Variable, utf8_prefix
from .uvm import builtin_params
from .views import config_db_report, mismatched, norm_type
from .workspace import Bound, Scope, Spec, Workspace, bind_params

WORD = re.compile(r"[A-Za-z0-9_$]")
# What the utils and field macros generate in a user class; other generated methods are API
# (a TLM port's get_next_item, item_done, put ...).
UTILS_METHODS = {"get_type", "get_object_type", "create", "get_type_name", "__m_uvm_field_automation"}
CROSSING_FOLDS = {"branch", "region", "macro", "body"}  # folds that need not nest with the syntax around them


def _nested(spans) -> list[tuple[int, int]]:
    """(start, end) spans, outer first on a shared start, less each that crosses one kept before it."""
    out, ends = [], []
    for start, end in sorted(spans, key=lambda p: (p[0], -p[1])):
        while ends and start > ends[-1]:
            ends.pop()
        if ends and end > ends[-1]:
            continue
        out.append((start, end))
        ends.append(end)
    return out


def _start_inside(nested, spans) -> set[int]:
    """The indexes of the spans that start inside one of `nested`, which nest, and end after it."""
    found, ends = set(), []
    for start, neg_end, i in sorted([(a, -b, -1) for a, b in nested] + [(a, -b, i) for i, (a, b) in enumerate(spans)]):
        while ends and start > ends[-1]:
            ends.pop()
        if i < 0:
            ends.append(-neg_end)
        elif ends and -neg_end > ends[-1]:
            found.add(i)
    return found


@dataclass
class Target:
    obj: object
    owner: object = None  # ClassInfo / DesignUnit / built-in class name
    bindings: dict | None = None
    spec: Spec | None = None  # for class-like targets
    note: str = ""


@dataclass
class CursorExpr:
    word: str
    segments: list[str]
    seps: list[str]
    is_macro: bool
    start: int
    end: int
    args: list[list[str]] = field(default_factory=list)


def expr_at(line: str, col: int) -> CursorExpr | None:
    """The dotted / scoped expression ending at the identifier under the cursor."""
    n = len(line)
    s = min(col, n)
    while s > 0 and WORD.match(line[s - 1]):
        s -= 1
    e = min(col, n)
    while e < n and WORD.match(line[e]):
        e += 1
    if s == e:
        return None
    word = line[s:e]
    is_macro = s > 0 and line[s - 1] == "`"
    segs, seps = [word], []
    i = s
    while not is_macro:
        j = i
        while j > 0 and line[j - 1] == " ":
            j -= 1
        if j >= 2 and line[j - 2:j] == "::":
            sep, j = "::", j - 2
        elif j >= 1 and line[j - 1] == "." and not (j >= 2 and line[j - 2] == "."):
            sep, j = ".", j - 1
        else:
            break
        while j > 0 and line[j - 1] == " ":
            j -= 1
        while j > 0 and line[j - 1] in ")]":
            close = line[j - 1]
            opener = "(" if close == ")" else "["
            depth, k = 0, j - 1
            while k >= 0:
                if line[k] == close:
                    depth += 1
                elif line[k] == opener:
                    depth -= 1
                    if depth == 0:
                        break
                k -= 1
            j = max(k, 0)
            while j > 0 and line[j - 1] in " #":
                j -= 1
        k = j
        while k > 0 and WORD.match(line[k - 1]):
            k -= 1
        if k == j:
            break
        segs.insert(0, line[k:j])
        seps.insert(0, sep)
        i = k
    return CursorExpr(word, segs, seps, is_macro, s, e)


def parse_head(type_text: str) -> str:
    m = re.match(r"\s*(\w+)", type_text or "")
    return m.group(1) if m else ""


def line_of(text: str, line: int) -> str:
    lines = text.split("\n")
    return lines[line] if 0 <= line < len(lines) else ""


class Navigator:
    def __init__(self, ws: Workspace):
        self.ws = ws

    # -- resolution ----------------------------------------------------------

    def resolve(self, path: str, line: int, col: int, text: str) -> list[Target]:
        ce = expr_at(line_of(text, line), col)
        return self.resolve_expr(path, line, col, ce)

    def resolve_expr(self, path: str, line: int, col: int, ce: CursorExpr | None, strict: bool = False) -> list[Target]:
        if ce is None:
            return []
        ws = self.ws
        if ce.is_macro:
            found = [Target(m) for m in ws.macros_by_name.get(ce.word, [])
                     if os.path.normpath(m.range.path) != SHIM_FILE]
            if not found and ce.word in shim_macros():
                params = shim_macros()[ce.word]
                found = [Target(("uvm-macro", ce.word, params))]
            return found
        segs, seps = ce.segments, ce.seps
        if segs == ["new"]:
            found = self.constructor_at(path, line, col, strict)
            if found is not None:
                return found
        cls, meth, unit = ws.enclosing(path, line, col)
        near = Range(path, line, col, line, col)
        current = self.resolve_first(segs[0], cls, meth, unit, scoped=bool(seps) and seps[0] == "::", strict=strict,
                                     near=near)
        for i in range(len(segs)):
            if i:
                if strict and len(current) != 1:
                    return []
                if not current:
                    return self.global_member_search(segs[i]) if i == len(segs) - 1 else []
                current = self.step(current[0], segs[i], seps[i - 1], cls, strict=strict)
            if ce.args and ce.args[i] and len(current) == 1 and current[0].spec is not None:
                here = Scope.of(cls, *self._package_scope(cls, unit), near)
                current[0].spec = replace(current[0].spec, args=[Bound(a, here) for a in ce.args[i]])
        return current

    @staticmethod
    def _package_scope(cls, unit) -> tuple[str | None, list[str]]:
        """The package and imports that code in a class, or else in a design unit, sees."""
        if cls is not None:
            return cls.package, list(cls.imports)
        return (unit.name if unit.kind == "package" else None, list(unit.imports)) if unit else (None, [])

    def resolve_first(self, name: str, cls, meth, unit, scoped: bool, strict: bool = False,
                      near: Range | None = None) -> list[Target]:
        ws = self.ws
        if name == "this" and cls is not None:
            return [Target(cls, spec=Spec(cls.name, cls, list(ws.formals(cls).values())))]
        if name == "super" and cls is not None:
            base = ws.base_spec(cls, strict=strict)
            return [Target(base.cls or base.name, spec=base)] if base else []
        if scoped:
            pkg = ws.find_unit(name, "package")
            if pkg is not None:
                return [Target(pkg)]
        if meth is not None:
            for v in meth.args + meth.locals:
                if v.name == name:
                    return [Target(v, owner=cls if cls is not None else unit)]
        if cls is not None:
            hit = ws.find_member(cls, name, strict=strict)
            if hit is not None:
                return [Target(hit[1], owner=hit[0], bindings=hit[2], note=name)]
            for c in ws.outer_classes(cls):
                hit = ws.find_member(c, name, strict=strict)
                if hit is not None:
                    return [Target(hit[1], owner=hit[0], bindings=hit[2], note=name)]
            nested = ws.nested_class(cls, name, strict)
            if nested is not None:
                return [Target(nested, spec=Spec(nested.name, nested))]
        package, imports = self._package_scope(cls, unit)
        found = ws.find_class(name, package, imports, strict=strict, near=near)
        if found is not None and not ws.typedef_hides(found, name, package):
            return [Target(found, spec=Spec(found.name, found))]
        td = ws.find_typedef(name, package, cls.name if cls else None, imports, strict=strict, near=near)
        if td is not None:
            spec = ws.resolve_spec(name, ctx=cls, strict=strict, near=near)
            return [Target(td, spec=spec)]
        for u in ws.units_by_name.get(name, []):
            return [Target(u)]
        if unit is not None:
            for v in unit.ports + unit.variables:
                if v.name == name:
                    return [Target(v, owner=unit)]
            for m in unit.methods:
                if m.name == name:
                    return [Target(m, owner=unit)]
        # Items of packages in scope, nearest first, then of any package.
        levels = [[ws.find_unit(p, "package") for p in level] for level in ws.import_scopes(name, package, imports)]
        if not strict:
            levels.append([u for u in ws.units if u.kind == "package"])
        for level in levels:
            matches = []
            for u in level:
                if u is None:
                    continue
                for member in [*u.variables, *u.methods]:
                    if member.name == name:
                        if not strict:
                            return [Target(member, owner=u)]
                        if not any(t.obj is member for t in matches):
                            matches.append(Target(member, owner=u))
            if matches:
                return matches if len(matches) == 1 else []
        if name.startswith("uvm_"):
            spec = ws.resolve_spec(name, package=package, imports=imports, strict=strict, near=near)
            if spec is not None:
                return [Target(spec.cls or spec.name, spec=spec)]
        return []

    def type_of(self, t: Target, strict: bool = False) -> Spec | None:
        ws = self.ws
        if t.spec is not None and isinstance(t.obj, (ClassInfo, Typedef, str)):
            return t.spec
        if isinstance(t.obj, Variable):
            if t.obj.type is None:
                return None
            if isinstance(t.owner, DesignUnit):  # a package, module or interface item
                package, imports = self._package_scope(None, t.owner)
                return ws.resolve_type_ref(t.obj.type, None, t.bindings, strict, package=package,
                                           imports=imports, near=t.obj.range)
            ctx = t.owner if isinstance(t.owner, ClassInfo) else None
            return ws.resolve_type_ref(t.obj.type, ctx, t.bindings, strict)
        if t.owner is not None and isinstance(t.obj, (Method, Typedef, str)):
            return ws.member_type_spec(t.owner, t.obj, t.bindings or {}, strict=strict)
        return None

    def new_target_type(self, path, line, col, target, type_ref=None):
        """The assigned type, its written spelling and whether it came from a base."""
        cls, _, _ = self.ws.enclosing(path, line, col)
        if type_ref is not None:
            return self.ws.resolve_type_ref(type_ref, cls), type_ref.text, False
        if not target:
            return None, "", False
        text = re.sub(r"\[[^\]]*\]", "", target)
        targets = self.resolve_expr(path, line, col, expr_at(text, len(text)), strict=True)
        if len(targets) != 1 or not isinstance(targets[0].obj, Variable):
            return None, "", False
        t = targets[0]
        return self.type_of(t, strict=True), t.obj.type_text, isinstance(t.owner, ClassInfo) and t.owner is not cls

    def constructor_at(self, path, line, col, strict=False, call=None) -> list[Target] | None:
        """The constructor a bare `new` at a position calls: the assigned variable's class's, or
        unless strict the one it inherits. None when the position has no indexed `new` or its
        target's class is unknown."""
        ws = self.ws
        cls, _, _ = ws.enclosing(path, line, col)
        create = next((cr for cr in (ws.calls_of(cls).creates if cls else ()) if cr.kind == "new" and
                       cr.range.path == path and cr.range.contains(line, col)), None)
        if call is None and create is None:
            call = next((c for c in ws.invocations_by_file.get(path, ()) if c.head.contains(line, col)
                         and [s[0] for s in c.segments] == ["new"]), None)
        if call is None and create is None:
            return None
        spec, _, _ = self.new_target_type(path, line, col, create.target if create else call.target,
                                          create.type if create else None)
        if spec is None or spec.cls is None:
            return None
        bindings = ws.specialize(spec.cls, spec.args, strict=strict)
        hit = ws.find_member(spec.cls, "new", bindings, strict)
        return [Target(hit[1], owner=hit[0], bindings=hit[2])] if hit else []

    def bound_member_hints(self, path: str, start_line: int = 0,
                           end_line: int | None = None) -> list[tuple[Range, str, str]]:
        ws = self.ws
        identifiers = ws.identifiers_by_file.get(path, ())
        first = bisect_left(identifiers, start_line, key=lambda pair: pair[1].line)
        last = bisect_right(identifiers, end_line, key=lambda pair: pair[1].line) \
            if end_line is not None else len(identifiers)
        identifiers = identifiers[first:last]
        line_numbers = dict.fromkeys(rng.line for _, rng in identifiers)
        cache = ws._bound_member_hints.setdefault(path, {})
        missing = line_numbers.keys() - cache.keys()
        if not missing:
            return [hint for line in line_numbers for hint in cache[line]]
        hints, seen, chains = [], set(), {}
        lines = ws.lines_of(path)
        for name, rng in identifiers:
            if rng.line not in missing:
                continue
            cls, _, _ = ws.enclosing(path, rng.line, rng.col)
            if cls is None or cls.library or cls.base is None or rng.line >= len(lines):
                continue
            col = len(utf8_prefix(lines[rng.line], rng.col))
            expr = expr_at(lines[rng.line], col)
            if expr is None or expr.is_macro or expr.segments not in ([name], ["this", name], ["super", name]):
                continue
            targets = self.resolve_expr(path, rng.line, rng.col, expr)
            if len(targets) != 1:
                continue
            target = targets[0]
            member, owner = target.obj, target.owner
            if isinstance(member, Variable):
                if member.kind != "property" or member.type is None or member.type.scope or member.type.args:
                    continue
                if member.range.path == path and member.range.line == rng.line:
                    continue
                param = member.type.name
                params = [p.name for p in owner.params if p.is_type] if isinstance(owner, ClassInfo) else []
            elif isinstance(member, str) and isinstance(owner, str):
                param = member
                params = [p for p, _ in builtin_params(owner)]
            else:
                continue
            if param not in params:
                continue
            if cls.qualname not in chains:
                # A generic class's defaults are not bindings for every specialization of it.
                chains[cls.qualname] = ws.chain(cls, {p.name: p.name for p in cls.params})
            entry = next((e for e in chains[cls.qualname][1:]
                          if (e.spec.cls or e.spec.name) == owner), None)
            if entry is None:
                continue
            bound = entry.bindings.get(param, "")
            if not bound or bound == param or any(re.search(r"\b" + re.escape(p.name) + r"\b", bound)
                                                  for p in cls.params):
                continue
            key = (rng.line, name)
            if key in seen:
                continue
            seen.add(key)
            target.bindings = entry.bindings
            spec = self.type_of(target)
            shown = (spec.via_typedef.name if spec.via_typedef else spec.name) if spec and not spec.args else bound
            tooltip = f"{param} bound in {cls.name} extends {norm_type(cls.base.text)}"
            hints.append((rng, norm_type(shown), tooltip))
        for line in missing:
            cache[line] = []
        for hint in hints:
            cache[hint[0].line].append(hint)
        return [hint for line in line_numbers for hint in cache[line]]

    @staticmethod
    def _package_item(ws, pkg, name: str, strict: bool) -> list[Target]:
        """A package's own class, typedef, variable or method of that name, at most one."""
        for c in ws.classes_by_name.get(name, []):
            if c.package == pkg.name:
                return [Target(c, spec=Spec(c.name, c))]
        td = ws.find_typedef(name, pkg.name, strict=strict)
        if td is not None and td.package == pkg.name:
            return [Target(td, spec=ws.resolve_spec(name, pkg.name, strict=strict))]
        for v in pkg.variables:
            if v.name == name:
                return [Target(v, owner=pkg)]
        for m in pkg.methods:
            if m.name == name:
                return [Target(m, owner=pkg)]
        return []

    def _package_member(self, ws, pkg, name: str, strict: bool, seen: set) -> list[Target]:
        """What `pkg::name` means (IEEE 1800 26.6): the package's own declaration, else what the
        name means in the package an import by that name comes from, else in the packages its
        wildcard imports bring in, each as far as `pkg` exports it. Strict: a tie within one of
        those levels is withheld, not guessed."""
        if pkg.name in seen:
            return []
        seen = seen | {pkg.name}
        exported = {p for level in ws.import_scopes(name, pkg.name, [pkg.name]) for p in level}
        own, named, wildcard = ws.import_order(name, pkg.name, pkg.imports)
        for level in (own, named, wildcard):
            found = []
            for p in dict.fromkeys(level):
                if p == pkg.name:
                    found += self._package_item(ws, pkg, name, strict)
                elif p in exported and (other := ws.find_unit(p, "package")) is not None:
                    found += self._package_member(ws, other, name, strict, seen)
            if found:
                # Two routes to one declaration (a diamond of re-exports) are no tie.
                distinct = list({id(f.obj): f for f in found}.values())
                return distinct[:1] if not strict or len(distinct) == 1 else []
        return []

    def step(self, t: Target, name: str, sep: str, ctx_cls, strict: bool = False) -> list[Target]:
        ws = self.ws
        if isinstance(t.obj, DesignUnit) and t.obj.kind == "package":
            # The package's own items, then what its imports by name re-export, then what its
            # wildcard imports re-export (IEEE 1800 26.6). Strict: a name several wildcard
            # re-exports supply is ambiguous, and an explicit import outranks them.
            return self._package_member(ws, t.obj, name, strict, set())
        if isinstance(t.obj, Variable) and t.obj.type is not None and t.obj.type.text.startswith("virtual"):
            iface = ws.find_unit(t.obj.type.name, "interface")
            if iface is not None:
                return self._unit_member(iface, name)
        if isinstance(t.obj, DesignUnit):
            return self._unit_member(t.obj, name)
        if isinstance(t.obj, Variable) and t.obj.kind in ("clocking", "modport"):
            return self._unit_member(t.owner, name) if isinstance(t.owner, DesignUnit) else []
        spec = self.type_of(t, strict=strict)
        if spec is None:
            return [] if strict else self.global_member_search(name)
        if spec.cls is not None:
            bindings = ws.specialize(spec.cls, spec.args, strict=strict)
            hit = ws.find_member(spec.cls, name, bindings, strict)
            if hit is not None:
                return [Target(hit[1], owner=hit[0], bindings=hit[2], note=name)]
            if sep == "::" and (nested := ws.nested_class(spec.cls, name, strict)) is not None:
                return [Target(nested, spec=Spec(nested.name, nested))]
            return []
        # A built-in UVM class: walk its built-in chain for known members.
        for entry_name in self._builtin_chain(spec.name):
            from .uvm import BUILTIN_MEMBERS, builtin_params
            mt = BUILTIN_MEMBERS.get(entry_name, {}).get(name)
            if mt is not None:
                bindings = bind_params(builtin_params(spec.name), spec.args, {})
                return [Target(mt, owner=entry_name, bindings=bindings, note=name)]
        return []

    def _unit_member(self, unit: DesignUnit, name: str) -> list[Target]:
        for v in unit.ports + unit.variables:
            if v.name == name:
                return [Target(v, owner=unit)]
        for m in unit.methods:
            if m.name == name:
                return [Target(m, owner=unit)]
        return []

    def _builtin_chain(self, name: str):
        from .uvm import BUILTIN_CLASSES
        out = []
        while name and name not in out:
            out.append(name)
            name = BUILTIN_CLASSES.get(name, (None, ""))[0]
        return out

    def global_member_search(self, name: str) -> list[Target]:
        out = []
        for cls in self.ws.classes:
            if cls.library:
                continue
            for v in cls.properties:
                if v.name == name:
                    out.append(Target(v, owner=cls))
            for m in cls.methods:
                if m.name == name:
                    out.append(Target(m, owner=cls))
        return out[:50]

    # -- locations -----------------------------------------------------------

    def target_ranges(self, t: Target) -> list[Range]:
        o = t.obj
        if isinstance(o, (ClassInfo, Variable, Typedef, DesignUnit, MacroDef)):
            return [o.range]
        if isinstance(o, Method):
            out = [o.range]
            if isinstance(t.owner, ClassInfo):
                impl = self.ws.impls.get((t.owner.qualname, o.name))
                if impl is not None and impl.range != o.range:
                    out.append(impl.range)
                for m in t.owner.methods:
                    if m.name == o.name and m.range != o.range and m.range not in out:
                        out.append(m.range)
            return out
        if isinstance(o, tuple) and len(o) == 3 and isinstance(o[2], Range):
            return [o[2]]
        return []

    def definition(self, path, line, col, text) -> list[Range]:
        out: list[Range] = []
        for t in self.resolve(path, line, col, text):
            for r in self.target_ranges(t):
                if r not in out:
                    out.append(r)
        return out

    def config_db_definition(self, path, line, col) -> list[Range]:
        hit = self.config_db_at(path, line, col)
        if hit is None:
            return []
        row, entry, _ = hit
        report = config_db_report(self.ws)
        ranges = set()
        for other in row["gets" if entry["op"] == "set" else "sets"]:
            rng = report.calls[id(other)].field_range
            if rng is not None:
                ranges.add(rng)
        return sorted(ranges, key=lambda r: (r.path, r.line, r.col))

    def type_definition(self, path, line, col, text) -> list[Range]:
        out = []
        for t in self.resolve(path, line, col, text):
            spec = self.type_of(t)
            if spec is not None and spec.cls is not None:
                out.append(spec.cls.range)
            elif isinstance(t.obj, Variable) and t.obj.type is not None:
                u = self.ws.find_unit(t.obj.type.name)
                if u is not None:
                    out.append(u.range)
        return out

    # -- hover ---------------------------------------------------------------

    def config_db_at(self, path, line, col, method=False):
        report = config_db_report(self.ws)
        for entry in report.by_file.get(path, ()):
            call = report.calls[id(entry)]
            if not call.field_literal:
                continue
            for rng in (call.field_range, call.method_range if method else None):
                if rng and rng.path == path and (rng.line, rng.col) <= (line, col) < (rng.end_line, rng.end_col):
                    return report.by_field[call.field_name], entry, rng
        return None

    def config_db_hover(self, path, line, col):
        hit = self.config_db_at(path, line, col, method=True)
        if hit is None:
            return None
        row, entry, rng = hit

        def escaped(text):
            return re.sub(r"([\\`*\[\]<>])", r"\\\1", text)

        def where(e):
            return f"{escaped(os.path.basename(e['file']))}:{e['line'] + 1}"

        lines = [f"**config_db '{escaped(row['field'])}'**"]
        types = dict.fromkeys((s["type"], s["canon"]) for s in row["sets"])
        for typ, canon in types:
            lines.append(f"type as set: {escaped(typ)}" + (f" ({escaped(canon)})" if canon and canon != typ else ""))
        for s in row["sets"]:
            inst = f'"{escaped(s["inst"])}"' if s["instLiteral"] else "dynamic"
            lines.append(f"set in {where(s)} for {inst}")
        if row["gets"]:
            lines.append("read in " + ", ".join(where(g) for g in sorted(
                row["gets"], key=lambda g: (g["file"], g["line"], g["col"]))))
        if not row["sets"] and not any(e["op"] == "set" for e in config_db_report(self.ws)["dynamic"]):
            lines.append("no set in the indexed sources")
        for typ in dict.fromkeys(g["type"] for g in mismatched(row)):
            lines.append(f"set as {escaped(row['sets'][0]['type'])} but read as {escaped(typ)}")
        return "\n\n".join(lines), row["field"], rng

    def hover(self, path, line, col, text) -> str | None:
        targets = self.resolve(path, line, col, text)
        if not targets:
            return None
        return self.describe(targets[0])

    def chain_text(self, cls: ClassInfo) -> str:
        parts = []
        for e in self.ws.chain(cls):
            label = e.spec.via_typedef.name if e.spec.via_typedef else e.spec.name
            parts.append(label)
        return " → ".join(parts)

    def method_doc(self, m: Method, owner) -> tuple[str, ClassInfo | None]:
        """A method's doc, on its prototype or its body; else that of the method it overrides,
        with the class that has it. A constructor does not take its base's."""
        if not isinstance(owner, ClassInfo):
            return m.doc, None
        for entry in self.ws.chain(owner):
            c = entry.spec.cls
            if c is None:
                continue
            for x in [*c.methods, self.ws.impls.get((c.qualname, m.name))]:
                if x is not None and x.name == m.name and x.doc:
                    return x.doc, (None if c is owner else c)
            if m.name == "new":
                break
        return m.doc, None

    def describe(self, t: Target) -> str:
        ws = self.ws
        o = t.obj
        if isinstance(o, ClassInfo):
            head = ("virtual " if o.is_virtual else "") + f"{o.kind} {o.name}"
            if o.params:
                head += " #(" + ", ".join(("type " if p.is_type else "") + p.name +
                                          (f" = {p.default}" if p.default else "") for p in o.params) + ")"
            if o.base is not None:
                head += f" extends {o.base.text}"
            lines = [f"```systemverilog\n{head}\n```"]
            facts = [f"**{ws.role(o)}**"]
            if o.package:
                facts.append(f"package `{o.package}`")
            if o.registration:
                facts.append(f"factory: {o.registration}")
            subs = ws.subclasses.get(o.qualname, [])
            if subs:
                facts.append(f"{len(subs)} subclass{'es' if len(subs) != 1 else ''}")
            lines.append(" · ".join(facts))
            lines.append(f"`{self.chain_text(o)}`")
            if o.doc:
                lines.append(naturaldocs.markdown(o.doc))
            return "\n\n".join(lines)
        if isinstance(o, Variable):
            lines = [f"```systemverilog\n{o.decl_text}\n```"]
            owner = t.owner.name if isinstance(t.owner, (ClassInfo, DesignUnit)) else t.owner
            kind = {"property": "property", "argument": "argument", "local": "local variable",
                    "port": "port", "signal": "signal", "enum_member": "enum value",
                    "clocking": "clocking block", "modport": "modport",
                    "parameter": "parameter"}.get(o.kind, o.kind)
            facts = [kind + (f" of `{owner}`" if owner and o.kind not in ("local", "argument") else "")]
            spec = self.type_of(t)
            if spec is not None:
                via = f"`{spec.via_typedef.name}` = " if spec.via_typedef else ""
                args = f" #({', '.join(spec.args)})" if spec.args and spec.via_typedef else ""
                if spec.cls is not None:
                    facts.append(f"type {via}`{spec.cls.name}{args}` ({ws.role(spec.cls)})")
                else:
                    facts.append(f"type {via}`{spec.name}{args}` ({ws.role_of_name(spec.name)})")
            if o.generated:
                facts.append("declared by a macro")
            lines.append(" · ".join(facts))
            if o.doc:
                lines.append(naturaldocs.markdown(o.doc))
            return "\n\n".join(lines)
        if isinstance(o, Method):
            if isinstance(t.owner, ClassInfo):  # an extern method's body: show its prototype
                o = next((m for m in t.owner.methods if m.name == o.name), o)
            quals = " ".join(q for q in o.qualifiers if q != "extern")
            sig = (quals + " " if quals else "") + o.signature
            lines = [f"```systemverilog\n{sig}\n```"]
            owner = t.owner.name if isinstance(t.owner, (ClassInfo, DesignUnit)) else t.owner
            facts = [f"{o.kind} of `{owner}`"] if owner else []
            doc, source = self.method_doc(o, t.owner)
            if source is not None:
                facts.append(f"doc from `{source.name}::{o.name}`")
            if facts:
                lines.append(" · ".join(facts))
            if doc:
                lines.append(naturaldocs.markdown(doc))
            return "\n\n".join(lines)
        if isinstance(o, Typedef):
            lines = [f"```systemverilog\n{o.decl_text}\n```"]
            if t.spec is not None and t.spec.cls is not None:
                lines.append(f"alias of `{t.spec.cls.name}` ({ws.role(t.spec.cls)})")
            return "\n\n".join(lines)
        if isinstance(o, DesignUnit):
            head = f"{o.kind} {o.name}"
            if o.params:
                head += " #(" + ", ".join(p.name for p in o.params) + ")"
            if o.ports:
                head += " (" + ", ".join(p.name for p in o.ports) + ")"
            lines = [f"```systemverilog\n{head}\n```"]
            if o.doc:
                lines.append(naturaldocs.markdown(o.doc))
            return "\n\n".join(lines)
        if isinstance(o, MacroDef):
            return f"```systemverilog\n{o.text}\n```"
        if isinstance(o, str):
            spec = self.type_of(t)
            lines = [f"```systemverilog\n{o} {t.note}\n```".replace(" \n```", "\n```")]
            facts = [f"member of `{t.owner}` (UVM)"]
            if spec is not None:
                shown = spec.cls.name if spec.cls else spec.name
                if parse_head(o) != shown:
                    facts.append(f"type `{shown}`")
            lines.append(" · ".join(facts))
            return "\n\n".join(lines)
        if isinstance(o, tuple) and o[0] == "uvm-macro":
            sig = f"`{o[1]}" + (f"({o[2]})" if o[2] is not None else "")
            return (f"```systemverilog\n{sig}\n```\n\nUVM macro. Set `uvm.uvmHome` to index the real "
                    "UVM library and jump to its definition.")
        if isinstance(o, tuple):
            return f"{o[0]} `{o[1]}` of `{t.owner.name if isinstance(t.owner, ClassInfo) else t.owner}`"
        return None

    # -- references ------------------------------------------------------------

    def references(self, path, line, col, text, include_decl=True) -> list[Range]:
        ce = expr_at(line_of(text, line), col)
        if ce is None:
            return []
        out = self.occurrences(ce.word)
        if not include_decl:
            decls = {(r.path, r.line, r.col) for r in self.definition(path, line, col, text)}
            out = [r for r in out if (r.path, r.line, r.col) not in decls]
        return out

    def occurrences(self, name, path=None) -> list[Range]:
        out: list[Range] = []
        seen = set()
        for idx in self.ws.indices.values():
            if path is not None and path not in idx.files:
                continue
            for r in idx.identifiers.get(name, []):
                if path is not None and r.path != path:
                    continue
                key = (r.path, r.line, r.col)
                if key not in seen:
                    seen.add(key)
                    out.append(r)
        out.sort(key=lambda r: (r.path, r.line, r.col))
        return out

    def document_highlights(self, path, line, col) -> list[Range]:
        """A keyword's matched pair, or the occurrences of an identifier in the file. The position
        just after a word is on it, unless a word starts there: editors ask with the cursor there."""
        pos = (line, col)
        structures = self.ws.structures_in(path)

        def pair_at(on):
            pairs = [p for p in structures if on(p.opening) or on(p.closing)]
            if not pairs:
                return None
            pair = min(pairs, key=lambda p: (p.closing.line - p.opening.line, p.closing.col - p.opening.col))
            return [pair.opening, pair.closing]

        def inside(r):
            return (r.line, r.col) <= pos < (r.end_line, r.end_col)

        def after(r):
            return (r.end_line, r.end_col) == pos

        if found := pair_at(inside):
            return found
        ended = None
        for idx in self.ws.indices.values():
            if path not in idx.files:
                continue
            for name, ranges in idx.identifiers.items():
                for r in ranges:
                    if r.path == path:
                        if inside(r):
                            return self.occurrences(name, path)
                        if ended is None and after(r):
                            ended = name
        if found := pair_at(after):
            return found
        return self.occurrences(ended, path) if ended is not None else []

    def folding_ranges(self, path) -> list[tuple[int, int, str | None]]:
        """(start, end, kind) of a file's folds, outer before inner on a shared start. Syntax and
        comments nest by construction, unless preprocessing roots disagree; an `ifdef branch,
        region, `uvm_*_begin block or statement body that crosses them is left out."""
        kinds: dict[tuple[int, int], str | None] = {}
        for f in sorted(self.ws.folds_in(path), key=lambda f: f.kind in CROSSING_FOLDS):
            kinds.setdefault((f.start, f.end), f.kind)
        # Different preprocessing roots can disagree about nesting.
        syntax = _nested(p for p, k in kinds.items() if k not in CROSSING_FOLDS)
        crossing = [p for p, k in kinds.items() if k in CROSSING_FOLDS]
        if crossing:
            # One that starts inside syntax and ends after it, or (mirrored) starts before and ends inside.
            dropped = _start_inside(syntax, crossing) | \
                _start_inside([(-b, -a) for a, b in syntax], [(-b, -a) for a, b in crossing])
            syntax = _nested(syntax + [p for i, p in enumerate(crossing) if i not in dropped])
        return [(start, end, kinds[start, end]) for start, end in syntax]

    # -- completion ------------------------------------------------------------

    def completions(self, path, line, col, text) -> list[dict]:
        ws = self.ws
        before = line_of(text, line)[:col]
        m = re.search(r"(`?)([A-Za-z0-9_$]*)$", before)
        prefix = m.group(2) if m else ""
        if m and m.group(1):
            return self._macro_completions(prefix)
        head = before[: len(before) - len(prefix)]
        cls, meth, unit = ws.enclosing(path, line, col)
        items: list[dict] = []
        if head.rstrip().endswith((".", "::")):
            sep = "::" if head.rstrip().endswith("::") else "."
            probe = head.rstrip()[: -len(sep)].rstrip()
            ce = expr_at(probe, len(probe))
            if ce is None:
                return []
            current = self.resolve_first(ce.segments[0], cls, meth, unit,
                                         scoped=sep == "::" and len(ce.segments) == 1)
            for i in range(1, len(ce.segments)):
                if not current:
                    break
                current = self.step(current[0], ce.segments[i], ce.seps[i - 1], cls)
            if not current:
                return []
            t = current[0]
            if isinstance(t.obj, DesignUnit):
                return self._unit_completions(t.obj)
            if isinstance(t.obj, Variable) and t.obj.kind in ("clocking", "modport") and \
                    isinstance(t.owner, DesignUnit):
                return [c for c in self._unit_completions(t.owner) if c["detail"] not in ("clocking", "modport")]
            if isinstance(t.obj, Variable) and t.obj.type is not None and t.obj.type.text.startswith("virtual"):
                iface = ws.find_unit(t.obj.type.name, "interface")
                return self._unit_completions(iface) if iface else []
            spec = self.type_of(t)
            if spec is None or spec.cls is None:
                return self._builtin_member_completions(spec.name) if spec else []
            return self._class_member_completions(spec.cls, cls)
        if meth is not None:
            for v in meth.args + meth.locals:
                items.append({"label": v.name, "kind": "variable", "detail": v.type_text})
        if cls is not None:
            items += self._class_member_completions(cls, cls)
        if unit is not None:
            items += self._unit_completions(unit)
        for c in ws.classes:
            items.append({"label": c.name, "kind": "class",
                          "detail": f"{ws.role(c)}" + (f" · {c.package}" if c.package else "")})
        for name, tds in ws.typedefs_by_name.items():
            if tds[0].owner_class is None:
                items.append({"label": name, "kind": "typedef", "detail": tds[0].target_text})
        for u in ws.units:
            items.append({"label": u.name, "kind": u.kind, "detail": u.kind})
        seen = set()
        out = []
        for it in items:
            if it["label"] not in seen and it["label"].lower().startswith(prefix.lower()[:1]):
                seen.add(it["label"])
                out.append(it)
        return out

    def _class_member_completions(self, cls: ClassInfo, ctx: ClassInfo | None) -> list[dict]:
        """The members of `cls` that code in class `ctx` (None: outside any class) can use: a
        local member only in the class that declares it, a protected one there and in subclasses."""
        inside = {e.spec.cls.qualname for e in self.ws.chain(ctx) if e.spec.cls} if ctx else set()

        def visible(c: ClassInfo, quals: list[str]) -> bool:
            if "local" in quals:
                return ctx is not None and c.qualname == ctx.qualname
            return "protected" not in quals or c.qualname in inside

        out = []
        seen = set()
        for entry in self.ws.chain(cls):
            c = entry.spec.cls
            if c is None:
                from .uvm import BUILTIN_MEMBERS
                for name, t in BUILTIN_MEMBERS.get(entry.spec.name, {}).items():
                    if name not in seen:
                        seen.add(name)
                        out.append({"label": name, "kind": "field", "detail": f"{t} ({entry.spec.name})"})
                continue
            for v in c.properties:
                if v.name not in seen and not (v.generated and v.name != "p_sequencer") \
                        and visible(c, v.qualifiers):
                    seen.add(v.name)
                    out.append({"label": v.name, "kind": "field", "detail": f"{v.type_text} ({c.name})"})
            for m in c.methods:
                if m.name not in seen and not (m.generated and m.name in UTILS_METHODS) \
                        and m.name != "new" and visible(c, m.qualifiers):
                    seen.add(m.name)
                    out.append({"label": m.name, "kind": "method", "detail": m.signature})
        return out

    def _builtin_member_completions(self, name: str) -> list[dict]:
        from .uvm import BUILTIN_MEMBERS
        out = []
        for n in self._builtin_chain(name):
            for mname, t in BUILTIN_MEMBERS.get(n, {}).items():
                out.append({"label": mname, "kind": "field", "detail": f"{t} ({n})"})
        return out

    def _unit_completions(self, unit: DesignUnit) -> list[dict]:
        out = []
        for v in unit.ports + unit.variables:
            out.append({"label": v.name, "kind": "enum" if v.kind == "enum_member" else "field",
                        "detail": v.type_text or v.kind})
        for m in unit.methods:
            out.append({"label": m.name, "kind": "method", "detail": m.signature})
        if unit.kind == "package":
            for c in self.ws.classes:
                if c.package == unit.name:
                    out.append({"label": c.name, "kind": "class", "detail": self.ws.role(c)})
        return out

    def _macro_completions(self, prefix: str) -> list[dict]:
        out = []
        seen = set()
        for name, defs in self.ws.macros_by_name.items():
            if name in seen:
                continue
            seen.add(name)
            d = defs[0]
            snippet = name
            if d.params:
                args = [a.split("=")[0].strip() for a in d.params.split(",")]
                snippet += "(" + ", ".join(f"${{{i + 1}:{a}}}" for i, a in enumerate(args)) + ")"
            out.append({"label": name, "kind": "macro", "detail": d.text[:80], "snippet": snippet})
        for name, params in shim_macros().items():
            if name in seen:
                continue
            seen.add(name)
            snippet = name
            if params:
                args = [a.split("=")[0].strip() for a in params.split(",")]
                snippet += "(" + ", ".join(f"${{{i + 1}:{a}}}" for i, a in enumerate(args)) + ")"
            out.append({"label": name, "kind": "macro", "detail": f"UVM macro {name}({params or ''})",
                        "snippet": snippet})
        return out

    # -- symbols ---------------------------------------------------------------

    def document_symbols(self, path: str) -> list[dict]:
        ws = self.ws
        syms = ws.symbols_in(path)
        units = [s for s in syms if isinstance(s, DesignUnit)]
        classes = [s for s in syms if isinstance(s, ClassInfo)]
        typedefs = [s for s in syms if isinstance(s, Typedef) and s.owner_class is None]

        def class_sym(c: ClassInfo) -> dict:
            children = []
            for v in c.properties:
                if v.generated and v.name != "p_sequencer":
                    continue
                children.append({"name": v.name, "kind": "field", "detail": v.type_text,
                                 "range": v.range, "selection": v.range})
            for m in c.methods:
                if m.generated:
                    continue
                children.append({"name": m.name, "kind": "constructor" if m.name == "new" else "method",
                                 "detail": m.return_type, "range": m.decl_range, "selection": m.range})
            for name, r in c.constraints:
                children.append({"name": name, "kind": "constraint", "detail": "constraint",
                                 "range": r, "selection": r})
            for name, r in c.covergroups:
                children.append({"name": name, "kind": "covergroup", "detail": "covergroup",
                                 "range": r, "selection": r})
            for td in c.typedefs:
                if td.range.path == path and td.name != "type_id":
                    children.append({"name": td.name, "kind": "typedef", "detail": td.target_text,
                                     "range": td.range, "selection": td.range})
            children = [ch for ch in children if ch["range"].path == path]
            return {"name": c.name, "kind": "interface" if c.kind == "interface class" else "class",
                    "detail": ws.role(c), "range": c.decl_range, "selection": c.range,
                    "children": sorted(children, key=lambda x: (x["range"].line, x["range"].col))}

        out = []
        placed = set()
        for u in units:
            children = []
            for c in classes:
                if u.decl_range.contains(c.range.line, c.range.col):
                    children.append(class_sym(c))
                    placed.add(id(c))
            for td in typedefs:
                if u.decl_range.contains(td.range.line, td.range.col):
                    children.append({"name": td.name, "kind": "typedef", "detail": td.target_text,
                                     "range": td.range, "selection": td.range})
                    placed.add(id(td))
            for v in u.ports + u.variables:
                if v.kind != "enum_member":
                    children.append({"name": v.name, "kind": "field", "detail": v.type_text or v.kind,
                                     "range": v.range, "selection": v.range})
            for m in u.methods:
                children.append({"name": m.name, "kind": "method", "detail": m.return_type,
                                 "range": m.decl_range, "selection": m.range})
            for inst in u.instances:
                children.append({"name": inst.name, "kind": "object", "detail": inst.module,
                                 "range": inst.range, "selection": inst.range})
            out.append({"name": u.name, "kind": u.kind, "detail": u.kind, "range": u.decl_range,
                        "selection": u.range,
                        "children": sorted(children, key=lambda x: (x["range"].line, x["range"].col))})
        for c in classes:
            if id(c) not in placed and c.outer_class is None:
                out.append(class_sym(c))
        for td in typedefs:
            if id(td) not in placed:
                out.append({"name": td.name, "kind": "typedef", "detail": td.target_text,
                            "range": td.range, "selection": td.range})
        for (qual, name), m in ws.impls.items():
            if m.decl_range.path == path and not any(
                    c.decl_range.contains(m.range.line, m.range.col) for c in classes):
                out.append({"name": f"{qual.split('::')[-1]}::{name}", "kind": "method",
                            "detail": m.return_type, "range": m.decl_range, "selection": m.range})
        return sorted(out, key=lambda x: (x["range"].line, x["range"].col))

    def workspace_symbols(self, query: str) -> list[dict]:
        ws = self.ws
        q = query.lower()

        def match(name: str) -> bool:
            if not q:
                return True
            n = name.lower()
            if q in n:
                return True
            it = iter(n)
            return all(ch in it for ch in q)

        out = []
        for c in ws.classes:
            if match(c.name):
                out.append({"name": c.name, "kind": "class", "container": c.package or "",
                            "range": c.range, "library": c.library})
        for u in ws.units:
            if match(u.name):
                out.append({"name": u.name, "kind": u.kind, "container": "", "range": u.range,
                            "library": u.library})
        for name, tds in ws.typedefs_by_name.items():
            for td in tds:
                if td.owner_class is None and match(name):
                    out.append({"name": name, "kind": "typedef", "container": td.package or "",
                                "range": td.range, "library": False})
        if q:
            for c in ws.classes:
                if c.library:
                    continue
                for m in c.methods:
                    if not m.generated and match(m.name):
                        out.append({"name": m.name, "kind": "method", "container": c.name,
                                    "range": m.range, "library": False})
        out.sort(key=lambda s: (s["library"], not s["name"].lower().startswith(q), len(s["name"]), s["name"]))
        return out[:500]

    def class_at(self, path, line, col, text) -> ClassInfo | None:
        for t in self.resolve(path, line, col, text):
            if isinstance(t.obj, ClassInfo):
                return t.obj
            spec = self.type_of(t)
            if spec is not None and spec.cls is not None:
                return spec.cls
        cls, _, _ = self.ws.enclosing(path, line, col)
        return cls


_SHIM_MACROS: dict[str, str | None] | None = None


def shim_macros() -> dict[str, str | None]:
    global _SHIM_MACROS
    if _SHIM_MACROS is None:
        _SHIM_MACROS = {}
        with open(SHIM_FILE, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"`define\s+(uvm_\w+)(?:\(([^)]*(?:\([^)]*\)[^)]*)*)\))?", line)
                if m:
                    _SHIM_MACROS[m.group(1)] = m.group(2)
    return _SHIM_MACROS
