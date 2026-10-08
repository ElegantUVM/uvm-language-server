"""Call context and public signatures from the syntax index."""

from __future__ import annotations

import re

from .fixes import FileText
from .indexer import MASK_RE, NOT_EOL_RE, argument_spans, mask
from .model import ClassInfo, Method
from .navigation import CursorExpr, Target
from .workspace import bind_params, substitute_params


LIMIT = 65536


def call_at(ws, path, line, col):
    if path not in ws._signature_texts:
        ft = FileText(path, ws.texts.get(path, ""))
        directives = [span for r in ws.directives_by_file.get(path, ()) if (span := ft.span(r)) is not None]
        ws._signature_texts[path] = ft, directives
    ft, directives = ws._signature_texts[path]
    cursor = ft.offset(line, col)
    if cursor is None or any(a <= cursor < b for a, b in directives):
        return None
    candidates = []
    for call in ws.invocations_by_file.get(path, ()):
        opening = ft.span(call.opening)
        if opening is None or cursor < opening[1]:
            continue
        closing = ft.span(call.closing) if call.closing else None
        boundary = ft.span(call.boundary)
        end = closing[0] if closing else boundary[1] if boundary else opening[1]
        if opening[1] <= cursor <= end and cursor - opening[1] <= LIMIT:
            candidates.append((opening[1], end, call))
    if not candidates:
        return None
    start, end, call = max(candidates, key=lambda c: c[0])
    text = ft.text[start:min(end, start + LIMIT)]
    # Directive syntax includes skipped branches; raw text must not reintroduce their arguments.
    for a, b in directives:
        a, b = max(0, a - start), min(len(text), b - start)
        if a < b:
            text = text[:a] + NOT_EOL_RE.sub(" ", text[a:b]) + text[b:]
    at = cursor - start
    for m in MASK_RE.finditer(text):
        if m.group().startswith(("//", "/*")) and m.start() <= at < m.end():
            return None
    masked = mask(text)
    prefix = masked[:at]
    if ";" in prefix or re.search(r"\b(?:endfunction|endtask|endclass)\b", prefix):
        return None
    slots = argument_spans(masked, 0, len(masked))
    active = sum((r.end_line, r.end_col) <= (line, col) for r in call.commas) if call.closing else \
        sum(b < at for _, b in slots)
    name = call.names[active] if active < len(call.names) else None
    if active < len(slots):
        a, b = slots[active]
        argument = masked[a:b].lstrip()
        if argument.startswith("."):
            label = re.match(r"\.\s*([A-Za-z_$][\w$]*)\s*\(", argument)
            if label is None:
                return None
            name = label.group(1)
    return call, active, name


def signature(nav, path, line, col):
    found = call_at(nav.ws, path, line, col)
    if found is None:
        return None
    call, active, name = found
    names = [s[0] for s in call.segments]
    if not names:
        return None
    if names == ["new"]:
        cls, _, _ = nav.ws.enclosing(path, call.head.line, call.head.col)
        creates = nav.ws.calls_of(cls).creates if cls else ()
        create = next((cr for cr in creates if cr.kind == "new" and cr.range.path == path and
                       cr.range.contains(call.head.line, call.head.col)), None)
        spec, _, _ = nav.new_target_type(path, call.head.line, call.head.col,
                                         create.target if create else call.target, create.type if create else None)
        if spec is None or spec.cls is None:
            return None
        bindings = bind_params([(p.name, p.default) for p in spec.cls.params], spec.args, {})
        hit = nav.ws.find_member(spec.cls, "new", bindings)
        targets = [Target(hit[1], owner=hit[0], bindings=hit[2])] if hit else []
    else:
        ce = CursorExpr(names[-1], names, [s[2] for s in call.segments[1:]], False, 0, 0,
                        [s[1] for s in call.segments])
        targets = nav.resolve_expr(path, call.head.line, call.head.col, ce, strict=True)
    if len(targets) != 1 or not isinstance(targets[0].obj, Method):
        return None
    target = targets[0]
    method = target.obj
    if isinstance(target.owner, ClassInfo):
        method = next((m for m in target.owner.methods if m.name == method.name), method)
    if name is not None:
        active = next((i for i, arg in enumerate(method.args) if arg.name == name), None)
        if active is None:
            return None
    bindings = target.bindings or {}
    head = method.kind + " "
    if method.return_type:
        head += substitute_params(method.return_type, bindings) + " "
    head += method.name + "("
    formals = []
    for arg in method.args:
        label = (arg.direction + " " if arg.direction else "")
        label += substitute_params(arg.type_text, bindings) + " " + arg.name
        label += substitute_params(arg.dimensions, bindings)
        if arg.default is not None:
            label += " = " + substitute_params(arg.default, bindings)
        formals.append(label)
    return target, method, head + ", ".join(formals) + ")", formals, min(active, len(formals) - 1)
