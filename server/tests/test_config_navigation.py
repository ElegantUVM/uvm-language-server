"""config_db navigation over stdio on an indexed copy of the ALU testbench."""

import shutil

import pytest

from conftest import EXAMPLE
from test_code_actions import start

CAPABILITIES = {"textDocument": {"codeLens": {}, "hover": {"contentFormat": ["markdown"]}}}
COMMANDS = ["uvm.showConfigDb", "uvm.revealLocation"]


@pytest.fixture(scope="module")
def tb(tmp_path_factory):
    root = tmp_path_factory.mktemp("config-navigation") / "alu_tb"
    shutil.copytree(EXAMPLE, root)
    path = root / "env/alu_env.svh"
    path.write_text(path.read_text().replace("endclass", '''
  task config_examples();
    int value;
    string field;
    uvm_config_db #(int)::set(this, "*", "several", 1);
    uvm_config_db #(int)::set(this, "*", "several", 2);
    void'(uvm_config_db #(int)::get(this, "", "several", value));
    uvm_config_db #(int)::set(this, "*", "unread", 1);
    uvm_config_db #(int)::set(this, "*", "orphan", 1);
    uvm_config_db #(int)::set(this, "*", "mismatch", 1);
    void'(uvm_config_db #(int unsigned)::get(this, "", "mismatch", value));
    uvm_config_db #(int)::set(this, "*", field, 1);
    void'(uvm_config_db #(int)::get(this, "", field, value));
    uvm_config_db #(int)::set(this, "*", "same_line", 1); void'(uvm_config_db #(int)::get(this, "", "same_line", value));
    void'(uvm_config_db #(int)::exists(this, "", "unread"));
    uvm_config_db #(int)::wait_modified(this, "", "unread");
    uvm_resource_db #(int)::set("*", "resource", 1);
    void'(uvm_resource_db #(int)::read_by_name("", "resource", value));
  endtask
endclass'''))
    return root


@pytest.fixture(scope="module")
def client(tb):
    c = start(tb, capabilities=CAPABILITIES, clientCommands=COMMANDS)
    yield c
    c.close()


def lenses(client, path):
    return client.request("textDocument/codeLens", {"textDocument": {"uri": path.as_uri()}})


def config_lenses(client, path):
    return [lens for lens in lenses(client, path) if lens["command"]["title"].startswith("$(database)")]


def test_config_lenses(client, tb):
    path = tb / "env/alu_env.svh"
    found = config_lenses(client, path)
    titles = [lens["command"]["title"] for lens in found]
    assert "$(database) 'alu_agent_config' · 3 gets" in titles
    assert titles.count("$(database) 'several' · 1 get") == 2
    assert "$(database) 'several' · 2 sets" in titles
    assert "$(database) 'mismatch' · 1 get · type mismatch" in titles
    assert titles.count("$(database) config_db · dynamic field") == 2
    assert "$(database) 'unread' · 2 gets" in titles
    assert "$(database) 'orphan' · no get" in titles
    assert "$(database) 'resource' · 1 get" in titles
    positions = [lens["range"]["start"] for lens in found]
    assert positions == sorted(positions, key=lambda p: (p["line"], p["character"]))
    for lens in found:
        command = lens["command"]
        if "dynamic field" in command["title"]:
            assert command["arguments"] == [{}]
        if command["command"] == "uvm.showConfigDb" and "dynamic field" not in command["title"]:
            assert command["arguments"][0]["field"] in command["title"]
    [single] = config_lenses(client, tb / "agent/alu_driver.svh")
    assert single["command"]["title"] == "$(database) 'alu_agent_config' · set in alu_env.svh:16"
    assert single["command"]["command"] == "uvm.revealLocation"
    [target] = single["command"]["arguments"]
    assert target == {"file": str(path), "line": 15, "col": 4, "endLine": 15, "endCol": 99}
    line = (tb / "agent/alu_driver.svh").read_text().splitlines()[13]
    assert single["range"] == {"start": {"line": 13, "character": line.index("uvm_config_db")},
                                "end": {"line": 13, "character": line.rindex(")")}}
    assert any(l["command"]["title"] == "$(database) 'checks_enable' · no set"
               for l in config_lenses(client, tb / "agent/alu_monitor.svh"))
    assert any(l["command"]["title"] == "$(database) 'vif' · 1 get"
               for l in config_lenses(client, tb / "tb/tb_top.sv"))


def test_config_lenses_without_reveal_command(tb):
    c = start(tb, capabilities=CAPABILITIES, clientCommands=["uvm.showConfigDb"])
    try:
        [lens] = config_lenses(c, tb / "agent/alu_driver.svh")
        assert lens["command"]["command"] == "uvm.showConfigDb"
        assert lens["command"]["arguments"] == [{"field": "alu_agent_config"}]
    finally:
        c.close()


@pytest.mark.parametrize("capabilities,setting", [(CAPABILITIES, False), ({}, True)])
def test_config_lenses_opt_out_keeps_class_lenses(tb, capabilities, setting):
    c = start(tb, capabilities=capabilities, clientCommands=COMMANDS, settings={
        "filelists": [str(tb / "alu_tb.f")], "elaboration": "off", "codeLens": {"configDb": setting}})
    try:
        found = lenses(c, tb / "agent/alu_driver.svh")
        assert found and all(l["command"]["command"] == "uvm.showClassGraph" for l in found)
    finally:
        c.close()
