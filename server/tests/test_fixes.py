"""Quick fixes (fixes.py): the text helpers, then each fix as a round trip."""

import os

import pytest

from uvmls.fixes import FileText, indent_unit, insert_member, mask
from uvmls.project import ProjectConfig
from uvmls.workspace import Workspace


def index(tmp_path, files: dict[str, str | bytes], bundled=False, **cfg) -> Workspace:
    """A workspace on files written to tmp_path (the built-in UVM model unless bundled)."""
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            p.write_bytes(text)
        else:
            with open(p, "w", newline="") as f:  # keep the line ends as given
                f.write(text)
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)], bundled_uvm=bundled, **cfg))
    return w


def apply(text: str, changes) -> str:
    for c in sorted(changes, key=lambda c: c.start, reverse=True):
        text = text[:c.start] + c.text + text[c.end:]
    return text


# -- helpers ---------------------------------------------------------------------


def test_mask_blanks_comments_and_strings_in_place():
    text = 'a = "x;y // z"; // c;\n/* b;\n c */ d; "q\\"r";'
    m = mask(text)
    assert len(m) == len(text) and m.count("\n") == 2
    assert m == 'a = "' + " " * 8 + '";      \n     \n      d; "    ";'


def test_index_columns_are_utf8_bytes(tmp_path):
    # slang counts bytes; editors count code points (and VS Code UTF-16 units).
    ft = FileText("x", "/*é*/ f = foo;\r\nx = '😀'; y = 1;\n")
    assert ft.offset(0, 17 - 6) == len("/*é*/ f = ") and ft.text[ft.offset(0, 11):].startswith("foo")
    assert ft.offset(1, len("x = '😀'; ".encode())) == ft.starts[1] + len("x = '😀'; ")
    assert ft.line(0) == "/*é*/ f = foo;" and ft.eol(0) == "\r\n" and ft.eol(1) == "\n"
    assert ft.offset(2, 0) == len(ft.text) and ft.offset(3, 0) is None and ft.offset(0, 99) is None
    # On disk, a Latin-1 byte is one column, and one character of the text.
    p = tmp_path / "l1.sv"
    p.write_bytes(b"// caf\xe9\r\nint x;\r\n")
    ft = FileText.from_disk(str(p))
    assert ft.text == "// caf�\r\nint x;\r\n" and ft.offset(0, 7) == 7 and ft.offset(1, 4) == 13
    # A byte order mark: slang counts it, editors do not show it.
    p.write_bytes(b"\xef\xbb\xbfclass a;")
    ft = FileText.from_disk(str(p))
    assert ft.text == "class a;" and ft.offset(0, 9) == 6


def test_line_ends_and_indentation_unit():
    assert FileText("x", "a\r\nb").eol(1) == "\r\n" and FileText("x", "a").eol(0) == "\n"
    assert FileText("x", "a\rb\r").eol(0) == "\r"
    assert indent_unit("\t", "\tx\n") == "\t"
    assert indent_unit("", "x\n   y\n      z\n") == "   "
    assert indent_unit("  ", "  x\n    y\n") == "  "
    assert indent_unit("", "x\n    y\n") == "    " and indent_unit("", "x\n") == "  "
    assert indent_unit("", "x\n" + " " * 12 + "y\n") == " " * 8


LAYOUTS = {
    "normal": ("class foo extends uvm_object;\n  int x;\nendclass\n",
               "class foo extends uvm_object;\n  `M\n\n  int x;\nendclass\n"),
    "one line": ("class foo extends uvm_object; endclass\n",
                 "class foo extends uvm_object; `M endclass\n"),
    "multi-line header": ("class foo #(\n    type T = int\n) extends uvm_object;\n\n  T x;\nendclass\n",
                          "class foo #(\n    type T = int\n) extends uvm_object;\n  `M\n\n  T x;\nendclass\n"),
    "trailing comment": ("class foo extends uvm_object; // the foo; really\n  int x;\nendclass\n",
                         "class foo extends uvm_object; // the foo; really\n  `M\n\n  int x;\nendclass\n"),
    "block comment after": ("class foo extends uvm_object; /* starts\n  here */\n  int x;\nendclass\n",
                            "class foo extends uvm_object; `M /* starts\n  here */\n  int x;\nendclass\n"),
    "empty body": ("package p;\n  class foo extends uvm_object;\n  endclass\nendpackage\n",
                   "package p;\n  class foo extends uvm_object;\n    `M\n  endclass\nendpackage\n"),
    "tabs": ("package p;\n\tclass foo extends uvm_object;\n\tendclass\nendpackage\n",
             "package p;\n\tclass foo extends uvm_object;\n\t\t`M\n\tendclass\nendpackage\n"),
    "crlf": ("class foo extends uvm_object;\r\n  int x;\r\nendclass\r\n",
             "class foo extends uvm_object;\r\n  `M\r\n\r\n  int x;\r\nendclass\r\n"),
    "semicolon in a string": ('class foo #(string S = "a;b") extends uvm_object;\n  int x;\nendclass\n',
                              'class foo #(string S = "a;b") extends uvm_object;\n  `M\n\n  int x;\nendclass\n'),
    "members deeper": ("class foo extends uvm_object;\n      int x;\nendclass\n",
                       "class foo extends uvm_object;\n      `M\n\n      int x;\nendclass\n"),
}


@pytest.mark.parametrize("layout", LAYOUTS)
def test_insert_member(tmp_path, layout):
    before, after = LAYOUTS[layout]
    w = index(tmp_path, {"t.sv": before})
    cls = w.find_class("foo")
    ft = FileText.from_disk(str(tmp_path / "t.sv"))
    assert apply(ft.text, [insert_member(ft, cls, "`M")]) == after


def test_insert_member_checks_the_text(tmp_path):
    w = index(tmp_path, {"t.sv": "class foo extends uvm_object;\n  int x;\nendclass\n"})
    cls = w.find_class("foo")
    assert insert_member(FileText("t", "class foo extends uvm_object:\n  int x;\nendclass\n"), cls, "`M") is None
    assert insert_member(FileText("t", "class foo"), cls, "`M") is None
