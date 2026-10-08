import pyslang

from uvmls.indexer import Indexer
from uvmls.project import ProjectConfig
from uvmls.workspace import Workspace


def classes(ws):
    return {c.name: c for c in ws.classes if not c.library}


def test_all_classes_found(any_ws):
    names = set(classes(any_ws))
    assert {"alu_driver", "alu_error_driver", "alu_agent", "alu_env", "alu_base_test",
            "alu_random_seq", "alu_seq_item"} <= names
    assert len(names) == 19


def test_packages_and_registration(ws):
    c = classes(ws)
    assert c["alu_driver"].package == "alu_pkg"
    assert c["alu_env"].package == "alu_env_pkg"
    assert c["alu_driver"].registration == "component"
    assert c["alu_seq_item"].registration == "object"
    assert c["alu_base_seq"].registration is None
    assert c["alu_base_seq"].is_virtual


def test_factory_creates_with_conditions(ws):
    agent = classes(ws)["alu_agent"]
    creates = {cr.target: cr for cr in ws.calls_of(agent).creates}
    assert creates["m_monitor"].type.name == "alu_monitor"
    assert not creates["m_monitor"].conditional
    assert creates["m_driver"].conditional
    assert "is_active" in creates["m_driver"].condition


def test_config_db_calls(ws):
    env = classes(ws)["alu_env"]
    calls = {(c.op, c.field_name): c for c in ws.calls_of(env).config_db}
    s = calls[("set", "alu_agent_config")]
    assert s.inst == "m_agent*" and s.type_text == "alu_agent_config"
    assert ("get", "alu_env_config") in calls


def test_overrides(ws):
    t = classes(ws)["alu_error_test"]
    (ov,) = ws.calls_of(t).overrides
    assert (ov.kind, ov.original, ov.override) == ("type", "alu_driver", "alu_error_driver")
    t = classes(ws)["alu_add_test"]
    (ov,) = ws.calls_of(t).overrides
    assert (ov.original, ov.override) == ("alu_random_seq", "alu_add_only_seq")


def test_out_of_block_methods_attach_to_class(ws):
    drv = classes(ws)["alu_driver"]
    assert ("alu_pkg::alu_driver", "run_phase") in ws.impls
    proto = next(m for m in drv.methods if m.name == "run_phase")
    assert proto.is_prototype


def test_macro_shim_keeps_if_bodies_intact(ws):
    # Without the shim, `if (...) `uvm_fatal(...)` swallows the following statement.
    agent = classes(ws)["alu_agent"]
    creates = {cr.target: cr for cr in ws.calls_of(agent).creates}
    assert not creates["m_monitor"].conditional


def test_unsaved_buffer_override(tmp_path):
    f = tmp_path / "a.sv"
    f.write_text("class a; endclass\n")
    ix = Indexer([], {}, use_shim=True)
    idx = ix.parse(str(f), {str(f): "class b extends a; endclass\n"})
    assert [c.name for c in idx.classes] == ["b"]


def test_structural_pairs_are_plain_source_tokens(tmp_path):
    from dataclasses import asdict
    from test_folding_range import SOURCE

    path = tmp_path / "structure.sv"
    path.write_text(SOURCE)
    idx = Indexer([], {}, use_shim=True).parse(str(path))
    lines = SOURCE.splitlines()
    found = []
    for pair in idx.structures:
        a, b = pair.opening, pair.closing
        assert a.path == b.path == str(path)
        assert asdict(pair)["opening"]["path"] == str(path)
        found.append((lines[a.line][a.col:a.end_col], lines[b.line][b.col:b.end_col]))
    assert sorted(found) == sorted([("class", "endclass"), ("function", "endfunction"),
        ("function", "endfunction")] + [("begin", "end")] * 5)
    idx = Indexer([], {}, use_shim=True).parse(str(path), {str(path): "class c;\n int value;\n"})
    assert idx.structures == []


def test_generated_structural_endpoints(tmp_path):
    path = tmp_path / "structure.sv"
    source = '''`define START class
`define FINISH endclass
`START c;
  int value;
endclass
class d;
  int value;
`FINISH
class e;
`ifdef NEVER_DEFINED
endclass
`endif
  int value;
endclass
'''
    path.write_text(source)
    idx = Indexer([], {}, use_shim=True).parse(str(path))
    [pair] = idx.structures
    assert (pair.opening.line, pair.closing.line) == (8, 13)


def test_syntax_errors_are_reported(tmp_path):
    f = tmp_path / "bad.sv"
    f.write_text("class c;\n  int x = ;\nendclass\n")
    idx = Indexer([], {}, use_shim=True).parse(str(f))
    assert any(d.severity == "error" and d.range.line == 1 for d in idx.diagnostics)


def test_include_skipped_by_its_guard(tmp_path):
    (tmp_path / "h.svh").write_text("`ifndef H\n`define H\n`endif\n")
    (tmp_path / "a.sv").write_text('`include "h.svh"\n`include "h.svh"\nmodule a; endmodule\n')
    idx = Indexer([], {}, use_shim=True).parse(str(tmp_path / "a.sv"))
    h = str(tmp_path / "h.svh")
    assert idx.files == {str(tmp_path / "a.sv"), h}
    # pyslang 12 reports the include its guard skips, with no buffer; 11 (the last for Python 3.10) leaves it out.
    skipped = [(h, 1)] if int(pyslang.__version__.split(".")[0]) >= 12 else []
    assert [(t, r.line) for t, r in idx.includes[str(tmp_path / "a.sv")]] == [(h, 0)] + skipped


def test_latin1_source_text_is_indexed(tmp_path):
    # Legacy files are often Latin-1: comments, strings and include names that are not UTF-8.
    (tmp_path / "pkg.sv").write_bytes(
        b'package p;\n  import uvm_pkg::*;\n  `include "fich\xe9.svh"\n'
        b'  // Pilote g\xe9n\xe9ral\n  class drv #(parameter string MODE = "r\xe9el") extends uvm_component;\n'
        b'    function void build_phase(uvm_phase phase);\n'
        b'      uvm_config_db #(int)::set(this, "*", "d\xe9lai", 1);\n    endfunction\n  endclass\nendpackage\n')
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    drv = w.find_class("drv")
    assert drv.doc == "Pilote g\ufffdn\ufffdral" and drv.params[0].default == '"r\ufffdel"'
    assert [c.field_name for c in w.calls_of(drv).config_db] == ["d\ufffdlai"]
    assert "CouldNotOpenIncludeFile" in {d.code for d in w.user_diagnostics()}


def test_ignore_directives_are_not_documentation(tmp_path):
    f = tmp_path / 't.sv'
    f.write_text("""// uvm-lint: ignore utils-mismatch
class c;
  // Doc.
  // uvm-lint: ignore phase-no-super
  function void f(); endfunction
  // uvm-lint: ignore factory-ctor
  function void g(); endfunction
  /* uvm-lint: ignore is only a line-comment directive. */
  function void h(); endfunction
endclass
""")
    [cls] = Indexer([], {}, use_shim=True).parse(str(f)).classes
    assert cls.doc == ''
    assert {m.name: m.doc for m in cls.methods} == {
        'f': 'Doc.', 'g': '', 'h': 'uvm-lint: ignore is only a line-comment directive.'}


def test_doc_comments(tmp_path):
    f = tmp_path / "p.sv"
    f.write_text(
        "// Copyright and licence\n\n"
        "package p;\n"
        "//------\n// CLASS: drv #(T)\n//\n// Drives <item>s.\n//------\n\n"
        "typedef class item;\n\n"
        "class drv #(type T = int);\n"
        "  // Port: seq_item_port\n  //\n  // Where items come from.\n\n"
        "  int seq_item_port;\n"
        "  int x; // ends its own line\n"
        "  int y;\n\n"
        "  // a note about something else\n\n"
        "  int z;\n"
        "  // Function: run\n  //\n  // Runs.\n\n"
        "  virtual task run(); endtask\n"
        "  // Function: other\n  // Not about stop.\n\n"
        "  extern function void stop();\n"
        "endclass\n"
        "endpackage\n")
    (idx_cls,) = Indexer([], {}, use_shim=True).parse(str(f)).classes
    props = {v.name: v.doc for v in idx_cls.properties}
    meths = {m.name: m.doc for m in idx_cls.methods}
    # A NaturalDocs topic naming the declaration counts across blank lines and forward typedefs.
    assert idx_cls.doc == "Drives <item>s."
    assert props == {"seq_item_port": "Where items come from.", "x": "", "y": "", "z": ""}
    assert meths == {"run": "Runs.", "stop": ""}


EDIT_ANCHORS = '''package p;
  import uvm_pkg::*;
  `include "uvm_macros.svh"
  class item #(type T = int, int N = 4) extends uvm_object; // header
    int x;
    `uvm_object_param_utils_begin(item #(T, N))
      `uvm_field_int(x, UVM_ALL_ON)
    `uvm_object_utils_end
    function new(string name = "item", int k = 3);
      super.new(name);
    endfunction
  endclass
  class drv extends uvm_component;
    `uvm_component_utils(drv)
    `uvm_new_func
    function void build_phase(uvm_phase phase);
      int a;
      if (a) `uvm_info("X", "y", UVM_LOW)
      super.build_phase(phase);
      uvm_component::connect_phase(phase);
    endfunction
  endclass
  class ext extends uvm_object(5); endclass
endpackage
'''


def test_where_edits_go(tmp_path):
    """Header ends, body statements, argument defaults and the registration macro call, in the
    file; never inside a macro's text."""
    (tmp_path / "p.sv").write_text(EDIT_ANCHORS)
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    item, drv, ext = (w.find_class(n) for n in ("item", "drv", "ext"))
    lines = EDIT_ANCHORS.split("\n")

    def at(line, needle, end=None):  # the span of `needle` (through `end`) on a line
        col = lines[line].index(needle)
        stop = lines[line].index(end, col) + len(end) if end else col + len(needle)
        return (line, col, line, stop)

    span = lambda r: (r.line, r.col, r.end_line, r.end_col)  # noqa: E731
    assert span(item.header_end) == at(3, ";") and span(item.end_kw) == at(11, "endclass")
    assert (item.registration_macro, span(item.registration_range), item.registration_arg) == \
        ("uvm_object_param_utils_begin", at(5, "`uvm", "N))"), "item#(T,N)")
    assert (drv.registration_macro, span(drv.registration_range)) == \
        ("uvm_component_utils", at(13, "`uvm", "(drv)"))
    new = next(m for m in item.methods if m.name == "new")
    assert [(a.name, a.type_text, a.default) for a in new.args] == [("name", "string", '"item"'), ("k", "int", "3")]
    assert span(new.first_stmt) == at(9, "super", ";")
    assert [span(r) for r in new.body_items] == [at(9, "super", ";")]
    build = next(m for m in drv.methods if m.name == "build_phase")
    # The `if` ends where the macro call does; the declaration is not a statement.
    assert [span(r) for r in build.body_items] == [at(17, "if", "LOW)"), at(18, "super", ";"),
                                                   at(19, "uvm_component", ";")]
    assert build.first_stmt == build.body_items[0]
    assert build.super_calls == ["build_phase", "uvm_component::connect_phase"]
    macro_new = next(m for m in drv.methods if m.name == "new")  # `uvm_new_func
    assert macro_new.generated and macro_new.first_stmt is None and macro_new.body_items == []
    assert [a.name for a in macro_new.args] == ["name", "parent"]
    assert ext.extends_args == "(5)" and ext.registration_macro is None


def test_fix_data_of_parse_diagnostics(tmp_path):
    (tmp_path / "a.sv").write_text('`include "sub/missing_hdr.svh"\nmodule m;\n  intial begin end\nendmodule\n')
    idx = Indexer([], {}, use_shim=True).parse(str(tmp_path / "a.sv"))
    assert [(d.code, d.data) for d in idx.diagnostics] == [
        ("CouldNotOpenIncludeFile", {"include": "sub/missing_hdr.svh"}),
        ("TypoKeyword", {"from": "intial", "to": "initial"})]


def test_invocation_spans_and_formal_metadata(tmp_path):
    from test_fixes import index

    text = '''`define GENERATED f(1)
class C;
 function void f(input int a, b, output bit [3:0] v[], const ref string s="x,y"); endfunction
 function void run();
   f(1, 2);
   `GENERATED
   // f(3)
`ifdef ABSENT
   f(4);
`endif
   f(
 endfunction
endclass
'''
    ws = index(tmp_path, {"t.sv": text})
    path = str(tmp_path / 't.sv')
    calls = ws.invocations_by_file[path]
    assert len(calls) == 2
    assert calls[0].segments == [('f', [], '')]
    assert calls[0].closing is not None and calls[1].closing is None
    args = ws.classes[0].methods[0].args
    assert [(a.direction, a.type_text, a.dimensions, a.default) for a in args] == [
        ('input', 'int', '', None), ('input', 'int', '', None),
        ('output', 'bit[3:0]', '[]', None), ('const ref', 'string', '', '"x,y"')]
    ws.update_document(path, text.replace('f(1, 2);', ''))
    assert len(ws.invocations_by_file[path]) == 1
