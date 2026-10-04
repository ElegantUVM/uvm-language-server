"""Quick fixes (fixes.py): the text helpers, then each fix as a round trip."""

import os

import pytest

from uvmls.fixes import Client, Context, FileText, FileTexts, fixes_for, indent_unit, insert_member, mask
from uvmls.indexer import ignore_directives
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


def test_create_no_parent_of_a_uvm_component(tmp_path):
    text = COMPONENT.replace("  drv m_drv, d2;", "  drv m_drv, d2;\n  uvm_sequencer #(uvm_sequence_item) sqr;").replace(
        'm_drv = drv::type_id::create("m_drv", this);',
        'm_drv = drv::type_id::create("m_drv", this);\n    sqr = uvm_sequencer #(uvm_sequence_item)::type_id::create("sqr");')
    for bundled in (False, True):
        w = index(tmp_path, {"t.sv": "import uvm_pkg::*;\n" + text}, bundled=bundled)
        [d] = findings(w, "create-no-parent")
        assert d.message == "component uvm_sequencer is created without a parent"
        assert offered(w, d)[0].title == "Pass 'this' as the parent"


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
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    m = mon::type_id::create("m", this);
    c = cfg::type_id::create("c");
  endfunction
endclass
"""
CFG = """class cfg extends uvm_object;
  `uvm_object_utils(cfg)

  int n;
  function new(string name = "cfg"); super.new(name); endfunction
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


# -- factory-ctor ----------------------------------------------------------------------

CTORS = """import uvm_pkg::*;
class drv extends uvm_driver;
  `uvm_component_utils(drv)

  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction

  int n;
endclass
class item extends uvm_sequence_item;
  rand int a;

  `uvm_object_utils_begin(item)
    `uvm_field_int(a, UVM_ALL_ON)
  `uvm_object_utils_end

  function new(string name = "item");
    super.new(name);
  endfunction
endclass
"""


def ctor_findings(tmp_path, text, **cfg):
    w = index(tmp_path, {"t.sv": text}, **cfg)
    return w, [(d.range.line, d.message) for d in findings(w, "factory-ctor")]


@pytest.mark.parametrize("old,new,message", [
    ("function new(string name, uvm_component parent);", "function new(string name);",
     "the factory constructs drv with new(name, parent), but this constructor takes 1 argument(s)"),
    ("function new(string name, uvm_component parent);", "function new(string name, uvm_component parent, int k);",
     "the factory constructs drv with new(name, parent); give 'k' a default value"),
    ("function new(string name, uvm_component parent);", "function new(string name, drv parent);",
     "the factory passes a uvm_component as argument 2 of drv's constructor, but 'parent' is drv"),
    ("function new(string name, uvm_component parent);", "function new(int name, uvm_component parent);",
     "the factory passes a string as argument 1 of drv's constructor, but 'name' is int"),
    ('function new(string name = "item");', 'function new(string name = "item", int k);',
     "the factory constructs item with new(name); give 'k' a default value"),
    ('function new(string name = "item");', "function new();",
     "the factory constructs item with new(name), but this constructor takes 0 argument(s)"),
])
def test_factory_ctor_reports_what_the_factory_cannot_call(tmp_path, old, new, message):
    w, found = ctor_findings(tmp_path, CTORS.replace(old, new))
    assert [m for _, m in found] == [message]
    assert offered(w, findings(w, "factory-ctor")[0]) == []  # no fix but adding or editing by hand


@pytest.mark.parametrize("old,new", [
    ("function new(string name, uvm_component parent);", "function new(string name, uvm_component parent, int k = 1);"),
    ("function new(string name, uvm_component parent);", "function new(string name = \"d\", uvm_object parent = null);"),
    ("`uvm_component_utils(drv)", "`uvm_component_utils(drv)\n  `uvm_new_func"),  # the macro's own
    ("class drv extends", "virtual class drv extends"),
    ("`uvm_component_utils(drv)", "`uvm_object_utils(drv)"),  # utils-mismatch, fixed first
    ("`uvm_component_utils(drv)", "`uvm_component_utils(drv)\n  `NOBODY_DEFINES_THIS"),  # may be the constructor
])
def test_factory_ctor_quiet(tmp_path, old, new):
    text = CTORS.replace(old, new)
    if "uvm_new_func" in new:
        text = text.replace("  function new(string name, uvm_component parent);\n    super.new(name, parent);\n"
                            "  endfunction\n", "")
    assert ctor_findings(tmp_path, text)[1] == []


def test_factory_ctor_with_the_real_uvm_new_func(tmp_path):
    text = CTORS.replace("  function new(string name, uvm_component parent);\n    super.new(name, parent);\n"
                         "  endfunction\n", "  `uvm_new_func\n")
    assert ctor_findings(tmp_path, '`include "uvm_macros.svh"\n' + text, bundled=True)[1] == []


def test_factory_ctor_objects_without_a_constructor_need_the_define(tmp_path):
    text = CTORS.replace('  function new(string name = "item");\n    super.new(name);\n  endfunction\n', "")
    assert [m for _, m in ctor_findings(tmp_path, text)[1]] == \
        ["item has no constructor, but the factory constructs it with new(name)"]
    assert ctor_findings(tmp_path, text, defines={"UVM_OBJECT_DO_NOT_NEED_CONSTRUCTOR": ""})[1] == []


@pytest.mark.parametrize("deleted,title", [
    ("  function new(string name, uvm_component parent);\n    super.new(name, parent);\n  endfunction\n\n",
     "Add a constructor: new(string name, uvm_component parent)"),
    ('  function new(string name = "item");\n    super.new(name);\n  endfunction\n',
     'Add a constructor: new(string name = "item")'),
])
def test_factory_ctor_adds_the_constructor(tmp_path, deleted, title):
    _, fix = round_trip(tmp_path, {"t.sv": CTORS}, {"t.sv": CTORS.replace(deleted, "")}, "factory-ctor")
    assert (fix.title, fix.preferred, fix.safe) == (title, True, True)


def test_factory_ctor_constructor_placement(tmp_path):
    text = """import uvm_pkg::*;
class a extends uvm_object; `uvm_object_utils(a)
  int x;
endclass
class b extends uvm_object;
	`uvm_object_utils(b)
endclass
class c extends uvm_object; `uvm_object_utils(c) endclass
"""
    w = index(tmp_path, {"t.sv": text})
    assert len(findings(w, "factory-ctor")) == 3
    fix_once(w, "factory-ctor")
    fix_once(w, "factory-ctor")
    assert [(d.data["cls"], offered(w, d)) for d in findings(w, "factory-ctor")] == [("c", [])]  # one line
    assert (tmp_path / "t.sv").read_text() == """import uvm_pkg::*;
class a extends uvm_object; `uvm_object_utils(a)

  function new(string name = "a");
    super.new(name);
  endfunction

  int x;
endclass
class b extends uvm_object;
	`uvm_object_utils(b)

	function new(string name = "b");
		super.new(name);
	endfunction
endclass
class c extends uvm_object; `uvm_object_utils(c) endclass
"""


def test_factory_ctor_name_default(tmp_path):
    planted = CTORS.replace('function new(string name = "item");', "function new(string name);")
    w = index(tmp_path, {"t.sv": planted})
    [d] = findings(w, "factory-ctor")
    assert d.message == "the factory constructs item with new() as well as new(name); give 'name' a default value"
    _, fix = round_trip(tmp_path, {"t.sv": CTORS}, {"t.sv": planted}, "factory-ctor")
    assert (fix.title, fix.preferred, fix.safe) == ('Give \'name\' a default value: "item"', True, True)
    # Not for an extern constructor: the default goes on its prototype, the body may repeat it.
    ext = planted.replace('function new(string name);\n    super.new(name);\n  endfunction',
                          "extern function new(string name);") + 'function item::new(string name);\n  super.new(name);\nendfunction\n'
    w = index(tmp_path, {"t.sv": ext})
    [d] = findings(w, "factory-ctor")
    assert offered(w, d) == []


def test_factory_ctor_not_offered_when_the_base_cannot_take_it(tmp_path):
    text = CTORS + """class base_drv extends uvm_driver;
  function new(string name, uvm_component parent, int n);
    super.new(name, parent);
  endfunction
endclass
class sub_drv extends base_drv;
  `uvm_component_utils(sub_drv)
endclass
class arg_drv extends uvm_driver(null);
  `uvm_component_utils(arg_drv)
endclass
"""
    w = index(tmp_path, {"t.sv": text})
    found = {d.data["cls"]: d for d in findings(w, "factory-ctor")}
    assert set(found) == {"sub_drv", "arg_drv"}
    assert offered(w, found["sub_drv"]) == [] and offered(w, found["arg_drv"]) == []


# -- create-new ------------------------------------------------------------------------

NEWS = """import uvm_pkg::*;
class item extends uvm_sequence_item;
  `uvm_object_utils(item)
  function new(string name = "item"); super.new(name); endfunction
endclass
class mon extends uvm_monitor;
  `uvm_component_utils(mon)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
endclass
class pdrv #(type T = int) extends uvm_driver;
  `uvm_component_param_utils(pdrv #(T))
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
endclass
typedef mon mon_t;
class agent extends uvm_agent;
  `uvm_component_utils(agent)
  mon m;
  mon_t mt;
  pdrv #(item) d;
  mon mons[2];
  uvm_analysis_port #(item) ap;
  uvm_tlm_analysis_fifo #(item) fifo;
  agent self;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    mon local_m;
    m = mon::type_id::create("m", this);
    mt = mon_t::type_id::create("mt", this);
    d = pdrv#(item)::type_id::create("d", this);
    local_m = mon::type_id::create("local_m", this);
    foreach (mons[i]) mons[i] = mon::type_id::create($sformatf("mons_%0d", i), this);
    ap = new("ap", this);
    fifo = new("fifo", this);
    self = new("self", null);
  endfunction
endclass
"""


@pytest.mark.parametrize("old,new,lhs,written", [
    ('m = mon::type_id::create("m", this)', 'm = new("m", this)', "m", "mon"),
    ('mt = mon_t::type_id::create("mt", this)', 'mt = new("mt", this)', "mt", "mon_t"),
    ('d = pdrv#(item)::type_id::create("d", this)', 'd = new("d", this)', "d", "pdrv#(item)"),
    ('local_m = mon::type_id::create("local_m", this)', 'local_m = new("local_m", this)', "local_m", "mon"),
    ('mons[i] = mon::type_id::create($sformatf("mons_%0d", i), this)', 'mons[i] = new($sformatf("mons_%0d", i), this)',
     "mons[i]", "mon"),
    ('m = mon::type_id::create("m", this)', 'm = mon::new("m", this)', "m", "mon"),
])
def test_create_new(tmp_path, old, new, lhs, written):
    planted = NEWS.replace(old, new)
    w = index(tmp_path, {"t.sv": planted})
    [d] = findings(w, "create-new")
    target = "pdrv" if written.startswith("pdrv") else "mon"
    assert d.message == f"{lhs} is created with new(), so a factory override of {target} never applies here; " \
                        f"use {written}::type_id::create"
    _, fix = round_trip(tmp_path, {"t.sv": NEWS}, {"t.sv": planted}, "create-new")
    assert (fix.title, fix.preferred, fix.safe) == (f"Create '{lhs}' with {written}::type_id::create", True, False)


def test_create_new_quiet(tmp_path):
    # Ports, FIFOs, the class itself, an unregistered component, three arguments, a covergroup.
    text = NEWS.replace("class mon extends uvm_monitor;\n  `uvm_component_utils(mon)",
                        "class mon extends uvm_monitor;\n  covergroup cg; endgroup") \
        .replace('m = mon::type_id::create("m", this);', 'm = new("m", this);\n    cg = new();') \
        .replace('mt = mon_t::type_id::create("mt", this);', 'd = new("d", this, 1);')
    w = index(tmp_path, {"t.sv": text})
    assert findings(w, "create-new") == []


def test_create_new_fix_compiles(tmp_path):
    w = example(tmp_path, {"agent/alu_agent.svh": ('m_monitor = alu_monitor::type_id::create("m_monitor", this)',
                                                   'm_monitor = new("m_monitor", this)')})
    assert user_errors(w) == []  # it compiles: the factory is silently bypassed
    fix_once(w, "create-new")
    assert unchanged(tmp_path) and user_errors(w) == []


# -- utils-param -----------------------------------------------------------------------

PARAMS = """import uvm_pkg::*;
class drv #(type T = int, int N = 2) extends uvm_driver;
  `uvm_component_param_utils(drv #(T, N))
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
endclass
class item #(type T = int) extends uvm_sequence_item;
  T x;
  `uvm_object_param_utils_begin(item #(T))
  `uvm_object_utils_end
  function new(string name = "item"); super.new(name); endfunction
endclass
class my_test extends uvm_test;
  `uvm_component_utils(my_test)
  drv #(int, 4) d4;
  drv #(bit [7:0], 8) d8;
  typedef item #(byte) byte_item;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    item #(int) i = item #(int)::type_id::create("i");
  endfunction
endclass
"""


@pytest.mark.parametrize("old,new,title", [
    ("`uvm_component_param_utils(drv #(T, N))", "`uvm_component_utils(drv)",
     "Change to `uvm_component_param_utils(drv #(T, N))"),
    ("`uvm_component_param_utils(drv #(T, N))", "`uvm_component_utils(drv #(T, N))",
     "Change to `uvm_component_param_utils(drv #(T, N))"),
    ("`uvm_object_param_utils_begin(item #(T))", "`uvm_object_utils_begin(item)",
     "Change to `uvm_object_param_utils_begin(item #(T))"),
    ("`uvm_component_utils(my_test)", "`uvm_component_param_utils(my_test)", "Change to `uvm_component_utils(my_test)"),
])
def test_utils_param(tmp_path, old, new, title):
    w = index(tmp_path, {"t.sv": PARAMS.replace(old, new)})
    [d] = findings(w, "utils-param")
    if "my_test" in old:
        assert d.message == "my_test is registered with `uvm_component_param_utils, which does not register its " \
                            'name: run_test("my_test") and +UVM_TESTNAME=my_test cannot find it'
    elif "drv" in old:
        arg = "drv" if new.endswith("(drv)") else "drv#(T,N)"
        assert d.message == "drv is parameterized but registered with `uvm_component_utils, so drv #(int, 4) and " \
                            f'drv #(bit[7:0], 8) both register the name "{arg}": UVM warns TPRGED and a lookup by ' \
                            "name gets one of them; use `uvm_component_param_utils"
    _, fix = round_trip(tmp_path, {"t.sv": PARAMS}, {"t.sv": PARAMS.replace(old, new)}, "utils-param")
    assert (fix.title, fix.preferred, fix.safe) == (title, True, False)


def test_utils_param_one_specialization_is_fine(tmp_path):
    # Named once (or only through another class's parameter): its name is its own.
    text = PARAMS.replace("`uvm_component_param_utils(drv #(T, N))", "`uvm_component_utils(drv)") \
        .replace("  drv #(bit [7:0], 8) d8;\n", "  drv #(int, 4) d8;\n  drv #(.N(4)) d4b;\n") \
        .replace("`uvm_object_param_utils_begin(item #(T))", "`uvm_object_utils_begin(item #(T))") \
        .replace("  typedef item #(byte) byte_item;\n", "")
    text += "class wrap #(type X = int) extends uvm_object;\n  item #(X) it;\nendclass\n"
    assert findings(index(tmp_path, {"t.sv": text}), "utils-param") == []


def test_utils_param_only_tests_need_their_name(tmp_path):
    text = PARAMS + "class env extends uvm_env;\n  `uvm_component_param_utils(env)\n" \
                    "  function new(string name, uvm_component parent); super.new(name, parent); endfunction\nendclass\n"
    assert findings(index(tmp_path, {"t.sv": text}), "utils-param") == []


def test_utils_param_fix_compiles(tmp_path):
    w = example(tmp_path, {"tests/alu_tests.svh": ("`uvm_component_utils(alu_random_test)",
                                                   "`uvm_component_param_utils(alu_random_test)")})
    assert user_errors(w) == []  # it compiles; run_test("alu_random_test") fails when it runs
    fix_once(w, "utils-param")
    assert unchanged(tmp_path) and user_errors(w) == []


# -- phase-no-super ----------------------------------------------------------------

PHASES = """import uvm_pkg::*;
class base_test extends uvm_test;
  `uvm_component_utils(base_test)
  uvm_env m_env;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    super.build_phase(phase);
    m_env = uvm_env::type_id::create("m_env", this);
  endfunction
endclass
class plain_test extends base_test;
  `uvm_component_utils(plain_test)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    int n;
    super.build_phase(phase);
    n = 1;
  endfunction
endclass
class override_test extends base_test;
  `uvm_component_utils(override_test)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    set_type_override_by_type(uvm_env::get_type(), uvm_env::get_type());
    uvm_config_db #(int)::set(this, "*", "n", 1);
    super.build_phase(phase);
    `uvm_info("T", "built", UVM_LOW)
  endfunction
endclass
class inline_test extends base_test;
  `uvm_component_utils(inline_test)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase); super.build_phase(phase); `uvm_info("T", "x", UVM_LOW) endfunction
endclass
"""


@pytest.mark.parametrize("old,new,title", [
    ("    int n;\n    super.build_phase(phase);\n", "    int n;\n", "Call super.build_phase(phase) first"),
    ("uvm_config_db #(int)::set(this, \"*\", \"n\", 1);\n    super.build_phase(phase);\n",
     "uvm_config_db #(int)::set(this, \"*\", \"n\", 1);\n", "Call super.build_phase(phase) after the factory overrides"),
    ("build_phase(uvm_phase phase); super.build_phase(phase); `uvm_info", "build_phase(uvm_phase phase); `uvm_info",
     "Call super.build_phase(phase) first"),
])
def test_phase_no_super(tmp_path, old, new, title):
    w = index(tmp_path, {"t.sv": PHASES.replace(old, new)})
    [d] = findings(w, "phase-no-super")
    assert d.message == "build_phase overrides base_test::build_phase without calling super.build_phase(phase); " \
                        "the base class's build_phase, which creates m_env, never runs"
    _, fix = round_trip(tmp_path, {"t.sv": PHASES}, {"t.sv": PHASES.replace(old, new)}, "phase-no-super")
    assert (fix.title, fix.preferred, fix.safe) == (title, True, False)


@pytest.mark.parametrize("old,new", [
    ("    super.build_phase(phase);\n    m_env", "    m_env"),  # base_test's own base is uvm_test
    ("    int n;\n    super.build_phase(phase);\n    n = 1;\n", ""),  # an empty override
    ("    super.build_phase(phase);\n    n = 1;", "    if (n) super.build_phase(phase);\n    n = 1;"),
    ("    super.build_phase(phase);\n    n = 1;", "    base_test::build_phase(phase);\n    n = 1;"),
    ("    super.build_phase(phase);\n    n = 1;", "    uvm_test::build_phase(phase);\n    n = 1;"),  # skips on purpose
])
def test_phase_no_super_quiet(tmp_path, old, new):
    assert old in PHASES
    assert findings(index(tmp_path, {"t.sv": PHASES.replace(old, new)}), "phase-no-super") == []


def test_phase_no_super_through_an_intermediate_class(tmp_path):
    text = PHASES.replace("class plain_test extends base_test;", "class mid_test extends base_test;\n"
                          "  `uvm_component_utils(mid_test)\n"
                          "  function new(string name, uvm_component parent); super.new(name, parent); endfunction\n"
                          "endclass\nclass plain_test extends mid_test;") \
        .replace("    int n;\n    super.build_phase(phase);\n", "    int n;\n")
    w = index(tmp_path, {"t.sv": text})
    [d] = findings(w, "phase-no-super")
    assert d.data == {"cls": "plain_test", "method": "build_phase", "base": "base_test"}
    # What the base's phase does, when it creates nothing.
    w = index(tmp_path, {"t.sv": text.replace('m_env = uvm_env::type_id::create("m_env", this);',
                                              'void\'(uvm_config_db #(int)::get(this, "", "cfg", n));')
                                     .replace("  uvm_env m_env;", "  int n;")})
    [d] = findings(w, "phase-no-super")
    assert d.message.endswith("the base class's build_phase, which gets 'cfg' from uvm_config_db, never runs")


@pytest.mark.parametrize("override", [
    "alu_random_seq::type_id::set_type_override(alu_add_only_seq::get_type());",  # alu_add_test
    "set_type_override_by_type(alu_driver::get_type(), alu_error_driver::get_type());",  # alu_error_test
])
def test_phase_no_super_fix_compiles(tmp_path, override):
    w = example(tmp_path, {"tests/alu_tests.svh": (
        f"    {override}\n    super.build_phase(phase);\n", f"    {override}\n")})
    [d] = findings(w, "phase-no-super")
    assert d.message == "build_phase overrides alu_base_test::build_phase without calling super.build_phase(phase); " \
                        "the base class's build_phase, which creates m_env, never runs"
    fix_once(w, "phase-no-super")
    assert unchanged(tmp_path) and user_errors(w) == []


# -- config_db ---------------------------------------------------------------------------

CONFIG = """import uvm_pkg::*;
typedef int depth_t;
class cfg extends uvm_object;
  `uvm_object_utils(cfg)
  function new(string name = "cfg"); super.new(name); endfunction
endclass
class env extends uvm_env;
  `uvm_component_utils(env)
  bit checks_enable = 1;
  int depth;
  depth_t depth2;
  cfg c;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    void'(uvm_config_db #(bit)::get(this, "", "checks_enable", checks_enable));
    if (!uvm_config_db #(int)::get(this, "", "depth", depth)) `uvm_fatal("D", "no depth")
    if (!uvm_config_db #(int)::get(this, "", "depth", depth2)) `uvm_fatal("D", "no depth")
    if (!uvm_config_db #(cfg)::get(this, "", "cfg", c)) `uvm_fatal("C", "no cfg")
  endfunction
endclass
class test extends uvm_test;
  `uvm_component_utils(test)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    uvm_config_db #(bit)::set(this, "*", "checks_enable", 0);
    uvm_config_db #(int)::set(this, "*", "depth", 4);
    uvm_config_db #(cfg)::set(this, "*", "cfg", cfg::type_id::create("c"));
  endfunction
endclass
"""


def line_no(text: str, needle: str) -> int:
    return text[:text.index(needle)].count("\n") + 1


def test_config_db_typo(tmp_path):
    planted = CONFIG.replace('"*", "checks_enable", 0', '"*", "checks_enabled", 0')
    at = line_no(planted, '"checks_enabled"')
    w = index(tmp_path, {"t.sv": planted})
    [d] = findings(w, "config-db-no-set")
    assert d.severity == "warning" and d.message.endswith(f"did you mean 'checks_enabled' (set in t.sv:{at})?")
    fixes = offered(w, d, commands=frozenset({"uvm.showConfigDb"}))
    assert [(f.title, f.preferred, f.command) for f in fixes] == [
        (f"Change 'checks_enable' to 'checks_enabled' (set in t.sv:{at})", True, None),
        ("Show 'checks_enable' in the config_db cross-reference", False,
         ("uvm.showConfigDb", [{"field": "checks_enable"}]))]
    fix_once(w, "config-db-no-set")
    assert not findings(w, "config-db-no-set") and \
        'get(this, "", "checks_enabled", checks_enable)' in (tmp_path / "t.sv").read_text()


def test_config_db_typo_candidates(tmp_path):
    text = CONFIG.replace('"*", "checks_enable", 0);', '"*", "Checks_Enable", 0);\n'
                          '    uvm_config_db #(bit)::set(this, "*", "checks_enabl", 0);\n'
                          '    uvm_config_db #(int)::set(this, "*", "checks_enablee", 0);')
    w = index(tmp_path, {"t.sv": text})
    [d] = findings(w, "config-db-no-set")
    # Two of the same type, none preferred; the int one is no candidate.
    assert [(f.title, f.preferred) for f in offered(w, d)] == [
        (f"Change 'checks_enable' to 'Checks_Enable' (set in t.sv:{line_no(text, 'Checks_Enable')})", False),
        (f"Change 'checks_enable' to 'checks_enabl' (set in t.sv:{line_no(text, 'checks_enabl"')})", False)]
    assert "did you mean" not in d.message


def test_config_db_type_mismatch_fix(tmp_path):
    for old, new, title, preferred in [
        ('uvm_config_db #(int)::get(this, "", "depth", depth)', 'uvm_config_db #(bit)::get(this, "", "depth", depth)',
         "Change to uvm_config_db #(int) (the type 'depth' is set with)", True),
        ('uvm_config_db #(int)::get(this, "", "depth", depth2)', 'uvm_config_db #(byte)::get(this, "", "depth", depth2)',
         "Change to uvm_config_db #(int) (the type 'depth' is set with)", True),  # through a typedef
    ]:
        w = index(tmp_path, {"t.sv": CONFIG.replace(old, new)})
        [d] = findings(w, "config-db-type-mismatch")
        [fix] = offered(w, d)
        assert (fix.title, fix.preferred, fix.safe) == (title, preferred, False)
        round_trip(tmp_path, {"t.sv": CONFIG}, {"t.sv": CONFIG.replace(old, new)}, "config-db-type-mismatch")
    # The variable is a bit: integral both, offered but not preferred.
    text = CONFIG.replace("  int depth;", "  bit [3:0] depth;").replace('#(int)::get(this, "", "depth", depth)',
                                                                       '#(bit [3:0])::get(this, "", "depth", depth)')
    w = index(tmp_path, {"t.sv": text})
    [d] = findings(w, "config-db-type-mismatch")
    assert [(f.title, f.preferred) for f in offered(w, d)] == \
        [("Change to uvm_config_db #(int) (the type 'depth' is set with)", False)]
    # A class handle of another type: an inout argument, so no fix.
    text = CONFIG.replace("  cfg c;", "  uvm_object c;").replace("#(cfg)::get", "#(uvm_object)::get")
    w = index(tmp_path, {"t.sv": text})
    [d] = findings(w, "config-db-type-mismatch")
    assert offered(w, d) == []


# -- slang's diagnostics -----------------------------------------------------------------


def slang_findings(w: Workspace, code: str) -> list:
    return [d for d in w.user_diagnostics() if d.code == code]


def elab_findings(w: Workspace, code: str) -> list:
    """Elaboration's diagnostics of a code, as the server turns them into findings."""
    from uvmls.elab import SOURCE, elaborate, request_for
    from uvmls.model import Diag, Range
    return [Diag(Range(d["file"], d["line"], d["col"], d["endLine"], d["endCol"]), d["message"], d["severity"],
                 d["code"], SOURCE, [], d.get("data"))
            for d in elaborate(request_for(w))["diagnostics"] if d["code"] == code]


def apply_fix(w: Workspace, fix):
    for path in {c.path for c in fix.changes}:
        ft = FileText.from_disk(path)
        with open(path, "w", newline="") as f:
            f.write(apply(ft.text, [c for c in fix.changes if c.path == path]))
    w.reindex_all()


def test_typo_keyword(tmp_path):
    text = "module m;\n  logic a;\n  always @(a) a = 0;\nendmodule\n"
    w = index(tmp_path, {"m.sv": text.replace("always @", "alwasy @")})
    [d] = slang_findings(w, "TypoKeyword")
    [fix] = offered(w, d)
    assert (fix.title, fix.preferred, fix.safe) == ("Change 'alwasy' to 'always'", True, False)
    apply_fix(w, fix)
    assert (tmp_path / "m.sv").read_text() == text and not slang_findings(w, "TypoKeyword")


def test_typo_not_offered_for_other_text(tmp_path):
    # The editor's text no longer has the name there.
    text = "module m;\n  logic a;\n  alwasy @(a) a = 0;\nendmodule\n"
    w = index(tmp_path, {"m.sv": text})
    [d] = slang_findings(w, "TypoKeyword")
    path = str(tmp_path / "m.sv")
    edited = FileTexts({path: (text.replace("alwasy", "alway_"), 2)})
    assert fixes_for(Context(w, edited, Client(roots=[str(tmp_path)]), path), d) == []


def test_missing_include_discovery(tmp_path):
    files = {"inc/sub/hdr.svh": "int q;\n", "t.sv": '`include "sub/hdr.svh"\nmodule t; endmodule\n'}
    w = index(tmp_path, files)
    [d] = slang_findings(w, "CouldNotOpenIncludeFile")
    assert offered(w, d) == []  # no filelist, and no editor command to change the setting
    [fix] = offered(w, d, commands=frozenset({"uvm.addIncludeDir"}))
    assert (fix.title, fix.preferred, fix.changes, fix.command) == (
        "Add 'inc' to uvm.includeDirs", True, [], ("uvm.addIncludeDir", [{"value": "inc"}]))
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)], include_dirs=[str(tmp_path / "inc")],
                              bundled_uvm=False))
    assert not slang_findings(w, "CouldNotOpenIncludeFile")


def test_missing_include_candidates(tmp_path):
    files = {"a/tb/t.sv": '`include "sub/hdr.svh"\nmodule t; endmodule\n', "a/inc/sub/hdr.svh": "int q;\n",
             "b/inc/sub/hdr.svh": "int q;\n", "c/sub_hdr.svh": "int q;\n", "d/xsub/hdr.svh": "int q;\n"}
    w = index(tmp_path, files)
    [d] = slang_findings(w, "CouldNotOpenIncludeFile")
    fixes = offered(w, d, commands=frozenset({"uvm.addIncludeDir"}))
    # Nearest the including file first; neither is the one fix to apply.
    assert [(f.title, f.preferred) for f in fixes] == [
        ("Add 'a/inc' to uvm.includeDirs", False), ("Add 'b/inc' to uvm.includeDirs", False)]
    # Outside the first workspace folder, which relative paths in the setting are read against.
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path / "a" / "tb"), str(tmp_path / "b")], bundled_uvm=False))
    [d] = slang_findings(w, "CouldNotOpenIncludeFile")
    assert [f.title for f in offered(w, d, commands=frozenset({"uvm.addIncludeDir"}))] == \
        [f"Add '{tmp_path / 'b' / 'inc'}' to uvm.includeDirs"]


@pytest.mark.parametrize("name", ["../inc/sub/hdr.svh", "/abs/sub/hdr.svh", "nowhere.svh"])
def test_missing_include_not_offered(tmp_path, name):
    w = index(tmp_path, {"inc/sub/hdr.svh": "int q;\n", "t.sv": f'`include "{name}"\nmodule t; endmodule\n'})
    [d] = slang_findings(w, "CouldNotOpenIncludeFile")
    assert offered(w, d, commands=frozenset({"uvm.addIncludeDir"})) == []


@pytest.mark.parametrize("before, after", [
    ("// the tb\n+incdir+rtl\n-incdir  ip\nt.sv\n", "// the tb\n+incdir+rtl\n-incdir  ip\n+incdir+inc\nt.sv\n"),
    ("// the tb\n\n+define+X\nt.sv\n", "// the tb\n\n+incdir+inc\n+define+X\nt.sv\n"),
    ("  +INCDIR+rtl\r\nt.sv", "  +INCDIR+rtl\r\n  +incdir+inc\r\nt.sv"),
    ("t.sv\n+incdir+rtl", "t.sv\n+incdir+rtl\n+incdir+inc"),
    ("/* the tb\n   +incdir+old\n*/ # and\n# more\nt.sv\n", "/* the tb\n   +incdir+old\n*/ # and\n# more\n+incdir+inc\nt.sv\n"),
])
def test_missing_include_filelist(tmp_path, before, after):
    files = {"inc/sub/hdr.svh": "int q;\n", "rtl/r.sv": "", "ip/i.sv": "",
             "t.sv": '`include "sub/hdr.svh"\nmodule t; endmodule\n', "tb.f": before}
    w = index(tmp_path, files, filelists=[str(tmp_path / "tb.f")])
    [d] = slang_findings(w, "CouldNotOpenIncludeFile")
    [fix] = offered(w, d)
    assert (fix.title, fix.preferred) == ("Add +incdir+inc to tb.f", False)
    # With the setting too: two ways, neither preferred.
    assert [(f.title, f.preferred) for f in offered(w, d, commands=frozenset({"uvm.addIncludeDir"}))] == [
        ("Add 'inc' to uvm.includeDirs", False), ("Add +incdir+inc to tb.f", False)]
    apply_fix(w, fix)
    with open(tmp_path / "tb.f", newline="") as f:
        assert f.read() == after
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)], filelists=[str(tmp_path / "tb.f")], bundled_uvm=False))
    assert not slang_findings(w, "CouldNotOpenIncludeFile")


def test_missing_include_filelist_outside_the_workspace(tmp_path):
    (tmp_path / "flows").mkdir()
    (tmp_path / "flows" / "tb.f").write_text("../ws/t.sv\n")
    files = {"inc/sub/hdr.svh": "int q;\n", "t.sv": '`include "sub/hdr.svh"\nmodule t; endmodule\n'}
    w = index(tmp_path / "ws", files, filelists=[str(tmp_path / "flows" / "tb.f")])
    [d] = slang_findings(w, "CouldNotOpenIncludeFile")
    assert offered(w, d) == []
    [fix] = offered(w, d, disabled_support=True)
    assert (fix.title, fix.changes, fix.disabled) == (
        "Add +incdir+../ws/inc to tb.f", [], f"{tmp_path / 'flows' / 'tb.f'} is outside the workspace")


# -- fix-all -------------------------------------------------------------------------------


def all_fixed(w, path, code=None):
    from uvmls.fixes import fix_all
    ctx = Context(w, FileTexts(), Client(roots=[os.path.dirname(path)]), path)
    return fix_all(ctx, [d for ds in lint(w).values() for d in ds if d.range.path == path], code)


def test_fix_all_makes_a_shared_edit_once(tmp_path):
    text = ENV.replace("  `uvm_component_utils(mon)\n", "").replace(
        'm = mon::type_id::create("m", this);', 'm = mon::type_id::create("m", this);\n    m = mon::type_id::create("m2");')
    w = index(tmp_path, {"env.sv": text, "cfg.svh": CFG})
    path = str(tmp_path / "env.sv")
    changes, fixed = all_fixed(w, path)
    # Two creates of mon, registered once.
    assert [d.code for d in fixed] == ["create-unregistered", "create-unregistered"] and len(changes) == 1
    text = apply(FileText.from_disk(path).text, changes)
    with open(path, "w") as f:
        f.write(text)
    w.reindex_all()
    # Each pass fixes what it can: now the second create's missing parent shows, and is fixed.
    changes, fixed = all_fixed(w, path, "create-no-parent")
    assert [d.code for d in fixed] == ["create-no-parent"]
    after = apply(FileText.from_disk(path).text, changes)
    assert after.count("`uvm_component_utils(mon)") == 1 and 'create("m2", this)' in after


def test_fix_all_only_safe_fixes_in_this_file(tmp_path):
    text = ENV.replace("\n", '\n`include "cfg.svh"\n', 1) + """class seq extends uvm_sequence;
  `uvm_object_utils(seq)
  mon m;
  function new(string name = "seq"); super.new(name); endfunction
  task body(); m = mon::type_id::create("m"); endtask
endclass
"""
    w = index(tmp_path, {"env.sv": text, "cfg.svh": CFG.replace("  `uvm_object_utils(cfg)\n", "")})
    path = str(tmp_path / "env.sv")
    assert {d.code for ds in lint(w).values() for d in ds} == {"create-unregistered", "create-no-parent"}
    # cfg is registered in its own file, and the sequence's parent would be null: neither is safe here.
    assert all_fixed(w, path) == ([], [])


def test_fix_all_leaves_a_clashing_edit_for_the_next_pass(tmp_path, monkeypatch):
    from uvmls import fixes
    from uvmls.fixes import Fix, TextChange
    w = index(tmp_path, {"env.sv": ENV.replace('"m", this', '"m"').replace('create("c")', 'create("c", this)'),
                         "cfg.svh": CFG})
    path = str(tmp_path / "env.sv")
    [d] = findings(w, "create-no-parent")
    later = type(d.range)(d.range.path, d.range.line + 1, 0, d.range.line + 1, 1)
    other = type(d)(later, "x", "error", "create-unregistered", "uvm", data={"target": "mon"})
    edits = {"create-no-parent": [TextChange(path, 10, 12, "a")], "create-unregistered": [TextChange(path, 11, 11, "b")]}
    monkeypatch.setattr(fixes, "fixes_for", lambda ctx, d: [Fix("t", edits[d.code], safe=True)])
    ctx = Context(w, FileTexts(), Client(roots=[str(tmp_path)]), path)
    changes, fixed = fixes.fix_all(ctx, [other, d])
    assert changes == edits["create-no-parent"] and len(fixed) == 1  # the one first in the file wins


# -- ignore directive ------------------------------------------------------------------


@pytest.mark.parametrize("text,found", [
    ("x = 1; // uvm-lint: ignore create-new\n", [(0, 0, ("create-new",))]),
    ("  // uvm-lint: ignore a, b ,c -- why\n\n  // more\n  /* and more */\n  x = 1;\n", [(0, 4, ("a", "b", "c"))]),
    ("x; /* c */ // uvm-lint: ignore a\n", [(0, 0, ("a",))]),
    ("/* a\n*/ // uvm-lint: ignore a\nx;\n", [(1, 2, ("a",))]),  # after a block comment's end: no code
    ("\t\tx;\t//uvm-lint:\tignore\ta\n", [(0, 0, ("a",))]),
    ("\r\n// uvm-lint: ignore a\r\n\r\nx;\r\n", [(1, 3, ("a",))]),
    ("\r// uvm-lint: ignore a\r\rx;\r", [(1, 3, ("a",))]),  # slang counts a lone CR as a line end too
    ("x; // uvm-lint: ignore a", [(0, 0, ("a",))]),
    ("x;\n// uvm-lint: ignore a", [(1, None, ("a",))]),  # no code after it
    ("`ifdef A\n// uvm-lint: ignore a\n`endif\nx;\n", [(1, 2, ("a",))]),  # never past the `endif
    ("// uvm-lint: ignore a\n// uvm-lint: ignore b\nx; // uvm-lint: ignore c\n",
     [(0, 2, ("a",)), (1, 2, ("b",)), (2, 2, ("c",))]),
    # Not directives: in a string or a block comment, not at the comment's start, or not the words.
    ('x = "// uvm-lint: ignore a"; y;\n', []),
    ("/* // uvm-lint: ignore a */ x;\n/*\n// uvm-lint: ignore b\n*/\ny;\n", []),
    ("x; // a // uvm-lint: ignore a\nx; // see uvm-lint: ignore a\n", []),
    ("x; // uvm-lint: ignored a\nx; // uvm-lint: ignore\nx; // UVM-LINT: ignore a\nx; /// uvm-lint: ignore a\n", []),
])
def test_ignore_directives(text, found):
    directives = ignore_directives(text)
    assert [(d.line, d.target, d.codes) for d in directives] == found
    assert all(text[:d.end].endswith(d.codes[-1]) for d in directives)


IGNORED = """import uvm_pkg::*;
`define MAKE(T, n) n = T::type_id::create(`"n`")
class mon extends uvm_monitor;
  `uvm_component_utils(mon)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
endclass
class env extends uvm_env;
  `uvm_component_utils(env)
  mon a, b, c, d, e, f, g, h;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    a = mon::type_id::create("a");  // uvm-lint: ignore create-no-parent
    // uvm-lint: ignore no-such-code, create-no-parent

    // (a blank line and a comment above)
    b = mon::type_id::create("b");  // uvm-lint: ignore create-new
    `MAKE(mon, c);  // uvm-lint: ignore create-no-parent
    d = mon::type_id::create("d");  // uvm-lint: ignore create-new, factory-ctor
    e = mon::type_id::create("e");  /* uvm-lint: ignore create-no-parent */
    f = mon::type_id::create("// uvm-lint: ignore create-no-parent");
    g = mon::type_id::create("g");  // uvm-lint: ignore create-no-parents
    h = mon::type_id::create("h");
  endfunction
endclass
"""


def lines_of(w: Workspace, code: str) -> list[int]:
    return [d.range.line for d in findings(w, code)]


def test_lint_leaves_out_ignored_findings(tmp_path):
    # a: after it; b: on a line of its own above it (with another directive on its line); c: from a
    # macro, reported where it is used. The others name other codes, or are not directives.
    w = index(tmp_path, {"t.sv": IGNORED})
    assert lines_of(w, "create-no-parent") == [17, 18, 19, 20, 21]
    assert w.ignores == {str(tmp_path / "t.sv"): {
        11: ["create-no-parent"], 15: ["no-such-code", "create-no-parent", "create-new"],
        16: ["create-no-parent"], 17: ["create-new", "factory-ctor"], 20: ["create-no-parents"]}}
    # Without them, every create reports it.
    (tmp_path / "t.sv").write_text(IGNORED.replace("uvm-lint", "lint"))
    w.reindex_all()
    assert lines_of(w, "create-no-parent") == [11, 15, 16, 17, 18, 19, 20, 21] and w.ignores == {}


def test_ignore_directives_in_an_editor_and_in_a_shared_header(tmp_path):
    # A header two roots include: its directives count once whichever root it is read from, and
    # those in an editor's unsaved text count until it is closed.
    text = IGNORED.replace("uvm-lint", "lint")
    w = index(tmp_path, {"h.svh": text, "a.sv": '`include "h.svh"\nmodule a; endmodule\n',
                         "b.sv": '`include "h.svh"\nmodule b; endmodule\n'})
    assert len(w.indices) == 2 and lines_of(w, "create-no-parent") == [11, 15, 16, 17, 18, 19, 20, 21]
    path = str(tmp_path / "h.svh")
    w.update_document(path, text.replace('create("h");', 'create("h");  // uvm-lint: ignore create-no-parent'))
    assert lines_of(w, "create-no-parent") == [11, 15, 16, 17, 18, 19, 20]
    w.update_document(path, None)
    assert lines_of(w, "create-no-parent") == [11, 15, 16, 17, 18, 19, 20, 21]


def test_fix_all_leaves_ignored_findings_alone(tmp_path):
    text = ENV.replace('"m", this', '"m"').replace('create("c")', 'create("c", this)')
    w = index(tmp_path, {"env.sv": text.replace('create("m");', 'create("m");\n    m = mon::type_id::create("m2");'),
                         "cfg.svh": CFG})
    path = str(tmp_path / "env.sv")
    assert [d.code for d in all_fixed(w, path)[1]] == ["create-no-parent", "create-no-parent"]
    with open(path, "w") as f:
        f.write(text.replace('create("m");', 'create("m");  // uvm-lint: ignore create-no-parent\n'
                                             '    m = mon::type_id::create("m2");'))
    w.reindex_all()
    changes, fixed = all_fixed(w, path)
    assert [d.range.line for d in fixed] == [13]
    assert 'create("m");  // uvm' in apply(FileText.from_disk(path).text, changes)


def test_the_cli_leaves_out_ignored_findings(tmp_path, capsys):
    import json

    from uvmls import cli
    planted = ENV.replace('"m", this', '"m"')
    index(tmp_path, {"env.sv": planted, "cfg.svh": CFG})

    def run(*args) -> tuple[int, str]:
        with pytest.raises(SystemExit) as e:
            cli.main(["lint", str(tmp_path), "--no-bundled-uvm", *args])
        return e.value.code, capsys.readouterr().out

    status, out = run("--json")
    assert status == 1 and [(d["code"], d["line"]) for d in json.loads(out)] == [("create-no-parent", 13)]
    (tmp_path / "env.sv").write_text(planted.replace('create("m");', 'create("m");  // uvm-lint: ignore create-no-parent'))
    assert run() == (0, "")


WAIVE = """import uvm_pkg::*;
class mon extends uvm_monitor;
  `uvm_component_utils(mon)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
endclass
class cfg extends uvm_object;
  function new(string name = "cfg"); super.new(name); endfunction
endclass
class env extends uvm_env;
  `uvm_component_utils(env)
  mon m, m2;
  cfg c;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
LINE
    m2 = mon::type_id::create("m2");
  endfunction
endclass
"""
M = 'm = mon::type_id::create("m");'
ABOVE = "    // uvm-lint: ignore create-no-parent\n"
IGNORE_LAYOUTS = {  # the line(s) in WAIVE with the finding, before and after; the file's line end
    "no comment": (f"    {M}", f"    {M}  // uvm-lint: ignore create-no-parent", "\n"),
    "spaces after it": (f"    {M}   ", f"    {M}  // uvm-lint: ignore create-no-parent   ", "\n"),
    "a directive after it": (f"    {M}  // uvm-lint: ignore create-new -- why",
                             f"    {M}  // uvm-lint: ignore create-new, create-no-parent -- why", "\n"),
    "another comment": (f"    {M}  // the monitor", f"{ABOVE}    {M}  // the monitor", "\n"),
    "uvm-lint in a comment": (f"    {M}  // see uvm-lint: ignore", f"{ABOVE}    {M}  // see uvm-lint: ignore", "\n"),
    "a block comment": (f"    {M} /* the monitor */", f"{ABOVE}    {M} /* the monitor */", "\n"),
    "an unclosed block comment": (f"    {M} /* the monitor,\n       created here */",
                                  f"{ABOVE}    {M} /* the monitor,\n       created here */", "\n"),
    "a directive above": (f"    // uvm-lint: ignore create-new\n    {M}  // the monitor",
                          f"    // uvm-lint: ignore create-new, create-no-parent\n    {M}  // the monitor", "\n"),
    "a directive above, then comments": (
        f"    // uvm-lint: ignore create-new -- why\n\n    /* more */\n    {M}",
        f"    // uvm-lint: ignore create-new, create-no-parent -- why\n\n    /* more */\n    {M}", "\n"),
    "two directives above": (f"    // uvm-lint: ignore create-new\n    // uvm-lint: ignore utils-param\n    {M}",
                             f"    // uvm-lint: ignore create-new\n    // uvm-lint: ignore utils-param, create-no-parent"
                             f"\n    {M}", "\n"),
    "a directive above about its own line": (
        f'    c = cfg::type_id::create("c");  // uvm-lint: ignore create-new\n    {M}  // the monitor',
        f'    c = cfg::type_id::create("c");  // uvm-lint: ignore create-new\n{ABOVE}    {M}  // the monitor', "\n"),
    "another finding on the line": (f'    c = cfg::type_id::create("c"); {M}',
                                    f'    c = cfg::type_id::create("c"); {M}  // uvm-lint: ignore create-no-parent', "\n"),
    "the line starts in a block comment": (f"    /* the\n       monitor */ {M}",
                                           f"    /* the\n       monitor */ {M}  // uvm-lint: ignore create-no-parent", "\n"),
    "tabs": (f"\t\t{M}\t// the monitor", f"\t\t// uvm-lint: ignore create-no-parent\n\t\t{M}\t// the monitor", "\n"),
    "crlf": (f"    {M}  // the monitor", f"{ABOVE}    {M}  // the monitor", "\r\n"),
    "crlf, no comment": (f"    {M}", f"    {M}  // uvm-lint: ignore create-no-parent", "\r\n"),
}


def fixes_ignore(w: Workspace, d, **client) -> list:
    from uvmls.fixes import ignore_fixes
    return ignore_fixes(Context(w, FileTexts(), Client(roots=[os.path.dirname(d.range.path)], **client),
                                d.range.path), d)


def all_findings(w: Workspace) -> list:
    return sorted((d.range.path, d.code, d.message) for ds in lint(w).values() for d in ds)


def ignore_round_trip(w: Workspace, d, expected: str):
    """Apply the Ignore fix of a finding: the file must read `expected`, and once indexed again
    that finding must be gone and every other one still there."""
    [fix] = fixes_ignore(w, d)
    assert (fix.title, fix.preferred, fix.safe, fix.disabled) == (f"Ignore '{d.code}' on this line", False, False, None)
    assert {c.path for c in fix.changes} == {d.range.path}
    before = all_findings(w)
    apply_fix(w, fix)
    with open(d.range.path, newline="") as f:
        assert f.read() == expected
    before.remove((d.range.path, d.code, d.message))
    assert all_findings(w) == before


@pytest.mark.parametrize("layout", IGNORE_LAYOUTS)
def test_ignore_on_this_line(tmp_path, layout):
    before, after, eol = IGNORE_LAYOUTS[layout]
    w = index(tmp_path, {"t.sv": WAIVE.replace("LINE", before).replace("\n", eol)})
    d = findings(w, "create-no-parent")[0]
    assert M in WAIVE.replace("LINE", before).split("\n")[d.range.line]  # m's, not m2's
    ignore_round_trip(w, d, WAIVE.replace("LINE", after).replace("\n", eol))


def test_ignore_two_codes_on_one_line(tmp_path):
    line = f'    c = cfg::type_id::create("c"); {M}'
    w = index(tmp_path, {"t.sv": WAIVE.replace("LINE", line)})
    ignore_round_trip(w, findings(w, "create-no-parent")[0],
                      WAIVE.replace("LINE", line + "  // uvm-lint: ignore create-no-parent"))
    ignore_round_trip(w, findings(w, "create-unregistered")[0],
                      WAIVE.replace("LINE", line + "  // uvm-lint: ignore create-no-parent, create-unregistered"))


@pytest.mark.parametrize("comment", ["", " // the env"])
def test_ignore_on_the_last_line(tmp_path, comment):
    # No line end after it: one goes after the directive, the file's own (CRLF here).
    last = "class env2 extends uvm_env; `uvm_object_utils(env2) endclass"
    w = index(tmp_path, {"t.sv": f"import uvm_pkg::*;\r\n{last}{comment}"})
    [d] = findings(w, "utils-mismatch")
    after = f"{last}  // uvm-lint: ignore utils-mismatch" if not comment else \
        f"// uvm-lint: ignore utils-mismatch\r\n{last}{comment}"
    ignore_round_trip(w, d, f"import uvm_pkg::*;\r\n{after}")


def test_ignore_from_a_macro_call(tmp_path):
    # Reported where the macro is used: the directive goes on that line.
    w = index(tmp_path, {"t.sv": "`define MAKE(T, n) n = T::type_id::create(`\"n`\")\n" +
                         WAIVE.replace("LINE", "    `MAKE(mon, m);  // the monitor")})
    d = findings(w, "create-no-parent")[0]
    ignore_round_trip(w, d, "`define MAKE(T, n) n = T::type_id::create(`\"n`\")\n" +
                      WAIVE.replace("LINE", f"{ABOVE}    `MAKE(mon, m);  // the monitor"))


def test_ignore_not_offered(tmp_path):
    from uvmls.model import Diag, Range
    from uvmls.project import BUNDLED_UVM
    # The line starts inside a block comment and ends in another: no line can go above it, and a
    # directive after the comment would be part of it.
    w = index(tmp_path, {"t.sv": WAIVE.replace("LINE", f"    /* the\n       monitor */ {M} // here") + "class\n"})
    d = findings(w, "create-no-parent")[0]
    assert fixes_ignore(w, d) == []
    # Nor when it starts inside a string continued from the line before.
    w2 = index(tmp_path / "s", {"t.sv": WAIVE.replace("LINE", f'    $display("the\\\nmonitor"); {M} // here')})
    d2 = findings(w2, "create-no-parent")[0]
    assert d2.range.line == 15 and fixes_ignore(w2, d2) == []
    # A string the line does not close (the text being typed), or a directive that has the code
    # already (the finding is not current): nothing to add.
    from uvmls.fixes import ignore_fixes
    path = d.range.path

    def on(text: str, line: int, col: int, code="create-new") -> list:
        ctx = Context(w, FileTexts({path: (text, 1)}), Client(roots=[str(tmp_path)]), path)
        return ignore_fixes(ctx, Diag(Range(path, line, col, line, col + 1), "x", "warning", code, "uvm"))
    assert on('    x = "abc\n', 0, 4) == [] and on('    x = "abc";\n', 0, 4) != []
    assert on("    x = y;  // uvm-lint: ignore a,create-new\n", 0, 4) == []
    w3 = index(tmp_path / "d", {"t.sv": WAIVE.replace("LINE", f"    // uvm-lint: ignore create-new\n    {M}")})
    d3 = findings(w3, "create-no-parent")[0]
    assert fixes_ignore(w3, Diag(d3.range, "x", "warning", "create-new", "uvm")) == []
    assert fixes_ignore(w3, Diag(d3.range, "x", "warning", "utils-param", "uvm")) != []
    # A compile error cannot be waived.
    [syntax] = [x for x in w.user_diagnostics() if x.severity == "error"]
    assert syntax.source == "slang" and fixes_ignore(w, syntax) == []
    assert fixes_ignore(w, Diag(d.range, d.message, "error", d.code, "slang-elab")) == []
    # A line the text no longer has.
    assert fixes_ignore(w, Diag(Range(d.range.path, 99, 0, 99, 1), d.message, "error", d.code, "uvm")) == []
    # The UVM library: greyed out with the reason, for an editor that shows such actions.
    lib = os.path.join(BUNDLED_UVM, "base", "uvm_component.svh")
    finding = Diag(Range(lib, 0, 0, 0, 1), "x", "warning", "create-new", "uvm")
    assert fixes_ignore(w, finding) == []
    [fix] = fixes_ignore(w, finding, disabled_support=True)
    assert fix.disabled == "uvm_component.svh is part of the UVM library" and fix.changes == []


def test_in_comment_or_string():
    from uvmls.fixes import _in_comment_or_string
    text = 'a = "x\\\ny"; b; // c\n/* d\ne */ f;\n'
    starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]
    assert [_in_comment_or_string(text, s) for s in starts] == [False, True, False, True, False]


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


def test_factory_ctor_fix_compiles(tmp_path):
    w = example(tmp_path, {"agent/alu_seq_item.svh": (
        '  function new(string name = "alu_seq_item");\n    super.new(name);\n  endfunction\n\n', "")})
    assert user_errors(w)  # the factory's new(name) and new() calls
    [d] = findings(w, "factory-ctor")
    assert d.message == "alu_seq_item has no constructor, but the factory constructs it with new(name)"
    fix_once(w, "factory-ctor")
    assert unchanged(tmp_path) and user_errors(w) == []


def test_typo_fixes_compile(tmp_path):
    w = example(tmp_path, {"agent/alu_agent.svh": ("if (m_cfg.is_active == UVM_ACTIVE) begin",
                                                   "if (m_cfg.is_actve == UVM_ACTIVE) begin"),
                           "agent/alu_driver.svh": ("seq_item_port.get_next_item(req)",
                                                    "seq_item_prt.get_next_item(req)")})
    for code in ("UnknownMember", "TypoIdentifier"):
        [d] = elab_findings(w, code)
        [fix] = offered(w, d)
        assert fix.preferred
        apply_fix(w, fix)
    assert unchanged(tmp_path) and user_errors(w) == []


def test_config_db_type_fix_compiles(tmp_path):
    w = example(tmp_path, {"agent/alu_agent.svh": ('uvm_config_db #(alu_agent_config)::get',
                                                   'uvm_config_db #(uvm_object)::get')})
    assert user_errors(w)  # the value argument is an inout alu_agent_config
    fix_once(w, "config-db-type-mismatch")
    assert unchanged(tmp_path) and user_errors(w) == []
