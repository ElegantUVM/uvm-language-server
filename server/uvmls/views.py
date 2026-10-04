"""Graph-shaped views over the workspace: class graph, UVM topology, config_db."""

from __future__ import annotations

import fnmatch
import re
from collections import deque

from . import naturaldocs
from .model import ClassInfo, ConfigDbCall, Typedef, Variable
from .uvm import COMPONENT_ROLES, ROLE_ORDER, builtin_params, is_tlm_port
from .workspace import Spec, Workspace


def _loc(r) -> dict:
    if r is None:
        return {}
    return {"file": r.path, "line": r.line, "col": r.col, "endLine": r.end_line, "endCol": r.end_col}


def _params_text(cls: ClassInfo) -> str:
    if not cls.params:
        return ""
    parts = []
    for p in cls.params:
        s = ("type " if p.is_type else "") + p.name
        if p.default:
            s += f" = {p.default}"
        parts.append(s)
    return "#(" + ", ".join(parts) + ")"


# ----------------------------------------------------------------------------
# Class graph


def class_graph(ws: Workspace) -> dict:
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}

    def add_class_node(cls: ClassInfo) -> str:
        nid = cls.qualname
        if nid in nodes:
            return nid
        members = []
        # Macro-generated members (type_id, get_type, ...) are left out, except p_sequencer.
        for v in cls.properties:
            if v.generated and v.name != "p_sequencer":
                continue
            members.append({"name": v.name, "type": v.type_text, "kind": "property",
                            "generated": v.generated, "qualifiers": v.qualifiers, **_loc(v.range)})
        for m in cls.methods:
            if m.generated:
                continue
            impl = ws.impls.get((cls.qualname, m.name), m) if m.is_prototype else m
            members.append({"name": m.name, "type": m.return_type or m.kind, "kind": m.kind,
                            "generated": m.generated, "qualifiers": m.qualifiers,
                            **_loc(impl.range)})
        for name, r in cls.constraints:
            members.append({"name": name, "type": "constraint", "kind": "constraint", **_loc(r)})
        for name, r in cls.covergroups:
            members.append({"name": name, "type": "covergroup", "kind": "covergroup", **_loc(r)})
        nodes[nid] = {
            "id": nid, "name": cls.name, "label": cls.name, "kind": cls.kind,
            "role": ws.role(cls), "package": cls.package, "library": cls.library,
            "external": False, "abstract": cls.is_virtual, "registered": cls.registration,
            "params": _params_text(cls), "base": cls.base.text if cls.base else None,
            "doc": naturaldocs.plain(cls.doc), "members": members[:300], **_loc(cls.range),
        }
        return nid

    def add_typedef_node(td: Typedef, spec: Spec) -> str:
        nid = f"{td.package}::{td.name}" if td.package else td.name
        if nid not in nodes:
            role = ws.role(spec.cls) if spec.cls else ws.role_of_name(spec.name)
            nodes[nid] = {"id": nid, "name": td.name, "label": td.name, "kind": "typedef",
                          "role": role, "package": td.package, "library": False,
                          "external": False, "abstract": False, "registered": None,
                          "params": "", "base": td.target_text, "doc": "", "members": [],
                          **_loc(td.range)}
        return nid

    def add_external(name: str) -> str:
        if name not in nodes:
            nodes[name] = {"id": name, "name": name, "label": name, "kind": "external",
                           "role": ws.role_of_name(name), "package": None, "library": True,
                           "external": True, "abstract": False, "registered": None,
                           "params": "", "base": None, "doc": "", "members": []}
        return name

    def spec_node(spec: Spec, prefer_typedef: bool = True) -> str:
        td = spec.via_typedef
        if prefer_typedef and td is not None and td.owner_class is None and \
                not _is_library_path(ws, td.range.path):
            tid = add_typedef_node(td, spec)
            target = add_class_node(spec.cls) if spec.cls else add_external(spec.name)
            add_edge(tid, target, "typedef", ", ".join(spec.args) if spec.args else "")
            return tid
        return add_class_node(spec.cls) if spec.cls else add_external(spec.name)

    def add_edge(src: str, dst: str, kind: str, label: str = "", **extra) -> None:
        if src == dst and kind != "creates":
            return
        key = (src, dst, kind)
        if key in edges:
            e = edges[key]
            if label and label not in e["label"].split(", "):
                e["label"] = f"{e['label']}, {label}" if e["label"] else label
            for k, v in extra.items():
                e[k] = e.get(k) or v
            return
        edges[key] = {"id": f"{kind}:{src}->{dst}", "source": src, "target": dst,
                      "kind": kind, "label": label, **extra}

    user_classes = [c for c in ws.classes if not c.library]
    for cls in user_classes:
        add_class_node(cls)

    for cls in user_classes:
        cid = cls.qualname
        # Inheritance (the whole chain, so UVM ancestors appear too).
        prev = cid
        chain = ws.chain(cls)
        for entry in chain[1:]:
            nid = spec_node(entry.spec, prefer_typedef=entry is chain[1])
            add_edge(prev, nid, "extends")
            if nid in nodes and nodes[nid]["kind"] == "typedef":
                nid = entry.spec.key
            prev = nid
        for iface in cls.implements:
            spec = ws.resolve_spec(iface.name, iface.scope, iface.args, cls)
            add_edge(cid, spec_node(spec) if spec else add_external(iface.name), "implements")
        # Type arguments of the base class, e.g. uvm_driver #(alu_item).
        base = ws.base_spec(cls)
        if base is not None and cls.base is not None:
            pnames = [p.name for p in base.cls.params] if base.cls else \
                [p for p, _ in builtin_params(base.name)]
            for i, arg in enumerate(base.args or cls.base.args):
                spec = ws.resolve_type_text(arg, cls)
                if spec is not None:
                    label = pnames[i] if i < len(pnames) else "param"
                    add_edge(cid, spec_node(spec), "param", label)
        # Composition: properties of class type.
        for v in cls.properties:
            if v.type is None:
                continue
            spec = ws.member_type_spec(cls, v, {})
            if spec is None:
                continue
            add_edge(cid, spec_node(spec), "has", v.name, line=v.range.line, file=v.range.path)
            # Item type carried by a TLM port: monitor --ap--> item.
            if is_tlm_port(spec.name) and v.type.args:
                item = ws.resolve_type_text(v.type.args[0], cls)
                if item is not None and (item.cls is None or not item.cls.library):
                    add_edge(cid, spec_node(item), "param", v.name)
        # Factory / new creations made by this class.
        calls = ws.calls_of(cls)
        for cr in calls.creates:
            spec = None
            if cr.type is not None:
                spec = ws.resolve_spec(cr.type.name, cr.type.scope, cr.type.args, cls)
            elif cr.target_base:
                hit = ws.find_member(cls, cr.target_base)
                if hit:
                    spec = ws.member_type_spec(*hit)
            if spec is None:
                continue
            nid = spec_node(spec)
            has_key = (cid, nid, "has")
            if has_key in edges:
                edges[has_key]["created"] = True
                if cr.conditional:
                    edges[has_key]["conditional"] = True
                continue
            add_edge(cid, nid, "creates", cr.inst_name or cr.target or "",
                     conditional=cr.conditional, line=cr.range.line, file=cr.range.path)
        for ov in calls.overrides:
            a = ws.resolve_spec(ov.original, ctx=cls)
            b = ws.resolve_spec(ov.override, ctx=cls)
            src = spec_node(a) if a else add_external(ov.original)
            dst = spec_node(b) if b else add_external(ov.override)
            add_edge(src, dst, "override", f"{ov.kind} override in {cls.name}",
                     line=ov.range.line, file=ov.range.path)

    # A library class drawn as the type of something (a port, a typedef's target) shows its own
    # bases too, up to where the chains of the user classes already reach.
    walked = {e["source"] for e in edges.values() if e["kind"] == "extends"}
    for nid in [n for n, d in nodes.items() if d["library"] and n not in walked]:
        cls = ws.class_by_qual.get(nid)
        prev = nid
        for entry in ws.chain(cls)[1:] if cls is not None else []:
            nxt = spec_node(entry.spec, prefer_typedef=False)
            add_edge(prev, nxt, "extends")
            walked.add(prev)
            if nxt in walked:
                break
            prev = nxt

    # Distance from user code, so the UI can show "direct UVM bases only".
    adj: dict[str, set[str]] = {}
    for e in edges.values():
        adj.setdefault(e["source"], set()).add(e["target"])
    dist = {nid: 0 for nid, n in nodes.items() if not n["library"]}
    q = deque(dist)
    while q:
        cur = q.popleft()
        for nxt in adj.get(cur, ()):
            if nxt not in dist:
                dist[nxt] = dist[cur] + 1
                q.append(nxt)
    for nid, n in nodes.items():
        n["libDistance"] = dist.get(nid, 99)
        n["subclassCount"] = 0
    for e in edges.values():
        if e["kind"] == "extends" and e["target"] in nodes:
            nodes[e["target"]]["subclassCount"] += 1

    packages = sorted({n["package"] for n in nodes.values() if n["package"]})
    return {
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "packages": packages,
        "roles": [r for r in ROLE_ORDER if any(n["role"] == r for n in nodes.values())],
        "stats": ws.stats(),
        "version": ws.version,
    }


def _is_library_path(ws: Workspace, path: str) -> bool:
    for root, idx in ws.indices.items():
        if idx.library and path in idx.files:
            return True
    return False


# ----------------------------------------------------------------------------
# Topology (static approximation of the UVM component tree)


def list_tests(ws: Workspace) -> dict:
    tests = sorted((c for c in ws.classes if not c.library and ws.role(c) == "test"),
                   key=lambda c: c.name)
    run_tests = [name for u in ws.units for name, _ in u.run_tests if name]
    default = None
    for name in run_tests:
        if any(t.name == name for t in tests):
            default = name
            break
    if default is None:
        leaves = [t for t in tests if not ws.subclasses.get(t.qualname)]
        default = (leaves or tests)[0].name if tests else None
    return {"tests": [t.name for t in tests], "default": default, "runTest": run_tests}


def topology(ws: Workspace, test: str | None = None) -> dict:
    info = list_tests(ws)
    name = test or info["default"]
    root_cls = ws.find_class(name) if name else None
    if root_cls is None:
        return {**info, "test": None, "root": None, "connections": [], "overrides": []}

    calls = ws.calls_in_chain(root_cls)
    type_ovr: dict[str, str] = {}
    inst_ovr: list[tuple[str, str, str]] = []
    for ov in calls.overrides:
        if ov.kind == "type":
            type_ovr[ov.original] = ov.override
        elif ov.inst_path:
            inst_ovr.append((ov.inst_path, ov.original, ov.override))

    def apply_overrides(type_name: str, path: str) -> tuple[str, str | None]:
        original = type_name
        for pattern, orig, new in inst_ovr:
            pats = [pattern, f"uvm_test_top.{pattern}"]
            if orig == type_name and any(fnmatch.fnmatchcase(path, p) for p in pats):
                return new, original
        seen = set()
        while type_name in type_ovr and type_name not in seen:
            seen.add(type_name)
            type_name = type_ovr[type_name]
        return type_name, (original if type_name != original else None)

    connections: list[dict] = []
    counter = [0]

    def build(spec: Spec, inst: str, path: str, depth: int, created=None,
              overridden_from=None, stack=()) -> dict:
        counter[0] += 1
        cls = spec.cls
        node = {
            "id": path, "name": inst, "type": spec.via_typedef.name if spec.via_typedef else spec.name,
            "class": cls.qualname if cls else spec.name,
            "role": ws.role(cls) if cls else ws.role_of_name(spec.name),
            "children": [], "ports": [],
            "conditional": bool(created and created.conditional),
            "condition": created.condition if created else None,
            "inLoop": bool(created and created.in_loop),
            "overriddenFrom": overridden_from,
            **(_loc(cls.range) if cls else {}),
        }
        if created is not None:
            node["createdAt"] = _loc(created.range)
        if cls is None or depth > 16 or cls.qualname in stack or counter[0] > 5000:
            return node
        stack = stack + (cls.qualname,)
        chain_calls = ws.calls_in_chain(cls)
        # Ports declared anywhere in the chain.
        for entry in ws.chain(cls):
            c = entry.spec.cls
            if c is None:
                continue
            for v in c.properties:
                if v.type is not None and is_tlm_port(v.type.name):
                    node["ports"].append({"name": v.name, "type": v.type_text, **_loc(v.range)})
        by_target: dict[str, tuple] = {}
        for cr in chain_calls.creates:
            cspec = None
            if cr.type is not None:
                cspec = ws.resolve_spec(cr.type.name, cr.type.scope, cr.type.args, cls)
            elif cr.target_base:
                hit = ws.find_member(cls, cr.target_base)
                if hit and isinstance(hit[1], Variable):
                    cspec = ws.member_type_spec(*hit)
            if cspec is None:
                continue
            role = ws.role(cspec.cls) if cspec.cls else ws.role_of_name(cspec.name)
            if role not in COMPONENT_ROLES:
                continue
            if cr.kind == "new" and (cr.parent_expr or "") != "this":
                continue
            key = cr.target or cr.inst_name or cr.name_expr or str(len(by_target))
            by_target[key] = (cr, cspec)
        for key, (cr, cspec) in by_target.items():
            child_inst = cr.inst_name or cr.name_expr or cr.target or "?"
            child_path = f"{path}.{child_inst}"
            tname = cspec.cls.name if cspec.cls else cspec.name
            new_name, orig = apply_overrides(tname, child_path)
            if orig is not None:
                ospec = ws.resolve_spec(new_name, ctx=cls) or Spec(new_name)
                cspec = ospec
            child = build(cspec, child_inst, child_path, depth + 1, cr, orig, stack)
            child["target"] = cr.target_base
            node["children"].append(child)
        # TLM connections made inside this component.
        def endpoint(expr: str):
            parts = [p for p in re.split(r"\.", re.sub(r"\[[^\]]*\]", "", expr)) if p]
            cur = node
            while len(parts) > 1:
                tmap = {c.get("target"): c for c in cur["children"] if c.get("target")}
                if parts[0] in tmap:
                    cur = tmap[parts.pop(0)]
                else:
                    break
            return cur["id"], ".".join(parts)

        for cn in chain_calls.connects:
            if cn.kind == "alias":
                hit = ws.find_member(cls, cn.source)
                if not (hit and isinstance(hit[1], Variable) and hit[1].type is not None
                        and is_tlm_port(hit[1].type.name)):
                    continue
            src = endpoint(cn.source)
            dst = endpoint(cn.target)
            connections.append({
                "source": src[0], "sourcePort": src[1], "target": dst[0], "targetPort": dst[1],
                "kind": cn.kind, "conditional": cn.conditional, **_loc(cn.range),
            })
        return node

    root = build(Spec(root_cls.name, root_cls), "uvm_test_top", "uvm_test_top", 0)
    # Keep only connections whose endpoints exist in the built tree.
    ids = set()

    def collect(n):
        ids.add(n["id"])
        for c in n["children"]:
            collect(c)
    collect(root)
    connections = [c for c in connections if c["source"] in ids and c["target"] in ids]
    overrides = [{"kind": ov.kind, "original": ov.original, "override": ov.override,
                  "instPath": ov.inst_path, "owner": ov.owner, **_loc(ov.range)}
                 for ov in calls.overrides]
    return {**info, "test": root_cls.name, "root": root, "connections": connections,
            "overrides": overrides}


# ----------------------------------------------------------------------------
# config_db cross reference


def norm_type(t: str) -> str:
    return re.sub(r"\s+", " ", t.replace("#(", " #(")).strip()


def config_db_calls(ws: Workspace) -> list[tuple[str, ConfigDbCall]]:
    out = []
    for cls in ws.classes:
        if cls.library:
            continue
        for c in ws.calls_of(cls).config_db:
            out.append((cls.qualname, c))
    for u in ws.units:
        if u.library:
            continue
        for c in u.config_db:
            out.append((u.name, c))
    return out


def config_db_report(ws: Workspace) -> dict:
    groups: dict[str, dict] = {}
    dynamic = []
    for owner, c in config_db_calls(ws):
        entry = {"op": c.op, "db": c.db, "type": norm_type(c.type_text), "context": c.context,
                 "inst": c.inst, "instLiteral": c.inst_literal, "field": c.field_name,
                 "value": c.value, "owner": owner, "method": c.method,
                 "conditional": c.conditional, **_loc(c.range)}
        if not c.field_literal:
            dynamic.append(entry)
            continue
        g = groups.setdefault(c.field_name, {"field": c.field_name, "sets": [], "gets": []})
        (g["sets"] if c.op == "set" else g["gets"]).append(entry)
    rows = []
    for g in sorted(groups.values(), key=lambda g: g["field"]):
        set_types = {e["type"] for e in g["sets"]}
        get_types = {e["type"] for e in g["gets"]}
        if not g["sets"]:
            status = "no-set"
        elif not g["gets"]:
            status = "no-get"
        elif get_types - set_types:
            status = "type-mismatch"
        else:
            status = "ok"
        rows.append({**g, "types": sorted(set_types | get_types), "status": status})
    return {"fields": rows, "dynamic": dynamic, "version": ws.version}
