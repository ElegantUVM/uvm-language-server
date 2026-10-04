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


# -- create-unregistered -------------------------------------------------------------

ENV = """import uvm_pkg::*;
class mon extends uvm_monitor;
  `uvm_component_utils(mon)

  function new(string name, uvm_component parent); super.new(name, parent); endfunction
endclass
class env extends uvm_env;
  `uvm_component_utils(env)
  mon m;
  cfg c;
  function void build_phase(uvm_phase phase);
    m = mon::type_id::create("m", this);
    c = cfg::type_id::create("c");
  endfunction
endclass
"""
CFG = """class cfg extends uvm_object;
  `uvm_object_utils(cfg)

  int n;
endclass
"""


def test_register_a_component_in_the_same_file(tmp_path):
    files = {"env.sv": ENV, "cfg.svh": CFG}
    planted = {"env.sv": ENV.replace("  `uvm_component_utils(mon)\n", "")}
    _, fix = round_trip(tmp_path, files, planted, "create-unregistered")
    assert (fix.title, fix.preferred, fix.safe) == ("Add `uvm_component_utils(mon) to class 'mon'", True, True)


def test_register_an_object_in_another_file(tmp_path):
    files = {"env.sv": ENV.replace("\n", '\n`include "cfg.svh"\n', 1), "cfg.svh": CFG}
    _, fix = round_trip(tmp_path, files, {"cfg.svh": CFG.replace("  `uvm_object_utils(cfg)\n", "")},
                        "create-unregistered")
    assert fix.title == "Add `uvm_object_utils(cfg) to class 'cfg' (cfg.svh)"
    assert [c.path for c in fix.changes] == [str(tmp_path / "cfg.svh")]
    assert fix.preferred and not fix.safe  # it edits another file than the finding's


def test_register_a_parameterized_class_and_a_nested_one(tmp_path):
    text = """import uvm_pkg::*;
class drv #(type T = int, int N = 2) extends uvm_driver;
  T x;
endclass
class outer extends uvm_object;
  `uvm_object_utils(outer)
  class inner extends uvm_object;
  endclass
  inner i;
endclass
class env extends uvm_env;
  `uvm_component_utils(env)
  drv #(int, 3) d;
  outer::inner i;
  function void build_phase(uvm_phase phase);
    d = drv #(int, 3)::type_id::create("d", this);
    i = outer::inner::type_id::create("i");
  endfunction
endclass
"""
    w = index(tmp_path, {"t.sv": text})
    titles = [offered(w, d)[0].title for d in findings(w, "create-unregistered")]
    assert titles == ["Add `uvm_component_param_utils(drv #(T, N)) to class 'drv'",
                      "Add `uvm_object_utils(inner) to class 'inner'"]
    fix_once(w, "create-unregistered")
    fix_once(w, "create-unregistered")
    assert not findings(w, "create-unregistered")
    after = (tmp_path / "t.sv").read_text()
    assert "extends uvm_driver;\n  `uvm_component_param_utils(drv #(T, N))\n\n  T x;" in after
    assert "  class inner extends uvm_object;\n    `uvm_object_utils(inner)\n  endclass" in after


@pytest.mark.parametrize("cfg_text", [
    CFG.replace("  `uvm_object_utils(cfg)\n\n  int n;", "  int n;\n  `uvm_field_int(n, UVM_ALL_ON)"),  # needs _begin/_end
    "`define DECL(n) class n extends uvm_object;\n`DECL(cfg)\n  int n;\nendclass\n",  # header from a macro
])
def test_register_not_offered(tmp_path, cfg_text):
    w = index(tmp_path, {"env.sv": ENV.replace("\n", '\n`include "cfg.svh"\n', 1), "cfg.svh": cfg_text})
    [d] = findings(w, "create-unregistered")
    assert offered(w, d) == []


def test_no_finding_for_a_class_that_is_not_a_uvm_object(tmp_path):
    # Usually the `ifndef UVM branch of a class, taken for want of a define the build sets.
    w = index(tmp_path, {"env.sv": ENV.replace("\n", '\n`include "cfg.svh"\n', 1),
                         "cfg.svh": "class cfg;\n  int n;\nendclass\n"})
    assert findings(w, "create-unregistered") == []


def test_register_needs_the_uvm_macros_to_compile(tmp_path):
    # In a package that does not import uvm_pkg, the macros cannot name uvm_object_registry.
    text = "package p;\n  class cfg extends uvm_pkg::uvm_object;\n  endclass\n" \
           "  class t extends uvm_pkg::uvm_test;\n    cfg c;\n" \
           '    function void build_phase(uvm_pkg::uvm_phase phase); c = cfg::type_id::create("c"); endfunction\n' \
           "  endclass\nendpackage\n"
    w = index(tmp_path, {"p.sv": text})
    [d] = findings(w, "create-unregistered")
    assert offered(w, d) == []
    # Another class of the package is registered through them: it compiles there.
    w = index(tmp_path, {"p.sv": text.replace("  class t extends uvm_pkg::uvm_test;\n",
                                              "  class t extends uvm_pkg::uvm_test;\n    `uvm_component_utils(t)\n")})
    [d] = findings(w, "create-unregistered")
    assert [f.title for f in offered(w, d)] == ["Add `uvm_object_utils(cfg) to class 'cfg'"]


def test_register_a_class_outside_the_workspace(tmp_path):
    w = index(tmp_path, {"ws/env.sv": ENV.replace("\n", '\n`include "../lib/cfg.svh"\n', 1),
                         "lib/cfg.svh": CFG.replace("  `uvm_object_utils(cfg)\n", "")})
    [d] = findings(w, "create-unregistered")
    roots = [str(tmp_path / "ws")]
    assert offered(w, d, roots=roots) == []
    [fix] = offered(w, d, roots=roots, disabled_support=True)
    assert fix.disabled == f"'cfg' is declared outside the workspace ({tmp_path / 'lib' / 'cfg.svh'})"
    assert not fix.changes and not fix.preferred


# -- utils-mismatch --------------------------------------------------------------------

DRV = """import uvm_pkg::*;
class drv extends uvm_driver;
  int x;
  `uvm_component_utils_begin(drv)
    `uvm_field_int(x, UVM_ALL_ON)
  `uvm_component_utils_end
endclass
class pdrv #(type T = int) extends uvm_driver;
  `uvm_component_param_utils_begin(pdrv #(T))
  `uvm_component_utils_end
endclass
class item extends uvm_sequence_item;
  `uvm_object_utils(item)
endclass
"""


@pytest.mark.parametrize("old,new,title", [
    ("`uvm_component_utils_begin(drv)", "`uvm_object_utils_begin(drv)", None),
    ("`uvm_component_param_utils_begin(pdrv #(T))", "`uvm_object_param_utils_begin(pdrv #(T))",
     "Change `uvm_object_param_utils_begin to `uvm_component_param_utils_begin"),
    ("`uvm_object_utils(item)", "`uvm_component_utils(item)", "Change `uvm_component_utils to `uvm_object_utils"),
])
def test_utils_mismatch_swaps_object_and_component(tmp_path, old, new, title):
    planted = DRV.replace(old, new)
    if "_begin(drv)" in old:
        planted = planted.replace("`uvm_component_utils_end", "`uvm_object_utils_end", 1)
        title = ("Change `uvm_object_utils_begin to `uvm_component_utils_begin and "
                 "`uvm_object_utils_end to `uvm_component_utils_end")
    w, fix = round_trip(tmp_path, {"t.sv": DRV}, {"t.sv": planted}, "utils-mismatch")
    assert (fix.title, fix.preferred, fix.safe) == (title, True, True)


def test_utils_mismatch_names_the_macro(tmp_path):
    w = index(tmp_path, {"t.sv": DRV.replace("`uvm_component_param_utils_begin", "`uvm_object_param_utils_begin")})
    [d] = findings(w, "utils-mismatch")
    assert d.message == "pdrv is a component but is registered with `uvm_object_param_utils_begin; " \
                        "use `uvm_component_param_utils_begin"


def test_utils_mismatch_not_offered(tmp_path):
    text = """import uvm_pkg::*;
`define MY_UTILS(T) `uvm_object_utils(T)
class a extends uvm_driver;
  typedef uvm_object_registry #(a) type_id;
endclass
class b extends uvm_driver;
  `MY_UTILS(b)
endclass
"""
    w = index(tmp_path, {"t.sv": text})
    a, b = findings(w, "utils-mismatch")
    assert a.message == "a is a component but is registered with a uvm_object_registry type_id; use `uvm_component_utils"
    assert offered(w, a) == [] and offered(w, b) == []
    # A macro the UVM in use does not have (1.2 has no _abstract_ ones): nothing to swap to.
    w = index(tmp_path, {"t.sv": "import uvm_pkg::*;\n`include \"uvm_macros.svh\"\n"
                                 "`define uvm_object_abstract_utils(T) typedef uvm_object_registry#(T) type_id;\n"
                                 "virtual class c extends uvm_driver;\n  `uvm_object_abstract_utils(c)\nendclass\n"},
              bundled=True)
    [d] = findings(w, "utils-mismatch")
    assert offered(w, d) == []


def test_utils_mismatch_is_not_safe_when_a_create_has_no_parent(tmp_path):
    text = DRV.replace("`uvm_object_utils(item)", "`uvm_object_utils(item)\n  function void f(); "
                       'drv d = drv::type_id::create("d"); endfunction')
    w = index(tmp_path, {"t.sv": text.replace("`uvm_component_utils_begin(drv)", "`uvm_object_utils_begin(drv)")})
    [d] = findings(w, "utils-mismatch")
    [fix] = offered(w, d)
    assert fix.preferred and not fix.safe  # as a component, that create() would stop compiling


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


def test_register_fix_compiles(tmp_path):
    w = example(tmp_path, {"env/alu_scoreboard.svh": ("  `uvm_component_utils(alu_scoreboard)\n", "")})
    assert user_errors(w)  # no member type_id
    fix_once(w, "create-unregistered")
    assert unchanged(tmp_path) and user_errors(w) == []


def test_utils_mismatch_fix_compiles(tmp_path):
    w = example(tmp_path, {"env/alu_coverage.svh": ("`uvm_component_utils(alu_coverage)", "`uvm_object_utils(alu_coverage)")})
    assert user_errors(w)
    fix_once(w, "utils-mismatch")
    assert unchanged(tmp_path) and user_errors(w) == []
