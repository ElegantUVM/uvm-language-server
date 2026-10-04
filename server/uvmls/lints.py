"""UVM-aware checks that a syntax-only tool would miss."""

from __future__ import annotations

from .model import Diag
from .uvm import COMPONENT_ROLES
from .views import config_db_report
from .workspace import Workspace


def lint(ws: Workspace) -> dict[str, list[Diag]]:
    out: dict[str, list[Diag]] = {}

    def add(r, msg, severity, code):
        out.setdefault(r.path, []).append(Diag(r, msg, severity, code, "uvm"))

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
                    "warning", "config-db-no-set")
            elif row["sets"] and g["type"] not in set_types:
                add(r, f"'{row['field']}' is set as {', '.join(sorted(set_types))} but read as "
                       f"{g['type']}; uvm_config_db matches on the exact type, so this get will fail",
                    "warning", "config-db-type-mismatch")

    for cls in ws.classes:
        if cls.library:
            continue
        role = ws.role(cls)
        is_comp = role in COMPONENT_ROLES
        if cls.registration == "object" and is_comp:
            add(cls.range, f"{cls.name} is a component but is registered with `uvm_object_utils; "
                           "use `uvm_component_utils", "warning", "utils-mismatch")
        elif cls.registration == "component" and role not in COMPONENT_ROLES and role not in ("class", "interface"):
            add(cls.range, f"{cls.name} is not a component but is registered with `uvm_component_utils; "
                           "use `uvm_object_utils", "warning", "utils-mismatch")
        if cls.base is not None and ws.base_spec(cls) is None and not cls.base.name.startswith("uvm_"):
            add(cls.base.range or cls.range, f"base class '{cls.base.name}' was not found in the indexed sources",
                "info", "unknown-base")
        for cr in ws.calls_of(cls).creates:
            if cr.kind != "factory" or cr.type is None:
                continue
            spec = ws.resolve_spec(cr.type.name, cr.type.scope, cr.type.args, cls)
            if spec is None or spec.cls is None or spec.cls.library:
                continue
            target = spec.cls
            if target.is_virtual:
                add(cr.range, f"{target.name} is a virtual class and cannot be created", "error",
                    "create-virtual")
            elif target.registration is None:
                macro = "`uvm_component_utils" if ws.is_component(target) else "`uvm_object_utils"
                add(cr.range, f"{target.name} is not registered with the factory (add {macro}({target.name}))",
                    "warning", "create-unregistered")
            elif ws.is_component(target) and not cr.parent_expr:
                add(cr.range, f"component {target.name} is created without a parent", "info",
                    "create-no-parent")
    return out


def _range(entry: dict):
    from .model import Range
    return Range(entry["file"], entry["line"], entry["col"],
                 entry.get("endLine", entry["line"]), entry.get("endCol", entry["col"] + 1))
