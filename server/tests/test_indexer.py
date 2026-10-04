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
    assert [(t, r.line) for t, r in idx.includes[str(tmp_path / "a.sv")]] == [(h, 0), (h, 1)]


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
