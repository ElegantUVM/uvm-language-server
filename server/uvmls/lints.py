"""UVM-aware checks that a syntax-only tool would miss."""

from __future__ import annotations

import bisect
import re

from .model import Diag
from .uvm import COMPONENT_ROLES
from .views import config_db_report
from .workspace import Workspace, parse_type_text


UTILS_RE = re.compile(r"^uvm_(object|component)(_abstract)?(_param)?_utils(_begin)?$")
REGISTRY_RE = re.compile(r"^uvm_(object|component)_registry$")


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


BUILTIN_TYPES = {"string", "bit", "logic", "reg", "int", "integer", "byte", "shortint", "longint", "time",
                 "real", "realtime", "shortreal", "chandle", "event", "void"}
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


def ctor_problem(ws: Workspace, cls) -> tuple[str, str] | None:
    """Why the factory cannot construct a registered class: (what, message), what being 'missing'
    (no constructor), 'name-default' (an object whose name has no default) or 'other'."""
    td = next((t for t in cls.typedefs if t.name == "type_id"), None)
    reg = td.target.name if td is not None and td.target is not None else None
    if cls.is_virtual or reg not in ("uvm_component_registry", "uvm_object_registry") or misregistered(ws, cls):
        return None
    component = reg == "uvm_component_registry"
    calls = [2] if component else [0] if "UVM_OBJECT_DO_NOT_NEED_CONSTRUCTOR" in ws.sources.defines else [1, 0]
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


def lint(ws: Workspace) -> dict[str, list[Diag]]:
    out: dict[str, list[Diag]] = {}

    def add(r, msg, severity, code, **data):
        out.setdefault(r.path, []).append(Diag(r, msg, severity, code, "uvm", data=data or None))

    # config_db: a get nothing sets, or a set whose type no get uses.
    report = config_db_report(ws)
    dynamic_sets = any(e["op"] == "set" for e in report["dynamic"])
    for row in report["fields"]:
        set_types = {e["type"] for e in row["sets"]}
        for g in row["gets"]:
            r = _range(g)
            if not row["sets"] and not dynamic_sets:
                add(r, f"uvm_config_db get of '{row['field']}' ({g['type']}) has no matching set "
                       "in the indexed sources (it may come from +uvm_set_config_* on the command line)",
                    "warning", "config-db-no-set", field=row["field"], at=[r.line, r.col])
            elif row["sets"] and g["type"] not in set_types:
                add(r, f"'{row['field']}' is set as {', '.join(sorted(set_types))} but read as "
                       f"{g['type']}; uvm_config_db matches on the exact type, so this get will fail",
                    "warning", "config-db-type-mismatch", field=row["field"], at=[r.line, r.col])

    no_parent = "error" if _parent_required(ws) else "warning"
    unknown_macros: dict[str, list[int]] = {}  # lines whose meaning a macro nobody defines hides
    for d in ws.user_diagnostics():
        if d.code == "UnknownDirective":
            bisect.insort(unknown_macros.setdefault(d.range.path, []), d.range.line)
    for cls in ws.classes:
        if cls.library:
            continue
        lines = unknown_macros.get(cls.path, ())
        i = bisect.bisect_left(lines, cls.decl_range.line)
        problem = ctor_problem(ws, cls) if not (i < len(lines) and lines[i] <= cls.decl_range.end_line) else None
        if problem:
            ctor = next((m for m in cls.methods if m.name == "new"), None)
            add(ctor.range if ctor else cls.range, problem[1], "error", "factory-ctor", cls=cls.qualname)
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
            if cr.kind != "factory" or cr.type is None:
                continue
            spec = ws.resolve_spec(cr.type.name, cr.type.scope, cr.type.args, cls)
            if spec is None or spec.cls is None or spec.cls.library:
                continue
            target = spec.cls
            at = [cr.range.line, cr.range.col]
            if target.is_virtual:
                add(cr.range, f"{target.name} is a virtual class and cannot be created", "error",
                    "create-virtual", cls=cls.qualname, at=at, target=target.qualname)
            elif target.registration is None and ws.role(target) not in ("class", "interface"):
                # (A class not known to be a uvm_object, such as one whose `ifdef UVM branch is
                # not taken, is not something a macro can register.)
                macro = "`uvm_component_utils" if ws.is_component(target) else "`uvm_object_utils"
                add(cr.range, f"{target.name} is not registered with the factory (add {macro}({target.name}))",
                    "warning", "create-unregistered", target=target.qualname)
            elif ws.is_component(target) and not cr.parent_expr:
                add(cr.range, f"component {target.name} is created without a parent", no_parent,
                    "create-no-parent", cls=cls.qualname, at=at)
    return out


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
