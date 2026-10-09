"""Call context and public signatures from the syntax index."""

from __future__ import annotations

import re

from .fixes import FileText
from .indexer import MASK_RE, NOT_EOL_RE, argument_spans, mask
from .model import ClassInfo, Method
from .navigation import CursorExpr
from .workspace import substitute_params


LIMIT = 65536
# What may follow an unfinished call: blanks, comments and a backtick slang skips as no directive.
GAP_RE = re.compile(r"(?:\s+|//[^\r\n]*|/\*.*?\*/|`(?![A-Za-z_]))*", re.S)


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
        boundary = ft.span(call.boundary) if call.boundary else None
        if closing:
            end = closing[0]
        elif boundary:
            end = boundary[1]
            # Typing goes on in the blanks, comments and inactive text before the next token.
            while cursor > end:
                gap = GAP_RE.match(ft.text, end, opening[1] + LIMIT).end()
                end = next((b for a, b in directives if a == gap < b), gap)
                if end == gap:
                    break
        else:
            end = opening[1]
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
        comment = m.group()
        # A line comment, or a block comment left open, goes on at its end.
        if comment.startswith(("//", "/*")) and (m.start() <= at < m.end() or at == m.end() and (
                comment.startswith("//") or len(comment) < 4 or not comment.endswith("*/"))):
            return None
    masked = mask(text)
    prefix = masked[:at]
    if ";" in prefix or re.search(r"\b(?:endfunction|endtask|endclass)\b", prefix):
        return None
    slots = argument_spans(masked, 0, len(masked))
    slot = sum(end <= (line, col) for end in call.commas) if call.closing else \
        sum(b < at for _, b in slots)
    # Commas a macro writes separate arguments too, though the text does not show them.
    active = slot + sum(end <= (line, col) for end in call.macro_commas)
    name = call.names[active] if active < len(call.names) else None
    if slot < len(slots):
        a, b = slots[slot]
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
        targets = nav.constructor_at(path, call.head.line, call.head.col, strict=True, call=call) or []
    else:
        ce = CursorExpr(names[-1], names, [s[2] for s in call.segments[1:]], False, 0, 0,
                        [list(s[1]) for s in call.segments])
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
    # Inside a generic class its own parameters are unbound: show them, not their defaults.
    bindings = {name: getattr(value, "formal", None) or value for name, value in (target.bindings or {}).items()}
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
