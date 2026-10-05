"""Editor features that resolve the code under the cursor: definition, hover,
references, completion, symbols and type hierarchy."""

from __future__ import annotations

import os
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from . import naturaldocs
from .indexer import EOL_RE, SHIM_FILE
from .model import ClassInfo, DesignUnit, MacroDef, Method, Range, Typedef, Variable
from .uvm import builtin_params
from .views import config_db_report, mismatched, norm_type
from .workspace import Spec, Workspace

WORD = re.compile(r"[A-Za-z0-9_$]")
# What the utils and field macros generate in a user class; other generated methods are API
# (a TLM port's get_next_item, item_done, put ...).
UTILS_METHODS = {"get_type", "get_object_type", "create", "get_type_name", "__m_uvm_field_automation"}


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

    def resolve_expr(self, path: str, line: int, col: int, ce: CursorExpr | None) -> list[Target]:
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
        cls, meth, unit = ws.enclosing(path, line, col)
        segs, seps = ce.segments, ce.seps
        current = self.resolve_first(segs[0], cls, meth, unit, scoped=bool(seps) and seps[0] == "::")
        for i in range(1, len(segs)):
            if not current:
                return self.global_member_search(segs[i]) if i == len(segs) - 1 else []
            current = self.step(current[0], segs[i], seps[i - 1], cls)
        return current

    def resolve_first(self, name: str, cls, meth, unit, scoped: bool) -> list[Target]:
        ws = self.ws
        if name == "this" and cls is not None:
            return [Target(cls, spec=Spec(cls.name, cls))]
        if name == "super" and cls is not None:
            base = ws.base_spec(cls)
            return [Target(base.cls or base.name, spec=base)] if base else []
        if scoped:
            pkg = ws.find_unit(name, "package")
            if pkg is not None:
                return [Target(pkg)]
        if meth is not None:
            for v in meth.args + meth.locals:
                if v.name == name:
                    return [Target(v, owner=cls)]
        if cls is not None:
            hit = ws.find_member(cls, name)
            if hit is not None:
                return [Target(hit[1], owner=hit[0], bindings=hit[2], note=name)]
            for c in self._outer_classes(cls):
                hit = ws.find_member(c, name)
                if hit is not None:
                    return [Target(hit[1], owner=hit[0], bindings=hit[2], note=name)]
        package = cls.package if cls else (unit.name if unit and unit.kind == "package" else None)
        imports = list(cls.imports) if cls else (list(unit.imports) if unit else [])
        found = ws.find_class(name, package, imports)
        if found is not None:
            return [Target(found, spec=Spec(found.name, found))]
        td = ws.find_typedef(name, package, cls.name if cls else None, imports)
        if td is not None:
            spec = ws.resolve_spec(name, ctx=cls)
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
        # Items of packages in scope, then of any package.
        scopes = [package] + imports if package else imports
        ordered = [ws.find_unit(p, "package") for p in scopes] + \
                  [u for u in ws.units if u.kind == "package"]
        for u in ordered:
            if u is None:
                continue
            for v in u.variables:
                if v.name == name:
                    return [Target(v, owner=u)]
            for m in u.methods:
                if m.name == name:
                    return [Target(m, owner=u)]
        if name.startswith("uvm_"):
            spec = ws.resolve_spec(name)
            if spec is not None:
                return [Target(spec.cls or spec.name, spec=spec)]
        return []

    def _outer_classes(self, cls: ClassInfo):
        out = []
        cur = cls
        while cur is not None and cur.outer_class:
            cur = self.ws.find_class(cur.outer_class, cur.package)
            if cur is not None:
                out.append(cur)
        return out

    def type_of(self, t: Target) -> Spec | None:
        ws = self.ws
        if t.spec is not None and isinstance(t.obj, (ClassInfo, Typedef, str)):
            return t.spec
        if isinstance(t.obj, Variable):
            ctx = t.owner if isinstance(t.owner, ClassInfo) else None
            if t.obj.type is None:
                return None
            if t.bindings and t.obj.type.name in t.bindings:
                return ws.resolve_type_text(t.bindings[t.obj.type.name], ctx)
            return ws.resolve_spec(t.obj.type.name, t.obj.type.scope, t.obj.type.args, ctx)
        if t.owner is not None and isinstance(t.obj, (Method, Typedef, str)):
            return ws.member_type_spec(t.owner, t.obj, t.bindings or {})
        return None

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
        if path not in ws.lines:
            ws.lines[path] = EOL_RE.split(ws.texts.get(path, ""))
        lines = ws.lines[path]
        for name, rng in identifiers:
            if rng.line not in missing:
                continue
            cls, _, _ = ws.enclosing(path, rng.line, rng.col)
            if cls is None or cls.library or cls.base is None or rng.line >= len(lines):
                continue
            col = len(lines[rng.line].encode("utf-8")[:rng.col].decode("utf-8", errors="replace"))
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

    def step(self, t: Target, name: str, sep: str, ctx_cls) -> list[Target]:
        ws = self.ws
        if isinstance(t.obj, DesignUnit) and t.obj.kind == "package":
            pkg = t.obj
            for c in ws.classes_by_name.get(name, []):
                if c.package == pkg.name:
                    return [Target(c, spec=Spec(c.name, c))]
            td = ws.find_typedef(name, pkg.name)
            if td is not None and td.package == pkg.name:
                return [Target(td, spec=ws.resolve_spec(name, pkg.name))]
            for v in pkg.variables:
                if v.name == name:
                    return [Target(v, owner=pkg)]
            for m in pkg.methods:
                if m.name == name:
                    return [Target(m, owner=pkg)]
            return []
        if isinstance(t.obj, Variable) and t.obj.type is not None and t.obj.type.text.startswith("virtual"):
            iface = ws.find_unit(t.obj.type.name, "interface")
            if iface is not None:
                return self._unit_member(iface, name)
        if isinstance(t.obj, DesignUnit):
            return self._unit_member(t.obj, name)
        if isinstance(t.obj, Variable) and t.obj.kind in ("clocking", "modport"):
            return self._unit_member(t.owner, name) if isinstance(t.owner, DesignUnit) else []
        spec = self.type_of(t)
        if spec is None:
            return self.global_member_search(name)
        if spec.cls is not None:
            hit = ws.find_member(spec.cls, name)
            if hit is not None:
                return [Target(hit[1], owner=hit[0], bindings=hit[2], note=name)]
            return []
        # A built-in UVM class: walk its built-in chain for known members.
        for entry_name in self._builtin_chain(spec.name):
            from .uvm import BUILTIN_MEMBERS, builtin_params
            mt = BUILTIN_MEMBERS.get(entry_name, {}).get(name)
            if mt is not None:
                from .workspace import bind_params
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
        out: list[Range] = []
        seen = set()
        for idx in self.ws.indices.values():
            for r in idx.identifiers.get(ce.word, []):
                key = (r.path, r.line, r.col)
                if key not in seen:
                    seen.add(key)
                    out.append(r)
        if not include_decl:
            decls = {(r.path, r.line, r.col) for r in self.definition(path, line, col, text)}
            out = [r for r in out if (r.path, r.line, r.col) not in decls]
        out.sort(key=lambda r: (r.path, r.line, r.col))
        return out

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
