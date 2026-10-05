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


def at(path, needle, within=0, occurrence=0):
    text = path.read_text()
    offset = -1
    for _ in range(occurrence + 1):
        offset = text.index(needle, offset + 1)
    offset += within
    return {"textDocument": {"uri": path.as_uri()},
            "position": {"line": text[:offset].count("\n"),
                         "character": len(text[:offset].rsplit("\n", 1)[-1].encode("utf-16-le")) // 2}}


def hover(client, path, needle, within=0, occurrence=0):
    result = client.request("textDocument/hover", at(path, needle, within, occurrence))
    return result["contents"]["value"] if result else ""


def test_config_hover_on_literal_and_method(client, tb):
    path = tb / "agent/alu_driver.svh"
    md = hover(client, path, '"alu_agent_config"', 3)
    assert md.startswith("**config_db 'alu_agent_config'**")
    assert "type as set: alu_agent_config (alu_pkg::alu_agent_config)" in md
    assert 'set in alu_env.svh:16 for "m_agent\\*"' in md
    assert "read in alu_agent.svh:17, alu_driver.svh:14, alu_monitor.svh:17" in md
    assert md == hover(client, path, "::get", 3)
    assert "[show in the config_db cross-reference](command:uvm.showConfigDb?" in md
    import json
    from urllib.parse import unquote
    assert json.loads(unquote(md.rsplit("?", 1)[1][:-1])) == [{"field": "alu_agent_config"}]
    assert md == hover(client, tb / "env/alu_env.svh", '"alu_agent_config"', 2)
    assert "**config_db" not in hover(client, path, "m_cfg))", 2)
    assert "**config_db" not in hover(client, path, "#(alu_agent_config)", 5)
    assert "**config_db" not in hover(client, path, '"", "alu_agent_config"', 0)
    assert "**driver**" in hover(client, path, "class alu_driver", 8)


def test_config_hover_status_and_other_operations(client, tb):
    path = tb / "env/alu_env.svh"
    md = hover(client, path, '"mismatch"', 2)
    assert "set as int but read as int unsigned" in md
    md = hover(client, path, '"several"', 2)
    assert md.count("type as set: int") == 1
    assert md.count("set in alu_env.svh:") == 2
    assert "**config_db 'unread'**" in hover(client, path, "::exists", 3)
    assert "**config_db 'unread'**" in hover(client, path, "::wait_modified", 3)
    assert "**config_db 'resource'**" in hover(client, path, "::read_by_name", 3)
    # A dynamic set suppresses the lint's no-set finding, even though lenses show no literal set.
    assert "no set in the indexed sources" not in hover(client, tb / "agent/alu_monitor.svh", '"checks_enable"', 3)


@pytest.mark.parametrize("capabilities,commands,link", [
    ({}, COMMANDS, False),
    ({"textDocument": {"hover": {"contentFormat": ["plaintext"]}}}, COMMANDS, False),
    (CAPABILITIES, [], False),
    (CAPABILITIES, COMMANDS, True),
])
def test_config_hover_links_require_client_support(tb, capabilities, commands, link):
    c = start(tb, capabilities=capabilities, clientCommands=commands)
    try:
        md = hover(c, tb / "agent/alu_driver.svh", '"alu_agent_config"', 2)
        assert "**config_db 'alu_agent_config'**" in md
        assert ("command:uvm.showConfigDb?" in md) == link
    finally:
        c.close()


def test_config_hover_multiple_types_dynamic_scope_and_no_set(tmp_path):
    root = tmp_path / "alu_tb"
    shutil.copytree(EXAMPLE, root)
    path = root / "env/alu_env.svh"
    path.write_text(path.read_text().replace("endclass", '''
  typedef int integer_alias;
  function void extra();
    int value;
    string scope;
    uvm_config_db #(integer_alias)::set(this, scope, "typed", 1);
    uvm_config_db #(bit)::set(this, "*", "typed", 1);
    void'(uvm_config_db #(int)::get(this, "", "typed", value));
  endfunction
endclass'''))
    c = start(root, capabilities=CAPABILITIES, clientCommands=COMMANDS)
    try:
        md = hover(c, path, '"typed"', 2)
        assert "type as set: integer_alias (bit signed\\[31:0\\])" in md
        assert "type as set: bit" in md
        assert "for dynamic" in md
        assert "but read as" not in md
        md = hover(c, root / "agent/alu_monitor.svh", '"checks_enable"', 3)
        assert "no set in the indexed sources" in md
    finally:
        c.close()


def test_config_literal_ranges_with_unicode_and_crlf(tmp_path):
    root = tmp_path / "café"
    shutil.copytree(EXAMPLE, root)
    path = root / "env/alu_env.svh"
    text = path.read_text().replace("endclass", '''
  function void extra();
    int value;
    /* 😀 */ uvm_config_db #(int)::set(this, "café😀", "café😀", 1);
    void'(uvm_config_db #(int)::get(this, "",
      "café😀", value));
  endfunction
endclass''')
    path.write_bytes(text.replace("\n", "\r\n").encode())
    c = start(root, capabilities=CAPABILITIES, clientCommands=COMMANDS)
    try:
        params = at(path, '"café😀"', 0, occurrence=1)
        result = c.request("textDocument/hover", params)
        assert result["contents"]["value"].startswith("**config_db 'café😀'**")
        assert result["range"] == {"start": params["position"],
                                   "end": at(path, '"café😀"', len('"café😀"'), occurrence=1)["position"]}
        assert "**config_db" not in hover(c, path, '"café😀"', 1)
        assert "**config_db 'café😀'**" in hover(c, path, '"café😀"', 2, occurrence=2)
        assert definitions(c, path, '"café😀"', occurrence=2) == [literal_location(path, "café😀", 1)]
    finally:
        c.close()


def literal_location(path, field, occurrence=0):
    needle = '"' + field + '"'
    return {"uri": path.as_uri(),
            "range": {"start": at(path, needle, occurrence=occurrence)["position"],
                      "end": at(path, needle, len(needle), occurrence)["position"]}}


def definitions(client, path, needle, within=1, occurrence=0):
    return client.request("textDocument/definition", at(path, needle, within, occurrence))


def test_config_definition_from_get_to_set_and_back(client, tb):
    env = tb / "env/alu_env.svh"
    assert definitions(client, tb / "agent/alu_driver.svh", '"alu_agent_config"') == [
        literal_location(env, "alu_agent_config")]
    expected = [literal_location(tb / "agent" / name, "alu_agent_config")
                for name in ("alu_agent.svh", "alu_driver.svh", "alu_monitor.svh")]
    assert sorted(definitions(client, env, '"alu_agent_config"'), key=lambda loc: loc["uri"]) == expected


@pytest.mark.parametrize("field,get_occurrence", [("several", 2), ("same_line", 1), ("resource", 1)])
def test_config_definition_all_candidates_and_same_line_calls(client, tb, field, get_occurrence):
    path = tb / "env/alu_env.svh"
    assert definitions(client, path, '"' + field + '"', occurrence=get_occurrence) == [
        literal_location(path, field, i) for i in range(get_occurrence)]
    assert definitions(client, path, '"' + field + '"') == [literal_location(path, field, get_occurrence)]


def test_config_definition_exists_and_wait_modified(client, tb):
    path = tb / "env/alu_env.svh"
    for occurrence in (1, 2):
        assert definitions(client, path, '"unread"', occurrence=occurrence) == [literal_location(path, "unread")]
    assert definitions(client, path, '"unread"') == [literal_location(path, "unread", i) for i in (1, 2)]


def test_config_definition_falls_back_without_counterparts_or_off_literal(client, tb):
    path = tb / "agent/alu_monitor.svh"
    [found] = definitions(client, path, '"checks_enable"')
    assert found["uri"] == path.as_uri() and found["range"]["start"]["line"] == 7
    [found] = definitions(client, path, "#(alu_agent_config)", 4)
    assert found["uri"] == (tb / "agent/alu_agent_config.svh").as_uri()
    [found] = definitions(client, path, "::get", 3)
    assert found["uri"].endswith("uvm_config_db.svh")
    assert definitions(client, tb / "env/alu_env.svh", '"orphan"') is None


def test_config_navigation_follows_unsaved_set_location(tb):
    c = start(tb, capabilities=CAPABILITIES, clientCommands=COMMANDS)
    path = tb / "env/alu_env.svh"
    try:
        c.forget("uvm/indexChanged")
        c.notify("textDocument/didOpen", {"textDocument": {
            "uri": path.as_uri(), "languageId": "systemverilog", "version": 1,
            "text": "// moved\n" + path.read_text()}})
        c.wait_for("uvm/indexChanged")
        expected = literal_location(path, "alu_agent_config")
        for pos in expected["range"].values():
            pos["line"] += 1
        driver = tb / "agent/alu_driver.svh"
        assert definitions(c, driver, '"alu_agent_config"') == [expected]
        assert "set in alu_env.svh:17" in hover(c, driver, '"alu_agent_config"', 1)
        assert "set in alu_env.svh:17" in config_lenses(c, driver)[0]["command"]["title"]
    finally:
        c.close()
