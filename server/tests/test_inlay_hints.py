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


@pytest.mark.parametrize("before", [True, False])
def test_object_creates_do_not_replace_components(tb, tmp_path, before):
    root = tmp_path / "alu_tb"
    shutil.copytree(tb, root)
    test = root / "tests/alu_tests.svh"
    test.write_text(test.read_text().replace("    alu_random_seq::type_id::set_type_override",
        "    alu_seq_item::type_id::set_type_override(hint_item::get_type());\n"
        "    alu_random_seq::type_id::set_type_override"))
    path = root / "agent/alu_agent.svh"
    methods = '''
  function void first_sequence();
    alu_seq_item m_driver;
    m_driver = alu_seq_item::type_id::create("seq");
  endfunction
  function void second_sequence();
    alu_seq_item m_driver;
    m_driver = alu_seq_item::type_id::create("seq");
  endfunction
'''
    text = path.read_text()
    text = text.replace("  function new", methods + "  function new") if before else text.replace(
        "endclass", methods + "endclass")
    text = '''class hint_item extends alu_seq_item;
  `uvm_object_utils(hint_item)
  function new(string name = "hint_item"); super.new(name); endfunction
endclass
''' + text
    path.write_text(text)
    c = start(root, capabilities=CAPABILITIES)
    try:
        tree = c.request("uvm/topology", {})["root"]
        for agent in tree["children"][0]["children"]:
            if agent["type"] != "alu_agent":
                continue
            assert {child["name"] for child in agent["children"]} == {"m_driver", "m_monitor", "m_sequencer"}
            [driver] = [child for child in agent["children"] if child["name"] == "m_driver"]
            assert driver["type"] == "alu_error_driver" and driver["overriddenFrom"] == "alu_driver"
        found = hints(c, path)
        expected = [" → hint_item"] * 2
        expected.insert(2 if before else 0, " → alu_error_driver")
        assert [label(h) for h in found] == expected
        lines = text.splitlines()
        assert [h["position"]["line"] for h in found] == [
            i for i, line in enumerate(lines) if "m_driver = alu_seq_item" in line or "m_driver    =" in line]
    finally:
        c.close()


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


@pytest.mark.parametrize("source", ["agent/alu_driver.svh", "agent/alu_agent.svh", "tests/alu_tests.svh"])
def test_inlay_request_immediately_after_edit(tb, source):
    c = start(tb, capabilities=CAPABILITIES)
    try:
        path = tb / ("agent/alu_agent.svh" if source.startswith("tests/") else source)
        doc = Doc(c, str(tb / source))
        c.wait_for("uvm/indexChanged")
        before = hints(c, path)
        assert before
        doc.change("\n\n" + doc.text)
        after = hints(c, path)
        assert [label(h) for h in after] == [label(h) for h in before]
        if source.startswith("tests/"):
            assert [h["position"] for h in after] == [h["position"] for h in before]
            old_range = before[0]["label"][0]["location"]["range"]
            new_range = after[0]["label"][0]["location"]["range"]
            assert new_range == {key: {**pos, "line": pos["line"] + 2} for key, pos in old_range.items()}
        else:
            assert [h["position"] for h in after] == [
                {**h["position"], "line": h["position"]["line"] + 2} for h in before]
            line = before[0]["position"]["line"]
            assert hints(c, path, start=(line, 0), end=(line + 1, 0)) == []
    finally:
        c.close()


@pytest.mark.parametrize("bundled", [True, False])
def test_bound_driver_members(tb, bundled):
    c = start(tb, capabilities=CAPABILITIES, settings=settings(tb, useBundledUvm=bundled))
    try:
        path = tb / "agent/alu_driver.svh"
        found = hints(c, path)
        assert [label(h) for h in found] == [": alu_seq_item", ": alu_seq_item"]
        assert [h["position"] for h in found] == [
            {"line": 25, "character": 35}, {"line": 26, "character": 18}]
        assert all(h["kind"] == 1 and h["paddingLeft"] for h in found)
        assert all(h["tooltip"] == {"kind": "markdown", "value":
            "REQ bound in alu_driver extends uvm_driver #(alu_seq_item)"} for h in found)
        assert hints(c, path, start=(26, 0), end=(27, 0)) == found[1:]
        assert hints(c, path, end=(25, 35)) == []
        assert hints(c, path, start=(27, 0)) == []
        c.notify("workspace/didChangeConfiguration", {"settings": {"uvm": settings(tb, inlayHints=False)}})
        c.wait_for("uvm/indexChanged")
        assert hints(c, path) == []
    finally:
        c.close()


MEMBERS = '''
class hint_base #(type T = uvm_sequence_item);
  T item;
  T second;
  int plain;
  function void own(); item = item; endfunction
endclass
class hint_mid #(type U = uvm_sequence_item) extends hint_base #(U);
endclass
class hint_bound extends hint_mid #(alu_seq_item);
  hint_base #(alu_seq_item) other;
  function void use_members();
    item = item; second = second;
    this.item = this.item;
    super.item = item;
    plain = 1;
    other.item = item;
    $display("item req rsp");
    // item second req rsp
  endfunction
  function void shadow(alu_seq_item item);
    item = item;
    this.item = item;
  endfunction
endclass
class hint_unbound #(type T = uvm_sequence_item) extends hint_base #(T);
  function void use_item(); item = item; endfunction
endclass
class hint_fixed #(type T = uvm_sequence_item) extends hint_base #(alu_seq_item);
  function void use_item(); item = item; endfunction
endclass
class hint_int extends hint_base #(.T(int));
  function void use_item(); item = item; endfunction
endclass
class hint_driver extends uvm_driver #(alu_seq_item);
  function void use_members();
    req = req; rsp = rsp;
    seq_item_port.item_done();
  endfunction
endclass
class hint_shadows extends hint_base #(alu_seq_item);
  alu_seq_item item;
  function void use_item(); item = item; endfunction
endclass
'''


@pytest.fixture(scope="module")
def members_tb(tmp_path_factory, tb):
    root = tmp_path_factory.mktemp("bound-members") / "alu_tb"
    shutil.copytree(tb, root)
    path = root / "agent/alu_driver.svh"
    path.write_text(path.read_text() + MEMBERS)
    return root


def test_bound_user_members_and_shadowing(members_tb):
    c = start(members_tb, capabilities=CAPABILITIES)
    try:
        path = members_tb / "agent/alu_driver.svh"
        lines = path.read_text().splitlines()
        start_line = lines.index("class hint_base #(type T = uvm_sequence_item);")
        found = hints(c, path, start=(start_line, 0))
        expected = []
        owner = ""
        for i, line in enumerate(lines):
            if line.startswith("class "):
                owner = line.split()[1]
            names = []
            if owner == "hint_bound":
                if line == "    item = item; second = second;":
                    names = ["item", "second"]
                elif line in ("    this.item = this.item;", "    super.item = item;", "    this.item = item;"):
                    names = ["item"]
                elif line == "    other.item = item;":
                    expected.append((i, line.rindex("item") + 4, ": alu_seq_item"))
            elif owner in ("hint_fixed", "hint_int") and "function void use_item" in line:
                names = ["item"]
            elif owner == "hint_driver" and "req = req" in line:
                names = ["req", "rsp"]
            for name in names:
                col = line.index(name, line.index(";") + 1) if "function void use_item" in line else line.index(name)
                expected.append((i, col + len(name), ": int" if owner == "hint_int" else ": alu_seq_item"))
        assert [(h["position"]["line"], h["position"]["character"], label(h)) for h in found] == expected
        assert len(found) == 10
        assert found[0]["tooltip"]["value"] == "T bound in hint_bound extends hint_mid #(alu_seq_item)"
        assert found[-1]["tooltip"]["value"] == "RSP bound in hint_driver extends uvm_driver #(alu_seq_item)"
    finally:
        c.close()


@pytest.mark.parametrize("bundled", [True, False])
def test_named_arguments_only_hint_actual_members(tb, tmp_path, bundled):
    root = tmp_path / "alu_tb"
    shutil.copytree(tb, root)
    path = root / "agent/alu_driver.svh"
    text = path.read_text() + '''
class hint_arguments extends uvm_driver #(alu_seq_item);
  function void consume(alu_seq_item req = null, alu_seq_item rsp = null); endfunction
  function void use_members();
    consume(.req(null));
    consume(.req(req));
    consume(.req(this.req), .rsp(rsp));
    consume(. /* formal */ req(req));
    consume(.
      req(null));
    consume(.
      req(req));
  endfunction
endclass
'''
    path.write_text(text)
    c = start(root, capabilities=CAPABILITIES, settings=settings(root, useBundledUvm=bundled))
    try:
        lines = text.splitlines()
        first = lines.index("class hint_arguments extends uvm_driver #(alu_seq_item);")
        found = hints(c, path, start=(first, 0))
        expected = []
        for line, source in enumerate(lines[first:], first):
            if source in ("    consume(.req(req));", "    consume(. /* formal */ req(req));", "      req(req));"):
                expected.append({"line": line, "character": source.rindex("req") + 3})
            elif source == "    consume(.req(this.req), .rsp(rsp));":
                expected.extend({"line": line, "character": source.rindex(name) + 3} for name in ("req", "rsp"))
        assert [h["position"] for h in found] == expected
        assert [label(h) for h in found] == [": alu_seq_item"] * 5
        for line, source in enumerate(lines):
            if "null));" in source:
                assert hints(c, path, start=(line, 0), end=(line + 1, 0)) == []
    finally:
        c.close()


def test_bound_member_unicode_and_edits(tb):
    c = start(tb, capabilities=CAPABILITIES)
    try:
        path = tb / "agent/alu_driver.svh"
        doc = Doc(c, str(path))
        c.wait_for("uvm/indexChanged")
        assert len(hints(c, path)) == 2
        doc.change(doc.text.replace("    drive_item(req);", '    /* 😀 */ drive_item(req); req = req;').replace("\n", "\r\n"))
        c.wait_for("uvm/indexChanged")
        found = hints(c, path)
        assert len(found) == 2
        assert found[1]["position"] == {"line": 26, "character": 27}
        doc.change(doc.text.replace("uvm_driver #(alu_seq_item)", "uvm_driver #(alu_agent_config)"))
        c.wait_for("uvm/indexChanged")
        assert [label(h) for h in hints(c, path)] == [": alu_agent_config", ": alu_agent_config"]
    finally:
        c.close()


@pytest.mark.parametrize("argument,expected", [
    ("box #(U)", "box #(alu_seq_item)"),
    ("box #(.U(U))", "box #(.U(alu_seq_item))"),
    ("box #(box #(U))", "box #(box #(alu_seq_item))"),
])
def test_compound_bindings_through_intermediate_classes(tb, tmp_path, argument, expected):
    root = tmp_path / "alu_tb"
    shutil.copytree(tb, root)
    path = root / "agent/alu_driver.svh"
    text = path.read_text() + '''
class box #(type U = int); endclass
class chain_base #(type T = int);
  T item;
endclass
class chain_mid #(type U = int) extends chain_base #(ARGUMENT);
endclass
class chain_bound extends chain_mid #(alu_seq_item);
  function void use_item();
    item = null;
  endfunction
endclass
class chain_open #(type A = alu_seq_item) extends chain_mid #(A);
  function void use_item(); item = null; endfunction
endclass
'''.replace("ARGUMENT", argument)
    path.write_text(text)
    c = start(root, capabilities=CAPABILITIES)
    try:
        lines = text.splitlines()
        first = lines.index("class chain_bound extends chain_mid #(alu_seq_item);")
        [hint] = hints(c, path, start=(first, 0))
        assert label(hint) == ": " + expected
        assert hint["position"] == {"line": first + 2, "character": 8}
        assert hint["tooltip"]["value"] == "T bound in chain_bound extends chain_mid #(alu_seq_item)"
    finally:
        c.close()


@pytest.mark.parametrize("refresh", [True, False])
def test_inlay_refresh_after_configuration_change(tb, refresh):
    c = Client()
    requests = []
    read = c.read

    def record(timeout=30):
        msg = read(timeout)
        if "id" in msg and "method" in msg:
            requests.append(msg["method"])
        return msg

    c.read = record
    try:
        c.request("initialize", {"processId": None, "rootUri": tb.as_uri(), "capabilities": {
            **CAPABILITIES, "workspace": {"inlayHint": {"refreshSupport": refresh}}},
            "initializationOptions": {"settings": settings(tb)}})
        c.notify("initialized", {})
        c.wait_for("uvm/indexChanged")
        assert hints(c, tb / "agent/alu_driver.svh")
        assert requests.count("workspace/inlayHint/refresh") == int(refresh)
        c.notify("workspace/didChangeConfiguration", {"settings": {"uvm": settings(tb, inlayHints=False)}})
        c.wait_for("uvm/indexChanged")
        assert hints(c, tb / "agent/alu_driver.svh") == []
        assert requests.count("workspace/inlayHint/refresh") == 2 * int(refresh)
    finally:
        c.close()
