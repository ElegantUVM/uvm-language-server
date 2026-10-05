"""UVM-aware checks that a syntax-only tool would miss."""

from __future__ import annotations

import bisect
import os
import re

from .model import Diag, Variable
from .uvm import COMPONENT_ROLES
from .views import config_db_report, mismatched
from .workspace import BUILTIN_TYPES, Workspace, bind_params, parse_type_text


UTILS_RE = re.compile(r"^uvm_(object|component)(_abstract)?(_param)?_utils(_begin)?$")
REGISTRY_RE = re.compile(r"^uvm_(object|component)_registry$")


def registration_for(ws: Workspace, cls) -> tuple[str, str]:
    """The utils macro and its argument, assigning only overridable parameters."""
    params = [p.name for p in cls.params if not p.is_local]
    macro = ("uvm_component" if ws.is_component(cls) else "uvm_object") + ("_param_utils" if params else "_utils")
    arg = cls.name + (" #(" + ", ".join(params) + ")" if params else "")
    return macro, arg


def misregistered(ws: Workspace, cls) -> str | None:
    """The registration a class needs when its own contradicts its role: 'component' for a
    component registered as an object, 'object' for the reverse."""
    role = ws.role(cls)
    if cls.registration == "object" and role in COMPONENT_ROLES:
        return "component"
    if cls.registration == "component" and role not in COMPONENT_ROLES and role not in ("class", "interface"):
        return "object"
    return None


def swapped_macro(macro: str | None, kind: str) -> str | None:
    """The registration macro for the other kind: uvm_object_param_utils -> uvm_component_param_utils."""
    if macro and (UTILS_RE.match(macro) or REGISTRY_RE.match(macro)):
        return macro.replace("component" if kind == "object" else "object", kind, 1)
    return None


# What a constructor argument may be for the factory to pass it a uvm_component.
COMPONENT_BASES = {"uvm_component", "uvm_report_object", "uvm_object", "uvm_void"}


def type_name(ws: Workspace, cls, text: str, depth: int = 0) -> str | None:
    """A type's name with typedefs followed: 'string', 'uvm_component', 'my_cfg'; None if unknown."""
    parsed = parse_type_text(text)
    if not parsed:
        return text.strip() or None  # e.g. bit [7:0]
    scope, name, args = parsed
    if name in BUILTIN_TYPES:
        return name
    td = ws.find_typedef(name, scope or cls.package, cls.name, cls.imports) if depth < 8 else None
    if td is not None and td.target_text:
        return type_name(ws, cls, td.target_text, depth + 1)
    spec = ws.resolve_spec(name, scope, args, cls)
    return spec.name if spec else None


def ctor_call_problem(ws: Workspace, cls, args: list, k: int) -> str | None:
    """Why `new(name)` (k=1), `new(name, parent)` (k=2) or `new()` (k=0) cannot call a constructor
    with these arguments, as the end of a message; None if it can."""
    if len(args) < k:
        return f"but this constructor takes {len(args)} argument(s)"
    later = next((a for a in args[k:] if a.default is None), None)
    if later is not None:
        return f"give '{later.name}' a default value"
    written = ""
    for i, (a, want) in enumerate(zip(args[:k], ("string", "uvm_component"))):
        written = a.type_text or written  # an argument without a type has the one before it
        got = type_name(ws, cls, written)
        if got is not None and (got != want if want == "string" else got not in COMPONENT_BASES):
            return f"the factory passes a {want} as argument {i + 1} of {cls.name}'s constructor, " \
                   f"but '{a.name}' is {written}"
    return None


def object_ctor_calls(ws: Workspace) -> list[int]:
    """The object constructor calls made by this UVM's registry and utils macros."""
    defines = ws.sources.defines
    if re.match(r"1\.[01](?!\d)", ws.sources.uvm_version or ""):
        return [1, 0] if {"UVM_OBJECT_MUST_HAVE_CONSTRUCTOR", "UVM_NO_DEPRECATED"} & defines.keys() else [0]
    return [0] if "UVM_OBJECT_DO_NOT_NEED_CONSTRUCTOR" in defines else [1, 0]


def ctor_problem(ws: Workspace, cls) -> tuple[str, str] | None:
    """Why the factory cannot construct a registered class: (what, message), what being 'missing'
    (no constructor), 'name-default' (an object whose name has no default) or 'other'."""
    td = next((t for t in cls.typedefs if t.name == "type_id"), None)
    reg = td.target.name if td is not None and td.target is not None else None
    if cls.is_virtual or reg not in ("uvm_component_registry", "uvm_object_registry") or misregistered(ws, cls):
        return None
    component = reg == "uvm_component_registry"
    calls = [2] if component else object_ctor_calls(ws)
    call = {2: "new(name, parent)", 1: "new(name)", 0: "new()"}
    ctor = next((m for m in cls.methods if m.name == "new"), None)
    if ctor is None:
        return None if calls == [0] else \
            ("missing", f"{cls.name} has no constructor, but the factory constructs it with {call[calls[0]]}")
    for k in calls:
        why = ctor_call_problem(ws, cls, ctor.args, k)
        if why is None:
            continue
        if why.startswith("the factory passes"):
            return "other", why
        if k == 0 and len(calls) == 2 and why == f"give '{ctor.args[0].name}' a default value":
            return "name-default", f"the factory constructs {cls.name} with new() as well as new(name); {why}"
        return "other", f"the factory constructs {cls.name} with {call[k]}" + (", " if why.startswith("but") else "; ") + why
    return None


LITERAL_RE = re.compile(r"^(\s*[-+*/()]*\s*(\d*'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ?_]+|\d[\d_]*|\"[^\"]*\")\s*[-+*/()]*)*$")


def type_refs(ws: Workspace):
    """Every type named in the project's code, with the class it is named in (None outside one)."""
    for c in ws.classes:
        if c.library:
            continue
        refs = [c.base] + [v.type for v in c.properties] + [td.target for td in c.typedefs] + \
            [cr.type for cr in ws.calls_of(c).creates]
        for m in c.methods + [ws.impls[(c.qualname, m.name)] for m in c.methods if (c.qualname, m.name) in ws.impls]:
            refs += [v.type for v in m.args + m.locals]
        for r in refs:
            if r is not None:
                yield r.name, r.scope, r.args, c
    for tds in ws.typedefs_by_name.values():
        for td in tds:
            if td.owner_class is None and td.target is not None and td.range.path not in ws.library_files:
                yield td.target.name, td.target.scope, td.target.args, None


def specializations(ws: Workspace, classes: dict[str, object]) -> dict[str, dict[tuple, str]]:
    """For parameterized classes (by name): the distinct specializations the code names with arguments
    whose values are known (a type, a literal), each as written once. One named through a parameter of
    another class (`item #(T)`) is not known here and is left out."""
    out: dict[str, dict[tuple, str]] = {}

    def named(refs):
        for name, scope, args, ctx in refs:
            yield name, scope, args, ctx
            # uvm_config_db #(cfg #(int)): cfg #(int) is named too
            yield from named((p[1], p[0], p[2], ctx) for p in map(parse_type_text, args) if p and p[2])

    for name, scope, args, ctx in named(type_refs(ws)):
        t = classes.get(name)
        if t is None or ctx is t or (ctx is not None and ctx.outer_class == t.name):
            continue  # inside its own body the name is the specialization being declared
        spec = ws.resolve_spec(name, scope, args, ctx)
        if spec is None or spec.cls is not t:
            continue
        params = [p for p in t.params if not p.is_local]
        bound = bind_params([(p.name, p.default) for p in params], args, {})
        key = []
        for p in params:
            value = bound.get(p.name, "")
            if p.is_type:
                vspec = ws.resolve_type_text(value, ctx)
                head = re.split(r"[\s\[]", value.strip(), maxsplit=1)[0]
                key.append(vspec.key if vspec else re.sub(r"\s+", "", value) if head in BUILTIN_TYPES else None)
            else:
                key.append(re.sub(r"\s+", "", value) if value and LITERAL_RE.match(value) else None)
        if None not in key:
            out.setdefault(t.qualname, {}).setdefault(tuple(key), f"{name} #({', '.join(args)})" if args else name)
    return out


def param_problem(ws: Workspace, cls, specs: dict[str, dict[tuple, str]] | None = None) -> tuple[str, str] | None:
    """(macro to use, message) when a registration macro does not fit whether the class is
    parameterized: a test with a _param macro, whose name is then not registered; or a parameterized
    class with a plain one when the code has two of its specializations, which then both register
    the same name."""
    m = UTILS_RE.match(cls.registration_macro or "")
    if not m:
        return None
    macro = cls.registration_macro
    parameterized = any(not p.is_local for p in cls.params)
    if parameterized and not m.group(3):
        if specs is None:
            specs = specializations(ws, {cls.name: cls})
        named = list(specs.get(cls.qualname, {}).values())
        if len(named) < 2:
            return None  # one specialization: its name is its own, and lookup by name works
        use = macro.replace("_utils", "_param_utils", 1)
        more = f" (and {len(named) - 2} more)" if len(named) > 2 else ""
        return use, (f"{cls.name} is parameterized but registered with `{macro}, so {named[0]} and {named[1]}{more} "
                     f"{'all' if more else 'both'} register the name \"{cls.registration_arg}\": UVM warns TPRGED and "
                     f"a lookup by name gets one of them; use `{use}")
    if not parameterized and m.group(3) and ws.role(cls) == "test":
        return macro.replace("_param", "", 1), (
            f"{cls.name} is registered with `{macro}, which does not register its name: "
            f"run_test(\"{cls.name}\") and +UVM_TESTNAME={cls.name} cannot find it")
    return None


NEW_TARGET_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_$]*)(\[[^\]]*\])*$")  # x, or an element x[i]


def new_target(ws: Workspace, cls, cr):
    """(class, factory scope) of what a `x = new(...)` makes, when that is a component of the
    project's that the factory knows. The scope is None when its spelling cannot safely be copied."""
    m = NEW_TARGET_RE.match(cr.target or "")
    if cr.kind != "new" or cr.nargs != 2 or not (m or cr.type):
        return None
    inherited = False
    if cr.type is not None:  # X::new(...)
        spec, written = ws.resolve_spec(cr.type.name, cr.type.scope, cr.type.args, cls), cr.type.text
    else:
        body = ws.impls.get((cls.qualname, cr.method)) or \
            next((x for x in cls.methods if x.name == cr.method and not x.is_prototype), None)
        var = next((v for v in body.args + body.locals if v.name == m.group(1)), None) if body else None
        if var is not None:
            spec = ws.resolve_spec(var.type.name, var.type.scope, var.type.args, cls) if var.type else None
        else:
            hit = ws.find_member(cls, m.group(1))
            var = hit[1] if hit and isinstance(hit[1], Variable) else None
            spec = ws.member_type_spec(*hit) if var is not None else None
            inherited = hit is not None and hit[0] is not cls
        written = var.type_text if var is not None else ""
    t = spec.cls if spec is not None else None
    if t is None or t.library or t is cls or t.registration != "component" or not written:
        return None
    if inherited:
        written = None  # the base's imports and type spellings need not be visible here
    elif any(not p.is_local for p in t.params) and spec.via_typedef is None and "#" not in written:
        written = written + "#()" if cr.type is None else None
    role = ws.role(t)
    return (t, written) if role in COMPONENT_ROLES and role != "tlm" else None


PHASES_WITH_SUPER = ("build_phase", "connect_phase")


def body_of(ws: Workspace, cls, name: str):
    """A class's own method with its body (the out-of-block one for an extern method), or None."""
    return ws.impls.get((cls.qualname, name)) or \
        next((m for m in cls.methods if m.name == name and not m.is_prototype), None)


def skipped_super(ws: Workspace, cls, name: str):
    """(base class, its method) when the class's phase method overrides one of a base class of the
    project's that does something, without calling it: super.build_phase() is missing."""
    if name not in PHASES_WITH_SUPER or not ws.is_component(cls):
        return None
    meth = body_of(ws, cls, name)
    if meth is None or not meth.body_items:  # an empty override is deliberate
        return None
    chain = ws.chain(cls)
    called = {c for c in meth.super_calls if c == name or c.endswith("::" + name)}
    if name in called or any(f"{e.spec.name}::{name}" in called for e in chain[1:]):
        return None
    for entry in chain[1:]:
        base = entry.spec.cls
        if base is None or base.library:
            return None  # a UVM base: skipping its build_phase is common and harmless
        found = body_of(ws, base, name)
        if found is not None:
            return (base, found) if found.body_items else None
    return None


def unknown_macro_lines(ws: Workspace) -> dict[str, list[int]]:
    """Lines whose meaning a macro nobody defines hides."""
    out: dict[str, list[int]] = {}
    for d in ws.user_diagnostics():
        if d.code == "UnknownDirective":
            bisect.insort(out.setdefault(d.range.path, []), d.range.line)
    return out


def hides_macro(unknown: dict[str, list[int]], cls) -> bool:
    lines = unknown.get(cls.path, ())
    i = bisect.bisect_left(lines, cls.decl_range.line)
    return i < len(lines) and lines[i] <= cls.decl_range.end_line


def lint(ws: Workspace) -> dict[str, list[Diag]]:
    out: dict[str, list[Diag]] = {}

    def add(r, msg, severity, code, **data):
        if code in ws.ignores.get(r.path, {}).get(r.line, ()):
            return  # `// uvm-lint: ignore code` on its line, or on a line of its own above it
        out.setdefault(r.path, []).append(Diag(r, msg, severity, code, "uvm", data=data or None))

    # config_db: a get nothing sets (worth a warning when the code needs the value, or when a set
    # of a name close to it looks like a typo), or a get of another type than what is set.
    report = config_db_report(ws)
    dynamic_sets = any(e["op"] == "set" for e in report["dynamic"])
    for row in report["fields"]:
        for g in row["gets"]:
            r = _range(g)
            if not row["sets"] and not dynamic_sets:
                near = near_misses(report, g)
                said = f"uvm_config_db get of '{row['field']}' ({g['type']}) has no matching set in the indexed sources"
                if g["optional"]:
                    said += "; the code carries on without it"
                elif g["conditional"]:
                    said += "; it is read only under a condition"
                else:
                    said += " (it may come from +uvm_set_config_* on the command line)"
                if len(near) == 1:
                    s0 = near[0][1]
                    said += f"; did you mean '{near[0][0]}' (set in {os.path.basename(s0['file'])}:{s0['line'] + 1})?"
                add(r, said, "warning" if near or not (g["optional"] or g["conditional"]) else "info",
                    "config-db-no-set", field=row["field"], at=[r.line, r.col])
        for g in mismatched(row):
            r = _range(g)
            add(r, f"'{row['field']}' is set as {row['sets'][0]['type']} but read as {g['type']}; "
                   "uvm_config_db matches on the exact type, so this get will fail",
                "warning", "config-db-type-mismatch", field=row["field"], at=[r.line, r.col])

    no_parent = "error" if _parent_required(ws) else "warning"
    unknown_macros = unknown_macro_lines(ws)
    # Parameterized classes registered by name: which of their specializations the code names.
    specs = specializations(ws, {c.name: c for c in ws.classes if not c.library and c.params and
                                 UTILS_RE.match(c.registration_macro or "") and "_param" not in c.registration_macro})
    for cls in ws.classes:
        if cls.library:
            continue
        problem = ctor_problem(ws, cls) if not hides_macro(unknown_macros, cls) else None
        if problem:
            ctor = next((m for m in cls.methods if m.name == "new"), None)
            add(ctor.range if ctor else cls.range, problem[1], "error", "factory-ctor", cls=cls.qualname)
        for name in PHASES_WITH_SUPER:
            skipped = skipped_super(ws, cls, name)
            if skipped:
                base = skipped[0]
                meth = body_of(ws, cls, name)
                arg = meth.args[0].name if meth.args else ""
                made = [cr for cr in ws.calls_of(base).creates if cr.method == name]
                made = [cr.inst_name or cr.target for cr in [c for c in made if c.parent_expr] or made]
                gets = [f"'{c.field_name}'" for c in ws.calls_of(base).config_db
                        if c.method == name and c.op == "get" and c.field_literal]
                what = f", which creates {_names(made)}," if made else \
                    f", which gets {_names(list(dict.fromkeys(gets)))} from uvm_config_db," if gets else ""
                add(meth.range, f"{name} overrides {base.name}::{name} without calling super.{name}({arg}); "
                                f"the base class's {name}{what} never runs", "warning", "phase-no-super",
                    cls=cls.qualname, method=name, base=base.qualname)
        wrong = param_problem(ws, cls, specs)
        if wrong:
            add(cls.range, wrong[1], "warning", "utils-param", cls=cls.qualname)
        kind = misregistered(ws, cls)
        if kind:
            used = f"`{cls.registration_macro}" if cls.registration_macro else \
                f"a uvm_{cls.registration}_registry type_id"
            use = swapped_macro(cls.registration_macro, kind) or f"uvm_{kind}_utils"
            add(cls.range, f"{cls.name} is {'a component' if kind == 'component' else 'not a component'} "
                           f"but is registered with {used}; use `{use}", "warning", "utils-mismatch",
                cls=cls.qualname)
        if cls.base is not None and ws.base_spec(cls) is None and not cls.base.name.startswith("uvm_"):
            add(cls.base.range or cls.range, f"base class '{cls.base.name}' was not found in the indexed sources",
                "info", "unknown-base", cls=cls.qualname)
        for cr in ws.calls_of(cls).creates:
            made = new_target(ws, cls, cr) if cr.kind == "new" else None
            if made:
                use = f"{made[1]}::type_id::create" if made[1] else "the factory's type_id::create"
                add(cr.range, f"{cr.target} is created with new(), so a factory override of {made[0].name} never "
                              f"applies here; use {use}", "warning", "create-new",
                    cls=cls.qualname, at=[cr.range.line, cr.range.col])
            if cr.kind != "factory" or cr.type is None:
                continue
            spec = ws.resolve_spec(cr.type.name, cr.type.scope, cr.type.args, cls)
            if spec is None:
                continue
            target = spec.cls
            at = [cr.range.line, cr.range.col]
            if target is None or target.library:  # a UVM class: only its parent can be missing
                role = ws.role(target) if target else ws.role_of_name(spec.name)
                if role in COMPONENT_ROLES and not cr.parent_expr:
                    add(cr.range, f"component {cr.type.name} is created without a parent", no_parent,
                        "create-no-parent", cls=cls.qualname, at=at)
                continue
            if target.is_virtual:
                add(cr.range, f"{target.name} is a virtual class and cannot be created", "error",
                    "create-virtual", cls=cls.qualname, at=at, target=target.qualname)
            elif target.registration is None and ws.role(target) not in ("class", "interface"):
                if hides_macro(unknown_macros, target):
                    continue  # the unknown macro may register it
                # (A class not known to be a uvm_object, such as one whose `ifdef UVM branch is
                # not taken, is not something a macro can register.)
                macro, arg = registration_for(ws, target)
                add(cr.range, f"{target.name} is not registered with the factory (add `{macro}({arg}))",
                    "warning", "create-unregistered", target=target.qualname)
            elif ws.is_component(target) and not cr.parent_expr:
                add(cr.range, f"component {target.name} is created without a parent", no_parent,
                    "create-no-parent", cls=cls.qualname, at=at)
    return out


def osa_distance(a: str, b: str) -> int:
    """Edits (insert, delete, change, swap two neighbours) that turn one word into the other."""
    d = [[i + j if not i * j else 0 for j in range(len(b) + 1)] for i in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def near_misses(report: dict, get: dict) -> list[tuple[str, dict]]:
    """Field names set with the get's type that its own name may be a typo of: the same but for case
    and underscores, or one edit away (two for a name of 8 or more), with the first set of each."""
    name = get["field"]
    if len(name) < 4:
        return []
    key, limit, kind = name.lower().replace("_", ""), 1 if len(name) < 8 else 2, get["canon"] or get["type"]
    out = []
    for row in report["fields"]:
        other = row["field"]
        if other == name or len(other) < 4 or not row["sets"]:
            continue
        if other.lower().replace("_", "") != key and \
                (abs(len(other) - len(name)) > limit or osa_distance(name, other) > limit):
            continue
        same_type = next((e for e in row["sets"] if (e["canon"] or e["type"]) == kind), None)
        if same_type:
            out.append((other, same_type))
    return out


def _names(names: list) -> str:
    names = [n for n in names if n][:4]
    if len(names) > 3:
        return ", ".join(names[:3]) + " and more"
    return " and ".join(names) if len(names) < 3 else f"{names[0]}, {names[1]} and {names[2]}"


def _parent_required(ws: Workspace) -> bool:
    """Whether the UVM in use has no default for create()'s parent, so that leaving it out does
    not compile (UVM 1.1, 1.2 and 1800.2 all have none; the built-in model cannot tell)."""
    reg = ws.class_by_qual.get("uvm_pkg::uvm_component_registry")
    create = next((m for m in reg.methods if m.name == "create"), None) if reg else None
    return bool(create and len(create.args) > 1 and create.args[1].default is None)


def _range(entry: dict):
    from .model import Range
    return Range(entry["file"], entry["line"], entry["col"],
                 entry.get("endLine", entry["line"]), entry.get("endCol", entry["col"] + 1))
