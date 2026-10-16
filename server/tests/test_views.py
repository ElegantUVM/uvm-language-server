from uvmls.lints import lint
from uvmls.project import display_path
from uvmls.views import class_graph, config_db_report, topology


def test_roles(any_ws):
    role = {c.name: any_ws.role(c) for c in any_ws.classes}
    assert role["alu_driver"] == "driver"
    assert role["alu_error_driver"] == "driver"
    assert role["alu_coverage"] == "subscriber"
    assert role["alu_agent_config"] == "config"
    assert role["alu_add_only_seq"] == "sequence"


def test_type_parameter_binding(any_ws):
    drv = any_ws.find_class("alu_driver")
    owner, member, bindings = any_ws.find_member(drv, "req")
    assert bindings["REQ"] == "alu_seq_item"
    assert any_ws.member_type_spec(owner, member, bindings).cls.name == "alu_seq_item"


def test_class_graph_edges(ws):
    g = class_graph(ws)
    edges = {(e["source"], e["target"], e["kind"]) for e in g["edges"]}
    assert ("alu_pkg::alu_error_driver", "alu_pkg::alu_driver", "extends") in edges
    assert ("alu_pkg::alu_driver", "uvm_driver", "extends") in edges
    assert ("alu_pkg::alu_agent", "alu_pkg::alu_sequencer", "has") in edges  # through the typedef
    assert ("alu_pkg::alu_sequencer", "uvm_sequencer", "typedef") in edges
    assert ("alu_pkg::alu_driver", "alu_pkg::alu_error_driver", "override") in edges
    assert ("alu_test_pkg::alu_base_test", "alu_seq_pkg::alu_random_seq", "creates") in edges
    has_driver = next(e for e in g["edges"] if e["source"] == "alu_pkg::alu_agent"
                      and e["target"] == "alu_pkg::alu_driver")
    assert has_driver["created"] and has_driver["conditional"]
    node = {n["id"]: n for n in g["nodes"]}
    assert node["uvm_driver"]["libDistance"] == 1
    assert node["uvm_report_object"]["libDistance"] == 3  # via uvm_driver -> uvm_component


def test_topology_applies_factory_override(any_ws):
    t = topology(any_ws, "alu_error_test")
    agent = t["root"]["children"][0]["children"][0]
    kids = {c["name"]: c for c in agent["children"]}
    assert kids["m_driver"]["type"] == "alu_error_driver"
    assert kids["m_driver"]["overriddenFrom"] == "alu_driver"
    assert kids["m_driver"]["conditional"]
    assert kids["m_sequencer"]["role"] == "sequencer"


def test_topology_connections(any_ws):
    t = topology(any_ws)
    assert t["test"] == "alu_random_test"  # from run_test("alu_random_test")
    conns = {(c["source"].split(".")[-1], c["sourcePort"], c["target"].split(".")[-1], c["targetPort"])
             for c in t["connections"]}
    assert ("m_driver", "seq_item_port", "m_sequencer", "seq_item_export") in conns
    assert ("m_agent", "ap", "m_scoreboard", "item_export") in conns


def test_config_db_report(ws):
    rows = {r["field"]: r for r in config_db_report(ws)["fields"]}
    assert rows["vif"]["status"] == "ok"
    assert rows["checks_enable"]["status"] == "no-set"
    assert len(rows["alu_agent_config"]["gets"]) == 3


def test_lint_flags_only_the_planted_problem(any_ws):
    found = [(d.code, display_path(d.range.path).rsplit("/", 1)[-1]) for ds in lint(any_ws).values() for d in ds]
    assert found == [("config-db-no-set", "alu_monitor.svh")]
    assert not [d for d in any_ws.user_diagnostics() if d.severity in ("error", "warning")]


def test_lint_unregistered_create_and_type_mismatch(tmp_path):
    from uvmls.project import ProjectConfig
    from uvmls.workspace import Workspace
    (tmp_path / "t.sv").write_text('''
class cfg extends uvm_object; endclass
class drv extends uvm_driver #(uvm_sequence_item);
  `uvm_component_utils(drv)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  cfg c; int n;
  function void build_phase(uvm_phase phase);
    c = cfg::type_id::create("c");
    void'(uvm_config_db#(int)::get(this, "", "depth", n));
  endfunction
endclass
class env extends uvm_env;
  `uvm_object_utils(env)
  function void build_phase(uvm_phase phase);
    uvm_config_db#(bit)::set(this, "*", "depth", 1);
  endfunction
endclass
''', encoding="utf-8", newline="")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    found = sorted((d.code, d.range.line, d.data) for ds in lint(w).values() for d in ds)
    assert found == [("config-db-type-mismatch", 8, {"field": "depth", "at": [8, 10]}),
                     ("create-unregistered", 7, {"target": "cfg"}),
                     ("utils-mismatch", 11, {"cls": "env"})]


CONFIG_DB = '''import uvm_pkg::*;
typedef virtual dut_if dut_vif;
class env #(type CFG_T = uvm_object) extends uvm_env;
  `uvm_component_param_utils(env #(CFG_T))
  dut_vif vif; CFG_T cfg; int depth; bit en; int knob;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    if (!uvm_config_db #(virtual dut_if)::get(this, "", "vif", vif)) `uvm_fatal("VIF", "no vif")
    if (!uvm_config_db #(CFG_T)::get(this, "", "cfg", cfg)) `uvm_fatal("CFG", "no cfg")
    if (!uvm_config_db #(int)::get(this, "", "depht", depth)) `uvm_fatal("D", "no depth")
    void'(uvm_config_db #(bit)::get(this, "", "enable", en));
    if (!uvm_config_db #(int)::get(this, "", "knob", knob)) knob = 3;
    if (depth > 2) begin
      if (!uvm_config_db #(int)::get(this, "", "extra", knob)) `uvm_fatal("X", "no extra")
    end
    if (!uvm_config_db #(int)::get(this, "", "needed", knob)) `uvm_fatal("N", "no needed")
  endfunction
endclass
module tb;
  initial begin
    uvm_config_db #(dut_vif)::set(null, "*", "vif", null);
    uvm_config_db #(uvm_object)::set(null, "*", "cfg", null);
    uvm_config_db #(int)::set(null, "*", "depth", 4);
  end
endmodule
'''


def test_config_db_precision(tmp_path):
    """Types compare after typedefs; a type parameter could be anything; an optional or conditional
    get with no set is a note, even when a set of a close name looks like a typo."""
    from uvmls.project import ProjectConfig
    from uvmls.workspace import Workspace
    (tmp_path / "t.sv").write_text(CONFIG_DB, encoding="utf-8", newline="")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    rows = {r["field"]: r for r in config_db_report(w)["fields"]}
    assert rows["vif"]["status"] == "ok" and rows["cfg"]["status"] == "ok"
    found = {d.data["field"]: (d.code, d.severity, d.message) for ds in lint(w).values() for d in ds}
    assert set(found) == {"depht", "enable", "knob", "extra", "needed"}
    assert found["depht"] == ("config-db-no-set", "warning", "uvm_config_db get of 'depht' (int) has no matching set "
                              "in the indexed sources (it may come from +uvm_set_config_* on the command line); "
                              "did you mean 'depth' (set in t.sv:23)?")
    assert found["enable"][:2] == found["knob"][:2] == ("config-db-no-set", "info")
    assert found["knob"][2].endswith("no matching set in the indexed sources; the code carries on without it")
    assert found["extra"][1:] == ("info", "uvm_config_db get of 'extra' (int) has no matching set in the indexed "
                                  "sources; it is read only under a condition")
    assert found["needed"][1] == "warning"


def test_config_db_type_mismatch_needs_one_type_set(tmp_path):
    # A name used in several scopes, set with two types: a get of a third is not known to fail.
    from uvmls.project import ProjectConfig
    from uvmls.workspace import Workspace
    text = CONFIG_DB.replace('uvm_config_db #(int)::set(null, "*", "depth", 4);',
                             'uvm_config_db #(int)::set(null, "*", "depth", 4);\n'
                             '    uvm_config_db #(bit)::set(null, "*.other", "vif", 0);')
    (tmp_path / "t.sv").write_text(text.replace("#(virtual dut_if)::get", "#(virtual other_if)::get"), encoding="utf-8", newline="")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    assert not [d for ds in lint(w).values() for d in ds if d.code == "config-db-type-mismatch"]
    (tmp_path / "t.sv").write_text(CONFIG_DB.replace("#(virtual dut_if)::get", "#(virtual other_if)::get"), encoding="utf-8", newline="")
    w.reindex_all()
    [d] = [d for ds in lint(w).values() for d in ds if d.code == "config-db-type-mismatch"]
    assert d.message == "'vif' is set as dut_vif but read as virtual other_if; uvm_config_db matches on the " \
                        "exact type, so this get will fail"


def test_topology_callers_get_their_own_copy(any_ws):
    first = topology(any_ws)
    first["root"]["children"].clear()
    assert topology(any_ws)["root"]["children"]


def test_failed_effective_type_build_is_not_cached(any_ws, monkeypatch):
    import pytest
    from uvmls import views

    any_ws._effective_type_cache.clear()
    calls = any_ws.calls_in_chain

    def broken(cls):
        raise RuntimeError("resolution bug")
    monkeypatch.setattr(any_ws, "calls_in_chain", broken)
    with pytest.raises(RuntimeError):
        views.effective_type(any_ws)
    monkeypatch.setattr(any_ws, "calls_in_chain", calls)
    assert topology(any_ws)["root"]["children"]
