"""Inlay hints over stdio, including the selected test's instance paths."""

import shutil

import pytest

from conftest import EXAMPLE
from test_code_actions import Doc, start
from test_lsp import Client

CAPABILITIES = {"textDocument": {"inlayHint": {}}}


@pytest.fixture(scope="module")
def tb(tmp_path_factory):
    root = tmp_path_factory.mktemp("inlay-hints") / "alu_tb"
    shutil.copytree(EXAMPLE, root)
    path = root / "tests/alu_tests.svh"
    path.write_text(path.read_text().replace(
        "alu_random_seq::type_id::set_type_override",
        "set_type_override_by_type(alu_driver::get_type(), alu_error_driver::get_type());\n"
        "    alu_random_seq::type_id::set_type_override") + '''
class alu_instance_test extends alu_base_test;
  function void build_phase(uvm_phase phase);
    set_inst_override_by_type(alu_driver::get_type(), alu_error_driver::get_type(),
                             "m_env.m_agent.m_driver");
    super.build_phase(phase);
  endfunction
endclass
''')
    path = root / "env/alu_env.svh"
    path.write_text(path.read_text().replace("alu_agent      m_agent;", "alu_agent      m_agent;\n  alu_agent m_other;").replace(
        'm_agent = alu_agent::type_id::create("m_agent", this);',
        'm_agent = alu_agent::type_id::create("m_agent", this);\n'
        '    m_other = alu_agent::type_id::create("m_other", this);'))
    path = root / "tb/tb_top.sv"
    path.write_text(path.read_text().replace('run_test("alu_random_test")', 'run_test("alu_add_test")'))
    return root


def settings(root, **extra):
    return {"filelists": [str(root / "alu_tb.f")], "elaboration": "off", **extra}


def hints(client, path, start=(0, 0), end=(10000, 0)):
    return client.request("textDocument/inlayHint", {
        "textDocument": {"uri": path.as_uri()},
        "range": {"start": {"line": start[0], "character": start[1]},
                  "end": {"line": end[0], "character": end[1]}}})


def label(hint):
    value = hint["label"]
    return value if isinstance(value, str) else "".join(part["value"] for part in value)


@pytest.fixture(scope="module")
def client(tb):
    c = start(tb, capabilities=CAPABILITIES)
    yield c
    c.close()


def test_factory_type_override(client, tb):
    path = tb / "agent/alu_agent.svh"
    [hint] = hints(client, path)
    assert label(hint) == " → alu_error_driver"
    assert hint["kind"] == 1 and hint["paddingLeft"] is True
    lines = path.read_text().splitlines()
    line = next(i for i, text in enumerate(lines) if 'create("m_driver"' in text)
    assert hint["position"] == {"line": line, "character": lines[line].index(");") + 1}
    target = tb / "tests/alu_tests.svh"
    override_line = next(i for i, text in enumerate(target.read_text().splitlines())
                         if "set_type_override_by_type" in text)
    assert hint["tooltip"] == {"kind": "markdown", "value":
        f"overridden in alu_add_test::build_phase (alu_tests.svh:{override_line + 1})"}
    [part] = hint["label"]
    assert part["location"]["uri"] == target.as_uri()
    assert part["location"]["range"]["start"] == {"line": override_line, "character": 4}
    tree = client.request("uvm/topology", {})["root"]
    drivers = [child for agent in tree["children"][0]["children"] for child in agent["children"]
               if child["name"] == "m_driver"]
    assert len(drivers) == 2
    assert all(d["type"] == "alu_error_driver" and d["overriddenFrom"] == "alu_driver" for d in drivers)
    assert all(d["createdAt"]["line"] == line for d in drivers)


def test_factory_object_override(client, tb):
    found = hints(client, tb / "tests/alu_base_test.svh")
    assert [label(h) for h in found] == [" → alu_add_only_seq"]


def test_factory_range_and_no_creates(client, tb):
    path = tb / "agent/alu_agent.svh"
    [hint] = hints(client, path)
    line, col = hint["position"]["line"], hint["position"]["character"]
    assert hints(client, path, end=(line, 0)) == []
    assert hints(client, path, start=(line + 1, 0)) == []
    assert hints(client, path, end=(line, col - 1)) == []
    assert hints(client, path, start=(line, 0), end=(line, col + 1)) == [hint]
    assert hints(client, tb / "agent/alu_seq_item.svh") == []


@pytest.mark.parametrize("test,enabled,expected", [
    ("alu_instance_test", True, [" → alu_error_driver (1 of 2 instances)"]),
    ("alu_random_test", True, []), ("unknown_test", True, []),
    ("alu_add_test", False, []),
])
def test_factory_selected_test_and_opt_out(tb, test, enabled, expected):
    c = start(tb, capabilities=CAPABILITIES, settings=settings(tb, **{
        "inlayHints": enabled, "inlayHints.test": test}))
    try:
        assert [label(h) for h in hints(c, tb / "agent/alu_agent.svh")] == expected
    finally:
        c.close()


@pytest.mark.parametrize("capabilities,advertised", [(CAPABILITIES, True), ({}, False)])
def test_inlay_capability(tb, capabilities, advertised):
    c = Client()
    try:
        result = c.request("initialize", {"processId": None, "rootUri": tb.as_uri(),
            "capabilities": capabilities, "initializationOptions": {"settings": settings(tb)}})
        assert result["capabilities"].get("inlayHintProvider") == ({"resolveProvider": False} if advertised else None)
    finally:
        c.close()


def test_factory_multiline_utf16_and_cache_invalidation(tb):
    c = start(tb, capabilities=CAPABILITIES)
    try:
        path = tb / "agent/alu_agent.svh"
        doc = Doc(c, str(path))
        c.wait_for("uvm/indexChanged")
        [before] = hints(c, path)
        doc.change(doc.text.replace('alu_driver::type_id::create("m_driver", this)',
                                   'alu_driver::type_id::create(\n        "m_driver😀", this)'))
        c.wait_for("uvm/indexChanged")
        [after] = hints(c, path)
        assert after["position"] == {"line": before["position"]["line"] + 1, "character": 27}
        override = Doc(c, str(tb / "tests/alu_tests.svh"))
        c.wait_for("uvm/indexChanged")
        override.change(override.text.replace("alu_error_driver::get_type()", "alu_driver::get_type()"))
        c.wait_for("uvm/indexChanged")
        assert hints(c, path) == []
    finally:
        c.close()
