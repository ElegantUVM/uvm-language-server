"""Call context and public signatures from the syntax index."""

from __future__ import annotations

import re

from .fixes import FileText
from .indexer import MASK_RE, argument_spans, mask
from .model import ClassInfo, Method
from .navigation import CursorExpr, Target
from .workspace import bind_params, substitute_params


LIMIT = 65536


def call_at(ws, path, line, col):
    if path not in ws._signature_texts:
        ws._signature_texts[path] = FileText(path, ws.texts.get(path, ""))
    ft = ws._signature_texts[path]
    cursor = ft.offset(line, col)
    if cursor is None:
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
    at = cursor - start
    for m in MASK_RE.finditer(text):
        if m.group().startswith(("//", "/*")) and m.start() <= at < m.end():
            return None
    masked = mask(text)
    prefix = masked[:at]
    if ";" in prefix or re.search(r"\b(?:endfunction|endtask|endclass)\b", prefix):
        return None
    slots = argument_spans(masked, 0, len(masked))
    # Named wrappers are not calls; named selection is handled separately.
    if any(masked[a:b].lstrip().startswith(".") for a, b in slots):
        return None
    active = sum((r.end_line, r.end_col) <= (line, col) for r in call.commas) if call.closing else \
        sum(b < at for _, b in slots)
    return call, active


def signature(nav, path, line, col):
    found = call_at(nav.ws, path, line, col)
    if found is None:
        return None
    call, active = found
    names = [s[0] for s in call.segments]
    if not names:
        return None
    if names == ["new"]:
        spec, _, _ = nav.new_target_type(path, line, col, call.target)
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
