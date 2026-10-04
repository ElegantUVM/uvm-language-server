"""Doc comments in UVM's NaturalDocs style, as Markdown for hovers or as plain text.

A topic line names what a comment documents (`Function: get_next_item`, `CLASS: uvm_driver`),
`<name>` links to another topic, `~name~` marks code, `*text*` is bold, a line starting with `|`
is example code, and `term - text` lines make a definition list.
"""

from __future__ import annotations

import re

TOPIC = re.compile(  # `Function: name`, or `Function- name` for an internal one
    r"^([A-Za-z]+)(?:[ \t]*:|-[ \t])[ \t]*(\S.*)$", re.MULTILINE)
KEYWORDS = {
    "class", "function", "task", "method", "variable", "var", "port", "export", "imp", "property",
    "field", "typedef", "type", "enum", "constant", "const", "parameter", "param", "macro", "define",
    "constraint", "covergroup", "package", "module", "interface", "program", "group", "topic",
    "title", "section",
}
TOPIC_NAME = re.compile(r"[\w$]+(?:\s*::\s*[\w$]+)*")
INLINE = re.compile(r"<([A-Za-z_$][\w$:.#()-]*)>|~([^~\s](?:[^~]*[^~\s])?)~"
                    r"|(?<!\w)\*([^*\s](?:[^*]*[^*\s])?)\*(?!\w)")
SPECIAL = re.compile(r"[\\`*<>\[\]]|(?<!\w)_|_(?!\w)")  # an intraword _ is not emphasis
BULLET = re.compile(r"[-*+o] (.+)")
DEFINITION = re.compile(r"(\S+(?: \S+){0,2}) - (.+)")


def _topics(text: str):
    """The topic lines of a comment."""
    return (m for m in TOPIC.finditer(text) if m.group(1).lower() in KEYWORDS)


def topic(text: str, name: str) -> str | None:
    """What a comment says about `name`: the lines after the topic line naming it, up to the next
    topic line. None when no topic line names it."""
    start = None
    for m in _topics(text):
        if start is not None:
            return text[start:m.start()].strip()
        head = TOPIC_NAME.match(m.group(2))
        if name and head and re.split(r"\s*::\s*", head.group(0))[-1] == name:
            start = m.end()
    return None if start is None else text[start:].strip()


def untitled(text: str) -> str:
    """A comment without the topic line at its top."""
    first = next(_topics(text), None)
    return text[first.end():].strip() if first is not None and first.start() == 0 else text


def _dedent(lines: list[str]) -> list[str]:
    width = min((len(s) - len(s.lstrip()) for s in lines if s.strip()), default=0)
    return [s[width:].rstrip() for s in lines]


def _escape(text: str) -> str:
    return SPECIAL.sub(r"\\\g<0>", text)


def _inline(text: str) -> str:
    out, pos = [], 0
    for m in INLINE.finditer(text):
        code = m.group(1) or m.group(2)
        out.append(_escape(text[pos:m.start()]))
        if code is None:
            out.append(f"**{_escape(m.group(3))}**")
        else:
            out.append(f"`` {code} ``" if "`" in code else f"`{code}`")
        pos = m.end()
    out.append(_escape(text[pos:]))
    return "".join(out)


def markdown(doc: str) -> str:
    """Markdown that reads as the comment does: example code in a code block, links and ~names~
    as code, bullets and definitions as list items, and nothing taken for HTML or emphasis."""
    out: list[str] = []
    code: list[str] = []
    listing = False  # in a list, the next `term - text` line is the next item
    for line in doc.split("\n") + [""]:
        if line.startswith("|"):
            code.append(line[1:])
            continue
        if code:
            out += ["```systemverilog", *_dedent(code), "```"]
            code = []
        bullet = BULLET.fullmatch(line)
        entry = DEFINITION.fullmatch(line) if listing or not out or not out[-1] else None
        if not line:
            listing = False
            out.append("")
        elif bullet:
            listing = True
            out.append("- " + _inline(bullet.group(1)))
        elif entry:
            listing = True
            out.append(f"- **{_inline(entry.group(1))}** – {_inline(entry.group(2))}")
        else:
            out.append(re.sub(r"^[#=+-]", r"\\\g<0>", _inline(line)))
    return "\n".join(out).strip()


def plain(doc: str) -> str:
    """Plain text: a paragraph on one line, links and ~names~ without their marks, example code
    indented on lines of its own."""
    out: list[str] = []
    code: list[str] = []
    joins = False  # the last line is prose the next one continues
    for line in doc.split("\n") + [""]:
        if line.startswith("|"):
            code.append(line[1:])
            continue
        if code:
            out += ["    " + s for s in _dedent(code)]
            code, joins = [], False
        text = INLINE.sub(lambda m: m.group(m.lastindex), line)
        if joins and line and not BULLET.fullmatch(line) and not DEFINITION.fullmatch(line):
            out[-1] += " " + text
        else:
            out.append(text)
            joins = bool(line)
    return "\n".join(out).strip()
