"""Quick fixes (fixes.py): the text helpers, then each fix as a round trip."""

import os

import pytest

from uvmls.fixes import Client, Context, FileText, FileTexts, fixes_for, indent_unit, insert_member, mask
from uvmls.lints import lint
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


def findings(w: Workspace, code: str) -> list:
    return sorted((d for ds in lint(w).values() for d in ds if d.code == code),
                  key=lambda d: (d.range.path, d.range.line, d.range.col))


def offered(w: Workspace, d, **client) -> list:
    roots = client.pop("roots", list(w.config.workspace_roots))
    return fixes_for(Context(w, FileTexts(), Client(roots=roots, **client), d.range.path), d)


def fix_once(w: Workspace, code: str, title: str | None = None, nth: int = 0):
    """Apply a quick fix for a finding to the files on disk and index them again. Returns the fix."""
    d = findings(w, code)[nth]
    fixes = offered(w, d)
    fix = fixes[0] if title is None else next(f for f in fixes if f.title == title)
    for path in {c.path for c in fix.changes}:
        ft = FileText.from_disk(path)
        with open(path, "w", newline="") as f:
            f.write(apply(ft.text, [c for c in fix.changes if c.path == path]))
    w.reindex_all()
    return fix


def round_trip(tmp_path, original: dict[str, str], planted: dict[str, str], code: str,
               title: str | None = None, bundled=False):
    """Plant a problem by undoing what the fix should write; the fix must give the original back,
    byte for byte, and then not be offered again."""
    w = index(tmp_path, {**original, **planted}, bundled=bundled)
    fix = fix_once(w, code, title)
    for name, text in original.items():
        with open(tmp_path / name, newline="") as f:
            assert f.read() == text, name
    assert not findings(w, code)
    return w, fix


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


# -- create-no-parent ----------------------------------------------------------------

COMPONENT = """class drv extends uvm_driver;
  `uvm_component_utils(drv)
  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction
endclass
class agent extends uvm_agent;
  `uvm_component_utils(agent)
  drv m_drv, d2;
  function new(string name, uvm_component parent);
    super.new(name, parent);
    d2 = drv::type_id::create("d2", this);
  endfunction
  function void build_phase(uvm_phase phase);
    m_drv = drv::type_id::create("m_drv", this);
  endfunction
endclass
"""


@pytest.mark.parametrize("planted", [
    ('create("m_drv", this)', 'create("m_drv")'),  # a component's method
    ('create("d2", this)', 'create("d2")'),  # its constructor
])
def test_create_no_parent_passes_this(tmp_path, planted):
    _, fix = round_trip(tmp_path, {"t.sv": COMPONENT}, {"t.sv": COMPONENT.replace(*planted)}, "create-no-parent")
    assert (fix.title, fix.preferred, fix.safe) == ("Pass 'this' as the parent", True, True)


def test_create_no_parent_after_a_comment(tmp_path):
    # The parent goes right after the name, never into a comment that ends the line.
    planted = COMPONENT.replace('create("m_drv", this)', 'create("m_drv" // its name\n    )')
    w = index(tmp_path, {"t.sv": planted})
    fix_once(w, "create-no-parent")
    assert 'create("m_drv", this // its name\n    )' in (tmp_path / "t.sv").read_text()
    assert not findings(w, "create-no-parent")


def test_create_no_parent_without_a_name(tmp_path):
    planted = COMPONENT.replace('create("d2", this)', "create()")
    _, fix = round_trip(tmp_path, {"t.sv": COMPONENT}, {"t.sv": planted}, "create-no-parent")
    assert fix.title == """Pass "d2" as the name and 'this' as the parent"""


def test_create_no_parent_passes_null_outside_a_component(tmp_path):
    text = COMPONENT + """class seq extends uvm_sequence;
  `uvm_object_utils(seq)
  drv d;
  task body(); d = drv::type_id::create("d"); endtask
endclass
class factory_fn extends uvm_agent;
  static function drv make(); return drv::type_id::create("x"); endfunction
endclass
"""
    w = index(tmp_path, {"t.sv": text})
    for d in findings(w, "create-no-parent"):
        [fix] = offered(w, d)
        assert (fix.title, fix.preferred, fix.safe) == ("Pass null as the parent", False, False)
    fix_once(w, "create-no-parent")
    fix_once(w, "create-no-parent")
    assert not findings(w, "create-no-parent")
    assert (tmp_path / "t.sv").read_text().count('create("d", null)') == 1


def test_create_no_parent_not_offered_in_a_macro_or_for_stale_text(tmp_path):
    text = COMPONENT.replace('m_drv = drv::type_id::create("m_drv", this);',
                             "`MK(drv, m_drv)\n    d2 = drv::type_id::create(\"d2\");")
    w = index(tmp_path, {"t.sv": "`define MK(T, n) n = T::type_id::create(`\"n`\");\n" + text})
    macro, plain = findings(w, "create-no-parent")
    assert offered(w, macro) == [] and len(offered(w, plain)) == 1
    # The call no longer reads as the index has it (edited since): no fix.
    (tmp_path / "t.sv").write_text((tmp_path / "t.sv").read_text().replace('create("d2")', 'create("d2" + "")'))
    assert offered(w, plain) == []


# -- compile proof: fixed code elaborates with the bundled UVM 1.2 -----------------------

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "examples", "alu_tb")


def example(tmp_path, plants: dict[str, tuple[str, str]]) -> Workspace:
    """A copy of the example with problems planted, indexed on the bundled UVM 1.2."""
    import shutil
    root = tmp_path / "alu_tb"
    shutil.copytree(EXAMPLE, root)
    for rel, (old, new) in plants.items():
        text = (root / rel).read_text()
        assert old in text, rel
        (root / rel).write_text(text.replace(old, new, 1))
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(root)], filelists=[str(root / "alu_tb.f")]))
    return w


def user_errors(w: Workspace) -> list:
    from uvmls.elab import elaborate, request_for
    r = elaborate(request_for(w))
    assert r["error"] is None
    return [(d["file"], d["line"], d["message"]) for d in r["diagnostics"] if d["severity"] == "error"]


def unchanged(tmp_path) -> bool:
    import filecmp
    cmp = filecmp.dircmp(EXAMPLE, tmp_path / "alu_tb")
    stack, same = [cmp], True
    while stack:
        c = stack.pop()
        same &= not (c.diff_files or c.left_only or c.right_only)
        stack += c.subdirs.values()
    return same


def test_create_no_parent_fix_compiles(tmp_path):
    w = example(tmp_path, {"agent/alu_agent.svh": ('create("m_driver", this)', 'create("m_driver")')})
    assert user_errors(w)  # too few arguments for create()
    [d] = findings(w, "create-no-parent")
    assert d.severity == "error"  # UVM 1.2 has no default for the parent
    fix_once(w, "create-no-parent")
    assert unchanged(tmp_path) and user_errors(w) == []
