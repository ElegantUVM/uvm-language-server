"""The MCP server as an agent drives it: `python -m uvmls mcp` over stdio through tools/mcp_smoke.py,
on a copy of the example (acceptance items 1-11 of docs/specs/f7-mcp.md)."""

import asyncio
import json
import os
import shutil
import sys
import time

import pytest

from conftest import EXAMPLE
from uvmls import mcp
from uvmls.cli import main as cli
from uvmls.indexer import Indexer
from uvmls.project import ProjectConfig
from uvmls.workspace import Workspace

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(SERVER_DIR), "tools"))
from mcp_smoke import Smoke  # noqa: E402

TOOL_NAMES = ["stats", "tests", "classes", "class", "topology", "effective_type", "config_db", "lint", "elaborate",
              "definition", "hover", "references", "symbols", "signature", "sequences", "creates", "includes",
              "units", "macros", "typedefs", "reindex"]
BUNDLED_OBJECT = os.path.join(SERVER_DIR, "uvmls", "third_party", "uvm-1.2", "src", "base", "uvm_object.svh")
DUP_PACKAGE = '''package dup_pkg;
  import uvm_pkg::*;
  `include "uvm_macros.svh"
  class alu_agent extends uvm_agent;
    `uvm_component_utils(alu_agent)
    function new(string name, uvm_component parent); super.new(name, parent); endfunction
  endclass
endpackage
'''


def start(root: str, protocol: str = "2025-06-18") -> Smoke:
    """A server on `root` with its filelist, initialized; UVM_HOME unset so the bundled UVM is used."""
    env = dict(os.environ, PYTHONPATH=SERVER_DIR)
    env.pop("UVM_HOME", None)
    c = Smoke([sys.executable, "-m", "uvmls", "mcp", root, "-f", "alu_tb.f"], cwd=root, env=env)
    c.initialize(protocol)
    return c


def clean(c: Smoke):
    assert "Traceback" not in c.stderr, c.stderr


def serve(root, *flags, protocol: str = "2025-06-18") -> Smoke:
    """A server on a small workspace of its own, on the built-in UVM model (fast to index)."""
    env = dict(os.environ, PYTHONPATH=SERVER_DIR)
    env.pop("UVM_HOME", None)
    c = Smoke([sys.executable, "-m", "uvmls", "mcp", str(root), "--no-bundled-uvm", *flags], cwd=str(root), env=env)
    c.initialize(protocol)
    return c


def names(c: Smoke) -> list[str]:
    return sorted(i["name"] for i in c.data("classes")["items"])


@pytest.fixture(scope="module")
def tb(tmp_path_factory):
    root = tmp_path_factory.mktemp("mcp") / "alu_tb"
    shutil.copytree(EXAMPLE, root)
    return str(root)


@pytest.fixture(scope="module")
def server(tb):
    c = start(tb)
    yield c
    clean(c)
    assert c.close() == 0


def read(root: str, rel: str) -> str:
    with open(os.path.join(root, rel), newline="") as f:
        return f.read()


def write(root: str, rel: str, text: str):
    with open(os.path.join(root, rel), "w", newline="") as f:
        f.write(text)


def at(root: str, rel: str, needle: str, offset: int = 0) -> dict:
    """The 1-based position of `needle` (plus offset) in a file of the copy, as a tool argument."""
    text = read(root, rel)
    idx = text.index(needle) + offset
    return {"file": rel, "line": text.count("\n", 0, idx) + 1, "col": idx - text.rfind("\n", 0, idx)}


def cli_json(capsys, monkeypatch, root: str, *args) -> dict:
    monkeypatch.delenv("UVM_HOME", raising=False)
    cli([*args, root, "-f", os.path.join(root, "alu_tb.f"), "--json"])
    return json.loads(capsys.readouterr().out)


# -- 1. handshake, listing, schemas --------------------------------------------------------------


def test_handshake_lists_and_schemas(tb, server):
    init = server.result("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    assert init["protocolVersion"] == "2025-06-18"
    assert init["capabilities"] == {"tools": {"listChanged": False}, "resources": {"listChanged": False},
                                    "prompts": {"listChanged": False}}
    assert init["serverInfo"]["name"] == "uvmls" and init["serverInfo"]["version"]
    assert server.request("server/discover")["error"]["code"] == -32601
    newer = start(tb, protocol="2026-07-28")
    try:
        assert newer.result("initialize", {"protocolVersion": "2026-07-28"})["protocolVersion"] == "2025-11-25"
    finally:
        newer.close()
    tools = server.result("tools/list")["tools"]
    assert [t["name"] for t in tools] == TOOL_NAMES
    for t in tools:
        schema = t["inputSchema"]
        assert t["title"] and t["description"] and t["annotations"]["readOnlyHint"] is True
        assert schema["type"] == "object" and isinstance(schema["properties"], dict)
        assert set(schema.get("required", [])) <= set(schema["properties"])
        for name, prop in schema["properties"].items():
            assert prop["type"] in ("string", "integer", "boolean", "array") and prop["description"], (t["name"], name)
    assert [r["uri"] for r in server.result("resources/list")["resources"]] == \
        ["uvm://stats", "uvm://topology", "uvm://configdb", "uvm://lint"]
    assert [r["uriTemplate"] for r in server.result("resources/templates/list")["resourceTemplates"]] == \
        ["uvm://topology/{test}", "uvm://file/{path}"]
    assert server.result("prompts/list") == {"prompts": []}
    clean(server)


# -- 2. classes ----------------------------------------------------------------------------------


def test_classes_filter_glob_and_page(server):
    drivers = server.data("classes", {"role": "driver"})
    assert [(i["qualname"], i["location"]["file"]) for i in drivers["items"]] == \
        [("alu_pkg::alu_driver", "agent/alu_driver.svh"), ("alu_pkg::alu_error_driver", "agent/alu_driver.svh")]
    assert drivers["items"][0]["location"] == {"file": "agent/alu_driver.svh", "line": 2, "col": 7, "endLine": 2, "endCol": 17}
    assert drivers["total"] == 2 and drivers["truncated"] is False
    everything = server.data("classes", {"query": "alu_*"})
    assert everything["total"] == 19 and all(i["name"].startswith("alu_") for i in everything["items"])
    assert [i["name"] for i in server.data("classes", {"query": "*_driver"})["items"]] == ["alu_driver", "alu_error_driver"]
    first = server.data("classes", {"limit": 1})
    assert first["truncated"] is True and first["total"] == 19 and len(first["items"]) == 1
    second = server.data("classes", {"limit": 1, "offset": 1})
    assert second["items"] == [everything["items"][1]] and second["truncated"] is True
    tail = server.data("classes", {"offset": 18})
    assert len(tail["items"]) == 1 and tail["truncated"] is False
    assert server.data("classes", {"role": "driver", "library": True})["total"] > 2  # uvm_driver and its kin
    assert "must be one of" in server.error_text("classes", {"role": "pilot"})
    tests = server.data("tests")
    assert [i["name"] for i in tests["items"]] == \
        ["alu_add_test", "alu_base_test", "alu_error_test", "alu_passive_test", "alu_random_test"]
    assert tests["default"] == "alu_random_test" and tests["runTest"] == ["alu_random_test"]
    clean(server)


# -- 3. class ------------------------------------------------------------------------------------


def test_class_members_registration_and_creator(server):
    agent = server.data("class", {"name": "alu_agent"})
    props = {p["name"]: p["type"] for p in agent["properties"]}
    assert props["m_driver"] == "alu_driver" and props["m_sequencer"] == "alu_sequencer" and props["m_monitor"] == "alu_monitor"
    methods = {m["name"]: m for m in agent["methods"]}
    assert methods["build_phase"]["signature"] == "function void build_phase(uvm_phase phase)"
    assert methods["connect_phase"]["signature"] == "function void connect_phase(uvm_phase phase)"
    assert methods["build_phase"]["location"]["file"] == "agent/alu_agent.svh"
    assert agent["registration"] == "component" and agent["registrationMacro"] == "uvm_component_utils"
    assert [(c["owner"], c["instName"], c["parent"]) for c in agent["createdBy"]] == [("alu_env_pkg::alu_env", "m_agent", "this")]
    assert [e["name"] for e in agent["chain"]][:3] == ["alu_agent", "uvm_agent", "uvm_component"]
    assert agent["declaration"]["line"] == 1 and agent["declaration"]["endLine"] == 31
    driver = server.data("class", {"name": "alu_pkg::alu_driver"})
    assert [s["qualname"] for s in driver["subclasses"]] == ["alu_pkg::alu_error_driver"]
    assert driver["chain"][1]["bindings"] == {"REQ": "alu_seq_item", "RSP": "alu_seq_item"}
    assert "no class nothing" in server.error_text("class", {"name": "nothing"})
    clean(server)


def test_class_declared_in_two_packages_names_the_candidates(tmp_path):
    root = tmp_path / "alu_tb"
    shutil.copytree(EXAMPLE, root)
    (root / "tests" / "dup_pkg.sv").write_text(DUP_PACKAGE)
    (root / "alu_tb.f").write_text(read(str(root), "alu_tb.f") + "tests/dup_pkg.sv\n")
    c = start(str(root))
    try:
        message = c.error_text("class", {"name": "alu_agent"})
        assert "alu_pkg::alu_agent" in message and "dup_pkg::alu_agent" in message
        assert c.data("class", {"name": "dup_pkg::alu_agent"})["location"]["file"] == "tests/dup_pkg.sv"
        assert c.data("class", {"name": "alu_pkg::alu_agent"})["location"]["file"] == "agent/alu_agent.svh"
        clean(c)
    finally:
        c.close()


# -- 4. topology and effective_type ------------------------------------------------------------


def shape(node: dict):
    return (node["name"], node["type"], node["class"], node["role"], node["overriddenFrom"], node["conditional"],
            [shape(c) for c in node["children"]])


def find(node: dict, name: str) -> dict:
    if node["name"] == name:
        return node
    for c in node["children"]:
        found = find(c, name)
        if found:
            return found
    return None


def test_topology_matches_the_cli_after_location_conversion(server, tb, capsys, monkeypatch):
    tool = server.data("topology", {"test": "alu_error_test"})
    ref = cli_json(capsys, monkeypatch, tb, "topology", "--test", "alu_error_test")
    assert tool["test"] == ref["test"] == "alu_error_test" and tool["tests"] == ref["tests"]
    assert shape(tool["root"]) == shape(ref["root"])
    assert len(tool["connections"]) == len(ref["connections"]) >= 3
    driver, ref_driver = find(tool["root"], "m_driver"), find(ref["root"], "m_driver")
    assert driver["overriddenFrom"] == "alu_driver" and driver["type"] == "alu_error_driver"
    assert driver["location"] == {"file": "agent/alu_driver.svh", "line": ref_driver["line"] + 1, "col": ref_driver["col"] + 1,
                                  "endLine": ref_driver["endLine"] + 1, "endCol": ref_driver["endCol"] + 1}
    assert driver["createdAt"]["line"] == ref_driver["createdAt"]["line"] + 1 and driver["createdAt"]["file"] == "agent/alu_agent.svh"
    assert not any(k in driver for k in ("file", "line", "col", "endLine", "endCol"))
    assert [(o["original"], o["override"], o["location"]["file"]) for o in tool["overrides"]] == \
        [("alu_driver", "alu_error_driver", "tests/alu_tests.svh")]
    default = server.data("topology")
    assert default["test"] == "alu_random_test" and find(default["root"], "m_driver")["type"] == "alu_driver"
    assert default["tests"] == ["alu_add_test", "alu_base_test", "alu_error_test", "alu_passive_test", "alu_random_test"]
    message = server.error_text("topology", {"test": "no_such"})
    assert "no_such" in message and "alu_random_test" in message and "alu_error_test" in message
    assert "no_such" in server.request("resources/read", {"uri": "uvm://topology/no_such"})["error"]["message"]
    clean(server)


def test_effective_type_of_the_driver_create(server, tb):
    create = at(tb, "agent/alu_agent.svh", 'alu_driver::type_id::create("m_driver"')
    where = {"file": create["file"], "line": create["line"]}
    [item] = server.data("effective_type", {"test": "alu_error_test", **where})["items"]
    assert (item["original"], item["effective"], item["instances"], item["count"]) == ("alu_driver", "alu_error_driver", 1, 1)
    assert item["create"]["line"] == create["line"] and item["create"]["file"] == "agent/alu_agent.svh"
    assert item["override"]["location"]["file"] == "tests/alu_tests.svh" and item["override"]["owner"] == "alu_error_test"
    [plain] = server.data("effective_type", {"test": "alu_random_test", **where})["items"]
    assert (plain["original"], plain["effective"], plain["override"]) == ("alu_driver", "alu_driver", None)
    everything = server.data("effective_type", {"test": "alu_error_test"})
    assert everything["total"] >= 6 and everything["test"] == "alu_error_test"
    assert "no_such" in server.error_text("effective_type", {"test": "no_such"})
    assert server.error_text("effective_type", {"line": 3}) == "line needs file"
    clean(server)


# -- 5. config_db ---------------------------------------------------------------------------------


def test_config_db_matches_the_cli_and_filters(server, tb, capsys, monkeypatch):
    rows = [r for r in server.data("config_db")["items"] if r["status"] != "dynamic"]
    ref = cli_json(capsys, monkeypatch, tb, "configdb")["fields"]
    assert [(r["field"], r["status"], r["types"]) for r in rows] == [(r["field"], r["status"], r["types"]) for r in ref]
    for row, ref_row in zip(rows, ref):
        for kind in ("sets", "gets"):
            for entry, ref_entry in zip(row[kind], ref_row[kind]):
                assert (entry["type"], entry["inst"], entry["owner"], entry["method"]) == \
                    (ref_entry["type"], ref_entry["inst"], ref_entry["owner"], ref_entry["method"])
                assert entry["location"]["line"] == ref_entry["line"] + 1 and entry["location"]["col"] == ref_entry["col"] + 1
                assert os.path.join(tb, entry["location"]["file"]) == ref_entry["file"]
    [row] = server.data("config_db", {"field": "alu_agent_config"})["items"]
    assert row["status"] == "ok" and len(row["sets"]) == 1 and len(row["gets"]) == 3
    assert row["sets"][0]["location"] == {"file": "env/alu_env.svh", "line": 16, "col": 5, "endLine": 16, "endCol": 100}
    assert [r["field"] for r in server.data("config_db", {"file": "env/alu_env.svh"})["items"]] == ["alu_agent_config", "alu_env_config"]
    env = read(tb, "env/alu_env.svh")
    try:
        write(tb, "env/alu_env.svh", env.replace('"alu_env_config", m_cfg', '"alu_env_cofnig", m_cfg'))
        server.data("reindex")
        planted = server.data("config_db", {"status": "no-set"})["items"]
        assert {r["field"] for r in planted} == {"alu_env_cofnig", "checks_enable"}  # the example plants checks_enable
        assert all(r["status"] == "no-set" and not r["sets"] for r in planted)
        [typo] = [r for r in planted if r["field"] == "alu_env_cofnig"]
        assert typo["gets"][0]["location"]["file"] == "env/alu_env.svh" and typo["gets"][0]["owner"] == "alu_env_pkg::alu_env"
    finally:
        write(tb, "env/alu_env.svh", env)
        server.data("reindex")
    clean(server)


# -- 6. lint and the ignore directive ------------------------------------------------------------


def test_lint_lists_the_planted_finding_until_an_ignore_directive_lands_on_disk(server, tb):
    get = at(tb, "agent/alu_monitor.svh", 'uvm_config_db #(bit)::get(this, "", "checks_enable"')
    [finding] = server.data("lint")["items"]
    assert finding["code"] == "config-db-no-set" and finding["severity"] == "info"
    assert finding["location"]["file"] == "agent/alu_monitor.svh" and finding["location"]["line"] == get["line"]
    assert server.data("lint", {"codes": ["config-db-no-set"]})["total"] == 1
    assert server.data("lint", {"codes": ["create-no-parent"]})["total"] == 0
    assert server.data("lint", {"severity": "warning"})["total"] == 0
    assert server.data("lint", {"file": "agent/alu_monitor.svh"})["total"] == 1
    assert json.loads(server.read_resource("uvm://lint")["contents"][0]["text"])["total"] == 1
    monitor = read(tb, "agent/alu_monitor.svh")
    lines = monitor.split("\n")
    lines[get["line"] - 1] += "  // uvm-lint: ignore config-db-no-set"
    try:
        write(tb, "agent/alu_monitor.svh", "\n".join(lines))
        time.sleep(1.1)  # the refresh looks at the files at most once a second
        assert server.data("lint")["items"] == []
    finally:
        write(tb, "agent/alu_monitor.svh", monitor)
        server.data("reindex")
    assert server.data("lint")["total"] == 1
    clean(server)


# -- 7. navigation ---------------------------------------------------------------------------------


def test_definition_hover_signature_and_symbols(server, tb):
    declaration = {"file": "agent/alu_driver.svh", "line": 2, "col": 7, "endLine": 2, "endCol": 17}
    assert server.data("symbols", {"query": "alu_pkg::alu_driver", "definition": True})["items"][0]["location"] == declaration
    assert server.data("definition", at(tb, "agent/alu_agent.svh", "alu_driver       m_driver;"))["locations"] == [declaration]
    assert server.data("definition", at(tb, "agent/alu_agent.svh", "m_driver    = alu_driver"))["locations"] == \
        [{"file": "agent/alu_agent.svh", "line": 5, "col": 20, "endLine": 5, "endCol": 28}]
    [field] = server.data("definition", at(tb, "agent/alu_agent.svh", '"alu_agent_config", m_cfg', 2))["locations"]
    assert (field["file"], field["line"]) == ("env/alu_env.svh", 16)  # a get's field literal goes to the set's
    assert read(tb, field["file"]).split("\n")[15][field["col"] - 1:field["endCol"] - 1] == '"alu_agent_config"'
    hover = server.data("hover", at(tb, "agent/alu_driver.svh", "get_next_item(req)"))["markdown"]
    assert "task get_next_item(output REQ t)" in hover
    assert "doc from `uvm_sqr_if_base::get_next_item`" in hover and "Retrieves the next available item" in hover
    assert server.data("hover", at(tb, "agent/alu_driver.svh", "// Drives"))["markdown"] is None
    found = server.data("signature", at(tb, "agent/alu_driver.svh", "drive_item(req)", len("drive_item(")))["signature"]
    assert found["label"] == "task drive_item(alu_seq_item item)" and found["formals"] == ["alu_seq_item item"]
    assert found["active"] == 0 and found["owner"] == "alu_pkg::alu_driver"
    assert server.data("signature", at(tb, "agent/alu_driver.svh", "// Drives"))["signature"] is None
    refused = server.error_text("hover", {"file": BUNDLED_OBJECT, "line": 1, "col": 1})
    assert refused.startswith("outside the workspace") and "library file" in refused
    assert server.error_text("hover", {"file": "../other.svh", "line": 1, "col": 1}).startswith("outside the workspace")
    assert server.error_text("hover", {"file": "agent/missing.svh", "line": 1, "col": 1}) == "not indexed: agent/missing.svh"
    assert "lines, not 99" in server.error_text("hover", {"file": "agent/alu_driver.svh", "line": 99, "col": 1})
    fuzzy = server.data("symbols", {"query": "aludrv"})
    assert [(s["name"], s["kind"]) for s in fuzzy["items"]] == [("alu_driver", "class"), ("alu_error_driver", "class")]
    refs = server.data("references", at(tb, "agent/alu_agent.svh", "m_driver    = alu_driver"))
    assert refs["total"] == 3 and refs["items"][0]["line"] == 5
    clean(server)


# -- 8. sequences, includes, creates, units, macros, typedefs -------------------------------------


def test_sequences_includes_and_creates(server):
    seqs = server.data("sequences")
    [random] = [s for s in seqs["items"] if s["name"] == "alu_random_seq"]
    assert random["item"] == "alu_seq_item" and random["pSequencer"] is None
    [begin] = random["starts"]
    assert begin["sequence"] == "alu_seq_pkg::alu_random_seq" and begin["sequencerExpr"] == "m_env.m_agent.m_sequencer"
    assert (begin["owner"], begin["method"], begin["location"]["file"]) == ("alu_base_test", "run_phase", "tests/alu_base_test.svh")
    assert {s["item"] for s in seqs["items"]} == {"alu_seq_item"}
    # `uvm_do_with expands to a __seq.start(m_sequencer) on a local nothing resolves: reported, with no sequence.
    [macro] = [s for s in seqs["starts"] if s is not begin and s != begin]
    assert (macro["sequence"], macro["receiver"], macro["owner"], macro["method"]) == (None, "__seq", "alu_add_only_seq", "body")
    assert macro["location"]["file"] == "seq/alu_sequences.svh" and len(seqs["starts"]) == 2
    agent = server.data("includes", {"file": "agent/alu_agent.svh"})
    assert agent["roots"] == ["agent/alu_pkg.sv"] and agent["includes"] == []
    assert [(i["file"], i["location"]["file"]) for i in agent["includedBy"]] == [("agent/alu_pkg.sv", "agent/alu_pkg.sv")]
    graph = server.data("includes")
    assert graph["total"] == 20 and {i["file"] for i in graph["items"] if i["includes"]} >= {"agent/alu_pkg.sv", "tests/alu_test_pkg.sv"}
    [create] = server.data("creates", {"type": "alu_driver"})["items"]
    assert (create["type"], create["parent"], create["instName"], create["owner"], create["conditional"]) == \
        ("alu_pkg::alu_driver", "this", "m_driver", "alu_pkg::alu_agent", True)
    assert server.data("creates", {"file": "env/alu_env.svh"})["total"] == 3
    units = server.data("units", {"kind": "module"})
    assert [u["name"] for u in units["items"]] == ["alu", "tb_top"]
    assert [r["test"] for r in units["items"][1]["runTests"]] == ["alu_random_test"]
    assert server.data("macros")["total"] == 0 and server.data("macros", {"query": "uvm_info", "library": True})["total"] >= 1
    assert server.data("typedefs", {"query": "alu_sequencer"})["items"][0]["target"] == "uvm_sequencer#(alu_seq_item)"
    clean(server)


# -- 9. freshness -------------------------------------------------------------------------------


def test_edits_on_disk_reach_the_next_call(server, tb):
    def set_line():
        [row] = server.data("config_db", {"field": "alu_agent_config"})["items"]
        return row["sets"][0]["location"]["line"]

    env = read(tb, "env/alu_env.svh")
    before = set_line()
    assert before == 16
    try:
        write(tb, "env/alu_env.svh", env.replace("    uvm_config_db #(alu_agent_config)::set", "\n    uvm_config_db #(alu_agent_config)::set"))
        assert set_line() in (before, before + 1)  # within the refresh's second it may still be the old index
        time.sleep(1.1)
        assert set_line() == before + 1
        version = server.data("stats")["version"]
    finally:
        write(tb, "env/alu_env.svh", env)
    stats = server.data("reindex")
    assert stats["version"] > version and stats["classes"] == 19 and stats["sinceIndex"] < 1
    assert set_line() == before
    refused = server.request("resources/read", {"uri": "uvm://file//etc/hostname"})["error"]
    assert refused["message"] == "outside the workspace: /etc/hostname"
    text = server.read_resource("uvm://file/env/alu_env.svh")["contents"][0]
    assert text["mimeType"] == "text/plain" and text["text"] == env
    clean(server)


# -- 10. elaboration ------------------------------------------------------------------------------


def test_elaborate_runs_in_the_worker(server):
    cpu_before = server.data("stats")["cpuSeconds"]
    result = server.data("elaborate")
    cpu_after = server.data("stats")["cpuSeconds"]
    assert "error" not in result and result["tops"] == ["tb_top"] and result["counts"]["errors"] == 0
    assert result["diagnostics"] == [] and result["truncated"] is False and result["seconds"] > 0
    assert cpu_after - cpu_before < 0.1, "the compile ran in the MCP process"
    failed = server.data("elaborate", {"tops": ["no_such"]})
    assert failed["counts"]["errors"] == 1 and failed["tops"] == []
    assert any("no_such" in d["message"] for d in failed["diagnostics"])
    clean(server)


# -- 11. protocol ----------------------------------------------------------------------------------


def test_protocol_errors_notifications_and_ping_during_elaboration(server):
    assert server.request("no/such")["error"]["code"] == -32601
    assert server.request("tools/call", {"name": "no_such", "arguments": {}})["error"]["code"] == -32602
    assert server.request("tools/call", {"name": "classes", "arguments": []})["error"]["code"] == -32602
    server.send_raw("{not json")
    bad = server.read()
    assert bad["error"]["code"] == -32700 and bad["id"] is None
    assert server.error_text("definition", {"file": "agent/alu_agent.svh", "col": 3}) == "missing argument: line"
    assert server.error_text("definition", {"file": "agent/alu_agent.svh", "line": "3", "col": 3}) == \
        "argument line must be an integer"
    assert server.error_text("classes", {"colour": "red"}) == "unknown argument: colour"
    server.notify("notifications/cancelled", {"requestId": 1})
    server.notify("no/such")
    assert server.result("ping") == {} and server.others == []
    server.send({"jsonrpc": "2.0", "id": 900, "method": "tools/call", "params": {"name": "elaborate", "arguments": {}}})
    server.send({"jsonrpc": "2.0", "id": 901, "method": "ping"})
    first, second = server.read(), server.read()
    assert first["id"] == 901 and second["id"] == 900 and second["result"]["structuredContent"]["tops"] == ["tb_top"]
    clean(server)


# -- the review's findings ----------------------------------------------------------------------


def test_symlinks_cannot_reach_outside_the_workspace(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.sv").write_text("// SECRET\nclass secret; endclass\n")
    (outside / "secret.svh").write_text("// SECRET\nclass secret_header; endclass\n")
    root = tmp_path / "ws"
    root.mkdir()
    (root / "link.sv").symlink_to(outside / "secret.sv")  # a root of its own
    (root / "linked.svh").symlink_to(outside / "secret.svh")  # a header root.sv includes
    (root / "root.sv").write_text('`include "linked.svh"\nclass in_ws; endclass\n')
    c = serve(root)
    try:
        for uri in ("uvm://file/link.sv", "uvm://file/linked.svh", f"uvm://file/{outside / 'secret.sv'}",
                    "uvm://file/../outside/secret.sv"):
            reply = c.request("resources/read", {"uri": uri})
            assert reply["error"]["message"].startswith("outside the workspace"), uri
            assert "SECRET" not in json.dumps(reply)
        assert c.error_text("hover", {"file": "link.sv", "line": 2, "col": 8}).startswith("outside the workspace")
        assert c.error_text("includes", {"file": "linked.svh"}).startswith("outside the workspace")
        assert c.read_resource("uvm://file/root.sv")["contents"][0]["text"].startswith("`include")
        clean(c)
    finally:
        c.close()
    alias = tmp_path / "alias"  # a workspace root that is itself a link serves its files
    alias.symlink_to(root, target_is_directory=True)
    c = serve(alias)
    try:
        assert c.read_resource("uvm://file/root.sv")["contents"][0]["text"].startswith("`include")
        assert "in_ws" in names(c)
        clean(c)
    finally:
        c.close()


@pytest.mark.skipif(not os.path.exists("/bin/true"), reason="needs /bin/true")
def test_worker_failures_are_tool_errors(ws, bundled_ws, monkeypatch):
    server = mcp.McpServer(bundled_ws)

    def elaborate() -> str:
        reply = asyncio.run(server._call({"name": "elaborate", "arguments": {}}))
        assert reply["isError"] is True and "structuredContent" not in reply
        return reply["content"][0]["text"]

    monkeypatch.setattr(mcp, "ELAB_TIMEOUT", 0.05)
    assert elaborate() == "elaboration failed: it took longer than 0 s and was stopped"
    monkeypatch.setattr(mcp, "ELAB_TIMEOUT", 120.0)
    monkeypatch.setattr(sys, "executable", "/nonexistent/python")
    assert elaborate().startswith("elaboration failed: cannot start /nonexistent/python")
    monkeypatch.setattr(sys, "executable", "/bin/true")  # exits without a result
    assert elaborate() == "elaboration failed: the worker exited with status 0 without a result"
    skipped = asyncio.run(mcp.McpServer(ws)._call({"name": "elaborate", "arguments": {}}))
    assert skipped["isError"] and skipped["content"][0]["text"].startswith("elaboration skipped: the built-in UVM model")


def test_envelopes_batches_null_and_surrogates(server):
    seen = len(server.others)
    for rid in (0, "", "x"):
        server.send({"jsonrpc": "2.0", "id": rid, "method": "ping"})
        assert server.read() == {"jsonrpc": "2.0", "id": rid, "result": {}}
    for rid in (None, True, 1.2, [], {}):
        server.send({"jsonrpc": "2.0", "id": rid, "method": "ping"})
        reply = server.read()
        assert reply["error"]["code"] == -32600 and reply["id"] is None and "id must be" in reply["error"]["message"]
    for message, code in [({"jsonrpc": "1.0", "id": 102, "method": "ping", "params": 5}, -32600),
                          ({"jsonrpc": "2.0", "id": 103, "method": "ping", "params": 5}, -32600),
                          ({"jsonrpc": "2.0", "id": 104, "method": "tools/list", "params": [1]}, -32602),
                          ({"jsonrpc": "2.0", "id": 105}, -32600),
                          ({"jsonrpc": "2.0", "id": 106, "method": 7}, -32600)]:
        server.send(message)
        reply = server.read()
        assert (reply["id"], reply["error"]["code"]) == (message["id"], code), message
    server.send_raw("null")  # a message, not the end of the input
    reply = server.read()
    assert reply["error"]["code"] == -32600 and reply["id"] is None
    assert server.result("prompts/list") == {"prompts": []}
    server.send([{"jsonrpc": "2.0", "id": 200, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"},
                 {"jsonrpc": "2.0", "id": 201, "method": "prompts/list"}])
    assert server.read() == [{"jsonrpc": "2.0", "id": 200, "result": {}},
                             {"jsonrpc": "2.0", "id": 201, "result": {"prompts": []}}]
    server.send([])
    assert server.read()["error"] == {"code": -32600, "message": "an empty batch"}
    server.send([{"jsonrpc": "2.0", "method": "notifications/x"}])  # a batch of notifications: no reply at all
    server.send([[{"jsonrpc": "2.0", "id": 1, "method": "ping"}], 5])  # nested arrays are not batches
    [nested, scalar] = server.read()
    assert nested["error"]["code"] == scalar["error"]["code"] == -32600
    server.send_raw("[" * 1100 + "0" + "]" * 1100)
    deep = server.read()
    assert (deep if isinstance(deep, dict) else deep[0])["error"]["code"] in (-32700, -32600)
    server.send_raw('{"jsonrpc":"2.0","id":"\\ud800","method":"ping"}')
    assert server.read() == {"jsonrpc": "2.0", "id": "\ud800", "result": {}}
    assert server.error_text("class", {"name": "\ud800"}) == "no class \ud800"
    server.send({"jsonrpc": "2.0", "id": "\udc00", "method": "tools/call",
                 "params": {"name": "classes", "arguments": {"query": "\ud83d"}}})
    reply = server.read()
    assert reply["id"] == "\udc00" and reply["result"]["structuredContent"]["total"] == 0
    server.proc.stdin.write(b"\xff\n")
    server.proc.stdin.flush()
    assert server.read()["error"]["code"] == -32700
    assert server.result("ping") == {} and len(server.others) == seen
    clean(server)


def test_nested_filelists_and_new_files_refresh(tmp_path):
    root = tmp_path / "nested"
    root.mkdir()
    (root / "a.sv").write_text("class original_class; endclass\n")
    (root / "b.sv").write_text("class after; endclass\n")
    (root / "top.f").write_text("-f child.f\n")
    (root / "child.f").write_text("a.sv\n")
    c = serve(root, "-f", "top.f")
    try:
        assert names(c) == ["original_class"]
        (root / "child.f").write_text("b.sv\n")  # a filelist the filelist includes
        time.sleep(1.1)
        assert names(c) == ["after"]
        clean(c)
    finally:
        c.close()
    root = tmp_path / "discovery"
    (root / "empty").mkdir(parents=True)
    (root / "a.sv").write_text("class original_class; endclass\n")
    c = serve(root)
    try:
        assert names(c) == ["original_class"]
        (root / "empty" / "new.sv").write_text("class added; endclass\n")  # a directory with no file yet
        time.sleep(1.1)
        assert names(c) == ["added", "original_class"]
        (root / "deeper" / "more").mkdir(parents=True)  # a directory that did not exist
        (root / "deeper" / "more" / "x.sv").write_text("class deeper_class; endclass\n")
        time.sleep(1.1)
        assert names(c) == ["added", "deeper_class", "original_class"]
        clean(c)
    finally:
        c.close()
    root = tmp_path / "header"
    root.mkdir()
    (root / "root.sv").write_text('`include "later.svh"\nclass original_class; endclass\n')
    (root / "top.f").write_text("root.sv\n")
    c = serve(root, "-f", "top.f")
    try:
        assert names(c) == ["original_class"]
        (root / "later.svh").write_text("class added; endclass\n")  # a missing include appears
        time.sleep(1.1)
        assert names(c) == ["added", "original_class"]
        (root / "later.svh").unlink()
        time.sleep(1.1)
        assert names(c) == ["original_class"]
        (root / "later.svh").write_text("class recreated; endclass\n")  # and comes back
        time.sleep(1.1)
        assert names(c) == ["original_class", "recreated"]
        clean(c)
    finally:
        c.close()


def test_a_linked_workspace_folder_round_trips_its_own_locations(tmp_path):
    """slang names files by their real path, so a root that is a link (an automounted home, macOS
    /tmp) or a linked include directory must still accept the locations the server returns."""
    real = tmp_path / "real"
    (real / "tb").mkdir(parents=True)
    (real / "headers" / "sub").mkdir(parents=True)
    (real / "tb" / "top.sv").write_text('`include "hdr.svh"\n`include "sub/deep.svh"\n'
                                        'class in_ws extends hdr_class; endclass\n')
    (real / "headers" / "hdr.svh").write_text("class hdr_class; endclass\n")
    (real / "headers" / "sub" / "deep.svh").write_text("class deep_class; endclass\n")
    (real / "inc").symlink_to(real / "headers", target_is_directory=True)
    (real / "tb.f").write_text("+incdir+inc\ntb/top.sv\n")
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    c = serve(alias, "-f", "tb.f")
    try:
        classes = {i["name"]: i["location"] for i in c.data("classes")["items"]}
        assert set(classes) == {"deep_class", "hdr_class", "in_ws"}
        for name, loc in classes.items():  # every location is relative to the root, and hovers
            assert not os.path.isabs(loc["file"]), loc
            assert name in c.data("hover", {"file": loc["file"], "line": loc["line"], "col": loc["col"]})["markdown"]
        base = c.data("definition", {"file": "tb/top.sv", "line": 3, "col": 22})["locations"]
        assert base == [classes["hdr_class"]]
        graph = c.data("includes", {"file": "tb/top.sv"})
        assert {i["file"] for i in graph["includes"]} == {classes["hdr_class"]["file"], classes["deep_class"]["file"]}
        header = c.data("includes", {"file": classes["hdr_class"]["file"]})
        assert header["roots"] == ["tb/top.sv"] and [i["file"] for i in header["includedBy"]] == ["tb/top.sv"]
        for name in ("tb/top.sv", classes["hdr_class"]["file"], "inc/hdr.svh", "headers/sub/deep.svh",
                     str(alias / "inc" / "hdr.svh"), str(real / "headers" / "hdr.svh")):
            assert c.read_resource(f"uvm://file/{name}")["contents"][0]["text"].startswith(("`include", "class")), name
        assert c.data("includes", {"file": str(real / "headers" / "hdr.svh")})["file"] == classes["hdr_class"]["file"]
        outside = {"file": str(tmp_path / "x.svh"), "line": 1, "col": 1}
        assert c.error_text("hover", outside).startswith("outside the workspace")
        assert c.error_text("hover", {"file": "../x.svh", "line": 1, "col": 1}).startswith("outside the workspace")
        clean(c)
    finally:
        c.close()


def in_process(root, filelist: str):
    """A server over a workspace of its own in this process, so a test can count its reindexes."""
    ws = Workspace()
    ws.configure(ProjectConfig(workspace_roots=[str(root)], filelists=[str(root / filelist)], bundled_uvm=False))
    return mcp.McpServer(ws)


def counting(server: mcp.McpServer, monkeypatch) -> list[int]:
    """How many times reindex_all ran, kept in a one-element list."""
    count = [0]
    original = server.ws.reindex_all

    def reindex_all():
        count[0] += 1
        original()
    monkeypatch.setattr(server.ws, "reindex_all", reindex_all)
    return count


def class_names(server: mcp.McpServer) -> list[str]:
    return sorted(i["name"] for i in server.tool_classes({})["items"])


def test_an_atomic_save_or_a_swap_file_does_not_reindex_everything(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.sv").write_text("class a_class; endclass\n")
    (root / "src" / "b.sv").write_text("class b_class; endclass\n")
    (root / "tb.f").write_text("src/a.sv\nsrc/b.sv\n")
    server = in_process(root, "tb.f")
    count = counting(server, monkeypatch)
    version = server.ws.version
    a = root / "src" / "a.sv"
    tmp = root / "src" / "a.sv.tmp~"
    tmp.write_text("class a_class; endclass\nclass a_second; endclass\n")
    os.replace(tmp, a)  # an atomic save: a new inode, and the directory's stamp changes
    server.refresh(force=True)
    assert count == [0] and server.ws.version == version + 1
    assert class_names(server) == ["a_class", "a_second", "b_class"]
    swap = root / "src" / ".a.sv.swp"
    swap.write_text("vim")
    server.refresh(force=True)
    swap.unlink()
    (root / "src" / "run.log").write_text("log")  # a stray file that is not a source
    server.refresh(force=True)
    assert count == [0] and server.ws.version == version + 1
    (root / "src" / "c.sv").write_text("class c_class; endclass\n")  # unlisted, but it could be included
    server.refresh(force=True)
    assert count == [1] and class_names(server) == ["a_class", "a_second", "b_class"]
    (root / "src" / "b.sv").unlink()
    server.refresh(force=True)
    assert count == [2] and class_names(server) == ["a_class", "a_second"]
    (root / "tb.f").write_text("src/a.sv\nsrc/c.sv\n")
    server.refresh(force=True)
    assert count == [3] and class_names(server) == ["a_class", "a_second", "c_class"]


def test_a_header_in_a_new_subdirectory_of_an_include_dir_is_noticed(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    (root / "inc").mkdir(parents=True)
    (root / "top.sv").write_text('`include "sub/x.svh"\n`include "deep/er/y.svh"\nclass top_class; endclass\n')
    (root / "tb.f").write_text("+incdir+inc\ntop.sv\n")
    server = in_process(root, "tb.f")
    count = counting(server, monkeypatch)
    assert class_names(server) == ["top_class"]
    (root / "inc" / "sub").mkdir()  # created after the start, empty: watched from now on, no reindex
    server.refresh(force=True)
    assert count == [0] and str(root / "inc" / "sub") in server.stamps
    (root / "inc" / "sub" / "x.svh").write_text("class x_class; endclass\n")
    server.refresh(force=True)
    assert count == [1] and class_names(server) == ["top_class", "x_class"]
    (root / "inc" / "deep" / "er").mkdir(parents=True)  # a subtree created with its header before a refresh
    (root / "inc" / "deep" / "er" / "y.svh").write_text("class y_class; endclass\n")
    server.refresh(force=True)
    assert count == [2] and class_names(server) == ["top_class", "x_class", "y_class"]
    (root / "inc" / "sub" / "x.svh").write_text("class x_class; endclass\nclass x_more; endclass\n")  # edited in place
    server.refresh(force=True)
    assert count == [2] and class_names(server) == ["top_class", "x_class", "x_more", "y_class"]


def test_a_linked_root_source_navigates_its_members(tmp_path):
    """The index names a root by the path slang resolves, as it names the locations in it: through
    a linked workspace folder, a member's location the server returned must hover and resolve."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "top.sv").write_text("class probe_class;\n  int member;\n  function void action();\n"
                                 "    member = 1;\n  endfunction\nendclass\n")
    (real / "tb.f").write_text("top.sv\n")
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    c = serve(alias, "-f", str(alias / "tb.f"))  # by its alias: the server's cwd resolves a relative one
    try:
        cls = c.data("class", {"name": "probe_class"})
        [prop] = cls["properties"]
        [method] = cls["methods"]
        assert prop["location"]["file"] == method["location"]["file"] == "top.sv"
        for file in ("top.sv", str(alias / "top.sv"), str(real / "top.sv")):
            at = {"file": file, "line": prop["location"]["line"], "col": prop["location"]["col"]}
            assert "int member" in c.data("hover", at)["markdown"], file
            assert c.data("definition", at)["locations"] == [prop["location"]]
            use = {"file": file, "line": 4, "col": 5}  # `member = 1` inside action(): the enclosing class is known
            assert "int member" in c.data("hover", use)["markdown"], file
            assert c.data("definition", use)["locations"] == [prop["location"]]
            assert c.data("references", use)["total"] == 2
        at = {"file": "top.sv", "line": method["location"]["line"], "col": method["location"]["col"]}
        assert "function void action()" in c.data("hover", at)["markdown"]
        assert [i["file"] for i in c.data("includes")["items"]] == ["top.sv"]
        clean(c)
    finally:
        c.close()


def test_retargeting_an_included_link_is_noticed(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "one.svh").write_text("class old_target; endclass\n")
    (root / "two.svh").write_text("class new_target; endclass\n")
    (root / "selected.svh").symlink_to(root / "one.svh")
    (root / "top.sv").write_text('`include "selected.svh"\nclass top; endclass\n')
    (root / "tb.f").write_text("top.sv\n")
    server = in_process(root, "tb.f")
    count = counting(server, monkeypatch)
    assert class_names(server) == ["old_target", "top"]
    (root / "selected.tmp").symlink_to(root / "two.svh")
    os.replace(root / "selected.tmp", root / "selected.svh")  # the name and its stamp stay; the target changes
    server.refresh(force=True)
    assert count == [1] and class_names(server) == ["new_target", "top"]
    (root / "two.svh").write_text("class new_target; endclass\nclass more; endclass\n")  # the target edited in place
    server.refresh(force=True)
    assert count == [1] and class_names(server) == ["more", "new_target", "top"]


def test_a_missing_header_of_any_extension_is_noticed_when_it_appears(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    (root / "inc").mkdir(parents=True)
    (root / "top.sv").write_text('`include "generated.inc"\n`include "gen/other.inc"\nclass top; endclass\n')
    (root / "tb.f").write_text("+incdir+inc\ntop.sv\n")
    server = in_process(root, "tb.f")
    count = counting(server, monkeypatch)
    assert class_names(server) == ["top"]
    assert {os.path.basename(p) for p in server.stamps if server.stamps[p] is None} >= {"generated.inc", "other.inc"}
    (root / "generated.inc").write_text("class generated_header; endclass\n")  # next to the including file
    server.refresh(force=True)
    assert count == [1] and class_names(server) == ["generated_header", "top"]
    (root / "inc" / "gen").mkdir()
    (root / "inc" / "gen" / "other.inc").write_text("class other_header; endclass\n")  # in an include directory
    server.refresh(force=True)
    assert count == [2] and class_names(server) == ["generated_header", "other_header", "top"]
    (root / "generated.inc").write_text("class generated_header; endclass\nclass second; endclass\n")
    server.refresh(force=True)
    assert count == [2] and class_names(server) == ["generated_header", "other_header", "second", "top"]


def test_a_batch_of_saves_parses_each_root_once(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    headers = []
    for i in range(40):
        header = root / f"d{i:02d}" / "h.svh"
        header.parent.mkdir()
        header.write_text(f"class c{i}; endclass\n")
        headers.append(header)
    (root / "top.sv").write_text("".join(f'`include "{h.relative_to(root)}"\n' for h in headers))
    (root / "tb.f").write_text("top.sv\n")
    server = in_process(root, "tb.f")
    count = counting(server, monkeypatch)
    parses = []
    original = Indexer.parse
    monkeypatch.setattr(Indexer, "parse", lambda self, root, *a, **k: (parses.append(root), original(self, root, *a, **k))[1])
    for header in headers:
        tmp = header.with_suffix(".tmp")
        tmp.write_text(header.read_text().replace("; endclass", "_saved; endclass"))
        os.replace(tmp, header)
    server.refresh(force=True)
    assert count == [0] and parses == [str(root / "top.sv")]
    assert class_names(server) == sorted(f"c{i}_saved" for i in range(40))


def test_a_source_landing_right_after_a_new_directory_is_listed_is_indexed(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "top.sv").write_text("class top; endclass\n")
    ws = Workspace()
    ws.configure(ProjectConfig(workspace_roots=[str(root)], bundled_uvm=False))  # discovery
    server = mcp.McpServer(ws)
    new = root / "newdir"
    new.mkdir()
    original = server._listing

    def listing(directory):
        out = original(directory)
        if directory == str(new) and not (new / "h.sv").exists():
            (new / "h.sv").write_text("class arrived; endclass\n")  # lands right after the listing
        return out
    monkeypatch.setattr(server, "_listing", listing)
    server.refresh(force=True)
    server.refresh(force=True)  # the directory's recorded stamp predates the file: a change to notice
    assert class_names(server) == ["arrived", "top"]
    later = root / "later"
    later.mkdir()
    stamp = mcp._stamp

    def stamped(path):
        out = stamp(path)
        if path == str(later) and not (later / "h.sv").exists():
            (later / "h.sv").write_text("class between; endclass\n")  # lands between the stamp and the listing
        return out
    monkeypatch.setattr(mcp, "_stamp", stamped)
    server.refresh(force=True)  # in the listing: rebuilt at once
    assert class_names(server) == ["arrived", "between", "top"]


def test_retargeting_an_included_link_of_any_name_is_noticed(tmp_path, monkeypatch):
    """A link an `include names may have any extension and point outside the workspace: the
    listing tracks every link's target, and the real header it points at is read, not the link."""
    root, outside = tmp_path / "ws", tmp_path / "external"
    root.mkdir()
    outside.mkdir()
    (outside / "one").write_text("class old_target; endclass\n")
    (outside / "two").write_text("class new_target; endclass\n")
    (root / "top.sv").write_text('`include "selected.inc"\nclass top; endclass\n')
    (root / "tb.f").write_text("top.sv\n")
    (root / "selected.inc").symlink_to(outside / "one")
    server = in_process(root, "tb.f")
    count = counting(server, monkeypatch)
    assert class_names(server) == ["old_target", "top"]
    (root / "swap.tmp").symlink_to(outside / "two")
    os.replace(root / "swap.tmp", root / "selected.inc")
    server.refresh(force=True)
    assert count == [1] and class_names(server) == ["new_target", "top"]
    (outside / "two").write_text("class newer_target; endclass\n")
    server.refresh(force=True)
    assert count == [1] and class_names(server) == ["newer_target", "top"]
    with pytest.raises(mcp.ToolError, match="outside the workspace"):
        server.path_of(str(root / "selected.inc"))


def test_a_missing_header_in_an_include_dir_absent_at_startup_is_noticed(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    (root / "inc1").mkdir(parents=True)
    (root / "top.sv").write_text('`include "generated.inc"\nclass top; endclass\n')
    (root / "tb.f").write_text("+incdir+inc1+inc2\ntop.sv\n")  # inc2 does not exist yet
    server = in_process(root, "tb.f")
    count = counting(server, monkeypatch)
    assert server.ws.sources.absent_include_dirs == [str(root / "inc2")]
    assert server.stamps[str(root / "inc2")] is None and server.stamps[str(root / "inc2" / "generated.inc")] is None
    (root / "inc2").mkdir()
    server.refresh(force=True)
    assert count == [0] and class_names(server) == ["top"]  # created but still empty
    (root / "inc2" / "generated.inc").write_text("class generated; endclass\n")
    server.refresh(force=True)
    assert count == [1] and class_names(server) == ["generated", "top"]
    assert server.ws.sources.absent_include_dirs == []
    (root / "inc1" / "generated.inc").write_text("class shadowing; endclass\n")  # earlier in the search order
    server.refresh(force=True)
    assert count == [2] and class_names(server) == ["shadowing", "top"]


def linked_pair(tmp_path, files: dict) -> tuple[mcp.McpServer, mcp.McpServer]:
    """Two servers over one testbench: by its real folder and through a link to it."""
    real, alias = tmp_path / "real", tmp_path / "alias"
    real.mkdir()
    for name, text in files.items():
        (real / name).write_text(text)
    alias.symlink_to(real, target_is_directory=True)
    return in_process(real, "tb.f"), in_process(alias, "tb.f")


def test_signature_help_through_a_linked_root(tmp_path):
    """The call records are merged lazily from the roots that record for a file, by its key, and
    read by the real path a location names: a root given through a link needs both."""
    normal, linked = linked_pair(tmp_path, {
        "tb.f": "top.sv\n",
        "top.sv": "class c;\n function void f(int arg); endfunction\n function void g();\n  f(1);\n endfunction\nendclass\n"})
    at = {"file": "top.sv", "line": 4, "col": 5}
    expected = normal.tool_signature(at)
    assert expected["signature"]["label"] == "function void f(int arg)"
    assert linked.tool_signature(at) == expected
    assert linked.tool_signature({"file": str(tmp_path / "real" / "top.sv"), "line": 4, "col": 5}) == expected
    (tmp_path / "real" / "top.sv").write_text(
        "class c;\n function void f(int arg, int more); endfunction\n function void g();\n  f(1);\n endfunction\nendclass\n")
    for server in (normal, linked):
        server.refresh(force=True)
    assert linked.tool_signature(at) == normal.tool_signature(at)
    assert linked.tool_signature(at)["signature"]["formals"] == ["int arg", "int more"]


def test_the_complete_include_graph_through_a_linked_root(tmp_path):
    normal, linked = linked_pair(tmp_path, {
        "tb.f": "top.sv\n", "top.sv": '`include "h.svh"\nclass top; endclass\n', "h.svh": "class h; endclass\n"})
    expected, actual = normal.tool_includes({}), linked.tool_includes({})
    assert actual == expected and expected["total"] == 2
    [top] = [i for i in expected["items"] if i["file"] == "top.sv"]
    assert top["includes"] == ["h.svh"] and top["roots"] == ["top.sv"]
    assert linked.tool_includes({"file": "top.sv"})["includes"][0]["file"] == "h.svh"


def test_the_real_path_cache_holds_the_live_files_only(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "top.sv").write_text("class top; endclass\n")
    (root / "tb.f").write_text("top.sv\n")
    for i in range(20):
        (root / f"h{i}.inc").write_text(f"class h{i}; endclass\n")
    server = in_process(root, "tb.f")
    sources = server.ws.sources
    for i in range(20):
        (root / "top.sv").write_text(f'`include "h{i}.inc"\nclass top; endclass\n')
        server.refresh(force=True)
        server.path_of("top.sv")
        assert set(server._real_of) == server.ws.files() == {str(root / "top.sv"), str(root / f"h{i}.inc")}
    assert server.ws.sources is sources and len(server.ws.texts) == 2


def test_discovery_watches_an_include_dir_outside_the_roots(tmp_path, monkeypatch):
    root, first, last = tmp_path / "ws", tmp_path / "first", tmp_path / "last"
    root.mkdir()
    last.mkdir()
    (last / "generated.inc").write_text("class fallback; endclass\n")
    (root / "top.sv").write_text('`include "generated.inc"\nclass top; endclass\n')
    ws = Workspace()
    ws.configure(ProjectConfig(workspace_roots=[str(root)], include_dirs=[str(first), str(last)], bundled_uvm=False))
    server = mcp.McpServer(ws)
    count = counting(server, monkeypatch)
    assert class_names(server) == ["fallback", "top"]
    first.mkdir()  # absent at the start, earlier in the search order
    (first / "generated.inc").write_text("class preferred; endclass\n")
    server.refresh(force=True)
    assert count == [1] and class_names(server) == ["preferred", "top"]
    (first / "generated.inc").write_text("class preferred; endclass\nclass more; endclass\n")
    server.refresh(force=True)
    assert count == [1] and class_names(server) == ["more", "preferred", "top"]


def test_a_file_reached_by_its_real_path_and_through_a_link_keeps_both_roots_records(tmp_path):
    # alias.sv is a root through a link to shared.svh, which parent.sv also includes by its real
    # path with EXTRA defined: the call parent.sv's view records must survive the link's mapping.
    (tmp_path / 'alias.sv').symlink_to(tmp_path / 'shared.svh')
    (tmp_path / 'tb.f').write_text('alias.sv\nparent.sv\n')
    (tmp_path / 'parent.sv').write_text('`define EXTRA\n`include "shared.svh"\n')
    (tmp_path / 'shared.svh').write_text(
        'class c;\n function void f(int arg); endfunction\n function void g();\n'
        '`ifdef EXTRA\n  f(1);\n`else\n  f(2);\n`endif\n endfunction\nendclass\n')
    server = in_process(tmp_path, 'tb.f')
    found = server.tool_signature({'file': 'shared.svh', 'line': 5, 'col': 5})
    assert found['signature'] and found['signature']['label'] == 'function void f(int arg)'
