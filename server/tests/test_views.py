from uvmls.lints import lint
from uvmls.views import class_graph, config_db_report, topology


def test_roles(ws):
    role = {c.name: ws.role(c) for c in ws.classes}
    assert role["alu_driver"] == "driver"
    assert role["alu_error_driver"] == "driver"
    assert role["alu_coverage"] == "subscriber"
    assert role["alu_agent_config"] == "config"
    assert role["alu_add_only_seq"] == "sequence"


def test_type_parameter_binding(ws):
    drv = ws.find_class("alu_driver")
    owner, member, bindings = ws.find_member(drv, "req")
    assert bindings["REQ"] == "alu_seq_item"
    assert ws.member_type_spec(owner, member, bindings).cls.name == "alu_seq_item"


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


def test_topology_applies_factory_override(ws):
    t = topology(ws, "alu_error_test")
    agent = t["root"]["children"][0]["children"][0]
    kids = {c["name"]: c for c in agent["children"]}
    assert kids["m_driver"]["type"] == "alu_error_driver"
    assert kids["m_driver"]["overriddenFrom"] == "alu_driver"
    assert kids["m_driver"]["conditional"]
    assert kids["m_sequencer"]["role"] == "sequencer"


def test_topology_connections(ws):
    t = topology(ws)
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


def test_lint_flags_only_the_planted_problem(ws):
    found = [(d.code, d.range.path.split("/")[-1]) for ds in lint(ws).values() for d in ds]
    assert found == [("config-db-no-set", "alu_monitor.svh")]


def test_lint_unregistered_create_and_type_mismatch(tmp_path):
    from uvmls.project import ProjectConfig
    from uvmls.workspace import Workspace
    (tmp_path / "t.sv").write_text('''
class cfg extends uvm_object; endclass
class drv extends uvm_driver #(uvm_sequence_item);
  `uvm_component_utils(drv)
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
''')
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    codes = sorted(d.code for ds in lint(w).values() for d in ds)
    assert codes == ["config-db-type-mismatch", "create-unregistered", "utils-mismatch"]
