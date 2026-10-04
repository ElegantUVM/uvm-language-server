"""textDocument/codeAction over stdio, as VS Code asks for it."""

import os
import re
import shutil

import pytest

from conftest import EXAMPLE
from test_lsp import Client

CAPABILITIES = {
    "workspace": {"workspaceEdit": {"documentChanges": True}},
    "textDocument": {
        "codeAction": {"dataSupport": True, "disabledSupport": True, "isPreferredSupport": True,
                       "codeActionLiteralSupport": {"codeActionKind": {"valueSet": [
                           "", "quickfix", "refactor", "source", "source.fixAll"]}}},
        "publishDiagnostics": {"dataSupport": True},
    },
}


def start(root, capabilities=CAPABILITIES, **options) -> Client:
    c = Client()
    c.request("initialize", {
        "processId": None, "rootUri": f"file://{root}", "capabilities": capabilities,
        "workspaceFolders": [{"uri": f"file://{root}", "name": "tb"}],
        "initializationOptions": {"settings": {"filelists": [os.path.join(root, "alu_tb.f")],
                                               "elaboration": "off"}, **options}})
    c.notify("initialized", {})
    c.wait_for("uvm/indexChanged")
    return c


@pytest.fixture(scope="module")
def tb(tmp_path_factory):
    root = tmp_path_factory.mktemp("cab") / "alu_tb"
    shutil.copytree(EXAMPLE, root)
    return str(root)


@pytest.fixture(scope="module")
def client(tb):
    c = start(tb)
    yield c
    c.close()


class Doc:
    """An open document: its text and version as the editor has them."""

    def __init__(self, client: Client, path: str, text: str | None = None):
        self.client, self.path, self.uri = client, path, f"file://{path}"
        with open(path, newline="") as f:
            self.text = f.read() if text is None else text
        self.version = 1
        client.forget("textDocument/publishDiagnostics")  # only what this document's text gets
        client.notify("textDocument/didOpen", {"textDocument": {
            "uri": self.uri, "languageId": "systemverilog", "version": 1, "text": self.text}})

    def change(self, text: str):
        self.version += 1
        self.text = text
        self.client.forget("textDocument/publishDiagnostics")
        self.client.notify("textDocument/didChange", {"textDocument": {"uri": self.uri, "version": self.version},
                                                      "contentChanges": [{"text": text}]})

    def close(self):
        self.client.notify("textDocument/didClose", {"textDocument": {"uri": self.uri}})

    def diagnostics(self, pred=lambda ds: True, timeout=20):
        """The next diagnostics published for this document that satisfy pred."""
        msg = self.client.wait_for("textDocument/publishDiagnostics",
                                   lambda m: m["params"]["uri"] == self.uri and pred(m["params"]["diagnostics"]),
                                   timeout=timeout)
        return msg["params"]["diagnostics"]

    def actions(self, diagnostics, only=None, trigger=1):
        rng = diagnostics[0]["range"] if diagnostics else {"start": {"line": 0, "character": 0},
                                                            "end": {"line": 0, "character": 0}}
        context = {"diagnostics": diagnostics, "triggerKind": trigger}
        if only is not None:
            context["only"] = only
        return self.client.request("textDocument/codeAction", {
            "textDocument": {"uri": self.uri}, "range": rng, "context": context}) or []


def utf16_offset(text: str, line: int, character: int) -> int:
    starts = [0] + [m.end() for m in re.finditer(r"\r\n|\r|\n", text)]
    pos, units = starts[line], 0
    while units < character:
        units += 2 if ord(text[pos]) > 0xFFFF else 1
        pos += 1
    return pos


def applied(text: str, edits: list) -> str:
    for e in sorted(edits, key=lambda e: (e["range"]["start"]["line"], e["range"]["start"]["character"]),
                    reverse=True):
        s, t = e["range"]["start"], e["range"]["end"]
        a, b = utf16_offset(text, s["line"], s["character"]), utf16_offset(text, t["line"], t["character"])
        text = text[:a] + e["newText"] + text[b:]
    return text


def edits_for(action, uri, version=None) -> list:
    """The edits an action makes to one document, checking the version it was made for."""
    edit = action["edit"]
    if "documentChanges" in edit:
        [dc] = [dc for dc in edit["documentChanges"] if dc["textDocument"]["uri"] == uri]
        assert dc["textDocument"]["version"] == version
        return dc["edits"]
    return edit["changes"][uri]


def test_capabilities(tb):
    c = Client()
    try:
        r = c.request("initialize", {"processId": None, "rootUri": f"file://{tb}", "capabilities": CAPABILITIES})
        assert r["capabilities"]["codeActionProvider"] == {"codeActionKinds": ["quickfix", "source.fixAll.uvm"],
                                                           "resolveProvider": False}
    finally:
        c.close()


def agent(tb):
    return os.path.join(tb, "agent", "alu_agent.svh")


def test_round_trip(client, tb):
    doc = Doc(client, agent(tb))
    original = doc.text
    doc.change(original.replace('create("m_driver", this)', 'create("m_driver")'))
    [d] = doc.diagnostics(lambda ds: ds)
    assert d["code"] == "create-no-parent" and d["data"]["cls"] == "alu_pkg::alu_agent"
    [a] = doc.actions([d])
    assert (a["title"], a["kind"], a["isPreferred"], a["diagnostics"]) == ("Pass 'this' as the parent", "quickfix", True, [d])
    doc.change(applied(doc.text, edits_for(a, doc.uri, doc.version)))
    assert doc.text == original
    doc.diagnostics(lambda ds: not ds)
    assert doc.actions([d]) == []  # fixed: the old diagnostic gets nothing
    doc.close()


def test_a_stale_diagnostic_is_fixed_where_its_finding_is_now(client, tb):
    doc = Doc(client, agent(tb))
    original = doc.text
    doc.change(original.replace('create("m_driver", this)', 'create("m_driver")'))
    [d] = doc.diagnostics(lambda ds: ds)
    # Three lines above it, then at once a request with the diagnostic the editor still has.
    doc.change("// one\n// two\n// three\n" + doc.text)
    [a] = doc.actions([d])
    edits = edits_for(a, doc.uri, doc.version)  # made for the new version
    assert edits[0]["range"]["start"]["line"] == d["range"]["start"]["line"] + 3
    assert applied(doc.text, edits) == "// one\n// two\n// three\n" + original
    # Fixed by hand meanwhile: the same old diagnostic gets nothing.
    doc.change(original)
    assert doc.actions([d]) == []
    doc.close()


def test_an_automatic_request_right_after_an_edit_waits_for_the_index(client, tb):
    doc = Doc(client, agent(tb))
    original = doc.text
    doc.change(original.replace('create("m_driver", this)', 'create("m_driver")'))
    [d] = doc.diagnostics(lambda ds: ds)
    # The lightbulb's request (Automatic) can come before the edit is indexed (250 ms later): it
    # waits for it, instead of answering from where the call was before the edit.
    doc.change("\n\n" + doc.text)
    [a] = doc.actions([d], trigger=2)
    assert applied(doc.text, edits_for(a, doc.uri, doc.version)) == "\n\n" + original
    doc.diagnostics(lambda ds: ds and ds[0]["range"]["start"]["line"] == d["range"]["start"]["line"] + 2)
    doc.change(original)
    doc.close()


def test_other_sources_get_nothing(client, tb):
    doc = Doc(client, agent(tb))
    foreign = {"range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 5}},
               "message": "something", "severity": 2, "code": "create-no-parent", "source": "verible"}
    assert doc.actions([foreign]) == []
    doc.close()


def test_edits_count_utf16_units(client, tb):
    doc = Doc(client, agent(tb))
    original = doc.text
    emoji = original.replace('m_driver    = alu_driver::type_id::create("m_driver", this);',
                             'm_driver    = alu_driver::type_id::create(/*😀é*/"m_driver", this);')
    doc.change(emoji.replace('"m_driver", this)', '"m_driver")'))
    [d] = doc.diagnostics(lambda ds: ds)
    [a] = doc.actions([d])
    [e] = edits_for(a, doc.uri, doc.version)
    line = doc.text.split("\n")[e["range"]["start"]["line"]]
    assert e["range"]["start"]["character"] == len(line[:line.index('"m_driver")') + 10].encode("utf-16-le")) // 2
    assert applied(doc.text, [e]) == emoji
    doc.change(original)
    doc.close()


def test_plain_changes_without_document_changes(tb):
    c = start(tb, capabilities={"textDocument": {"publishDiagnostics": {"dataSupport": True}}})
    try:
        doc = Doc(c, agent(tb))
        original = doc.text
        doc.change(original.replace('create("m_driver", this)', 'create("m_driver")'))
        [d] = doc.diagnostics(lambda ds: ds)
        [a] = doc.actions([d])
        assert "documentChanges" not in a["edit"]
        assert applied(doc.text, edits_for(a, doc.uri)) == original
        assert "disabled" not in a and a["isPreferred"]
    finally:
        c.close()


def test_a_client_without_data_support(tb):
    c = start(tb, capabilities={})
    try:
        doc = Doc(c, agent(tb))
        original = doc.text
        doc.change(original.replace('create("m_driver", this)', 'create("m_driver")'))
        [d] = doc.diagnostics(lambda ds: ds)
        d.pop("data")  # such a client sends it back without
        [a] = doc.actions([d])
        assert applied(doc.text, edits_for(a, doc.uri)) == original
    finally:
        c.close()


def published(client, path, pred=lambda ds: True, timeout=20):
    return client.wait_for("textDocument/publishDiagnostics",
                           lambda m: m["params"]["uri"] == f"file://{path}" and pred(m["params"]["diagnostics"]),
                           timeout=timeout)["params"]["diagnostics"]


def test_a_fix_in_another_file(client, tb):
    """The finding is on the create() call in alu_env.svh; the fix registers alu_scoreboard in its
    own file, open in the editor (versioned) or not (no version)."""
    sb_path, env_path = os.path.join(tb, "env", "alu_scoreboard.svh"), os.path.join(tb, "env", "alu_env.svh")
    with open(sb_path) as f:
        original = f.read()
    planted = original.replace("  `uvm_component_utils(alu_scoreboard)\n", "")
    sb = Doc(client, sb_path, planted)
    env = Doc(client, env_path)
    [d] = published(client, env_path, lambda ds: ds)
    assert d["code"] == "create-unregistered"
    [a] = env.actions([d])
    assert a["title"] == "Add `uvm_component_utils(alu_scoreboard) to class 'alu_scoreboard' (alu_scoreboard.svh)"
    assert applied(planted, edits_for(a, sb.uri, sb.version)) == original
    sb.close()
    env.close()
    published(client, env_path, lambda ds: not ds)
    # Not open: the file on disk, and no version to check.
    with open(sb_path, "w") as f:
        f.write(planted)
    try:
        client.notify("workspace/didChangeWatchedFiles", {"changes": [{"uri": sb.uri, "type": 2}]})
        env = Doc(client, env_path)
        [d] = published(client, env_path, lambda ds: ds)
        [a] = env.actions([d])
        assert applied(planted, edits_for(a, sb.uri, None)) == original
        env.close()
    finally:
        with open(sb_path, "w") as f:
            f.write(original)
        client.notify("workspace/didChangeWatchedFiles", {"changes": [{"uri": sb.uri, "type": 2}]})
    published(client, env_path, lambda ds: not ds)


def test_fix_all(client, tb):
    doc = Doc(client, agent(tb))
    original = doc.text
    doc.change(original.replace('create("m_driver", this)', 'create("m_driver")')
               .replace('create("m_sequencer", this)', 'create("m_sequencer")'))
    ds = doc.diagnostics(lambda ds: len(ds) == 2)
    acts = doc.actions(ds[:1])  # the lightbulb on one of them; never the source action unasked
    assert [(a["title"], a["kind"], a.get("isPreferred")) for a in acts] == [
        ("Pass 'this' as the parent", "quickfix", True),
        ("Fix all 2 'create-no-parent' problems in this file", "quickfix", None)]
    assert len(acts[1]["diagnostics"]) == 2
    assert applied(doc.text, edits_for(acts[1], doc.uri, doc.version)) == original
    # Source Action / codeActionsOnSave ask by kind, and get only that.
    for only in (["source.fixAll"], ["source"], ["source.fixAll.uvm"]):
        [a] = doc.actions([], only=only)
        assert (a["title"], a["kind"]) == ("Fix all auto-fixable UVM problems", "source.fixAll.uvm")
        assert applied(doc.text, edits_for(a, doc.uri, doc.version)) == original
    assert doc.actions(ds[:1], only=["quickfix"])[-1]["kind"] == "quickfix"
    doc.change(original)
    assert doc.actions([], only=["source.fixAll"]) == []  # nothing to fix: no action
    doc.close()


def test_commands_only_for_a_client_that_has_them(client, tb):
    mon = os.path.join(tb, "agent", "alu_monitor.svh")
    doc = Doc(client, mon)
    doc.change("\n" + doc.text)  # moves the planted finding, so it is published again
    [d] = doc.diagnostics(lambda ds: ds)
    assert d["code"] == "config-db-no-set" and d["severity"] == 3  # optional get: a note
    assert [a["title"] for a in doc.actions([d])] == []
    doc.close()
    c = start(tb, clientCommands=["uvm.showConfigDb"])
    try:
        doc = Doc(c, mon)
        doc.change("\n" + doc.text)
        [d] = doc.diagnostics(lambda ds: ds)
        [a] = doc.actions([d])
        assert (a["title"], a["command"]["command"], a["command"]["arguments"]) == (
            "Show 'checks_enable' in the config_db cross-reference", "uvm.showConfigDb", [{"field": "checks_enable"}])
        assert "isPreferred" not in a and "edit" not in a
    finally:
        c.close()


def test_missing_include(tb):
    # slang's diagnostic: the setting, through the editor's command, or the filelist, a file not open.
    inc = os.path.join(tb, "inc")
    os.makedirs(os.path.join(inc, "sub"))
    with open(os.path.join(inc, "sub", "hdr.svh"), "w") as f:
        f.write("// nothing\n")
    c = start(tb, clientCommands=["uvm.addIncludeDir"])
    try:
        doc = Doc(c, agent(tb))
        doc.change('`include "sub/hdr.svh"\n' + doc.text)
        [d] = doc.diagnostics(lambda ds: ds)
        assert (d["source"], d["code"], d["data"]) == ("slang", "CouldNotOpenIncludeFile", {"include": "sub/hdr.svh"})
        setting, filelist = doc.actions([d])
        assert (setting["title"], setting["command"]["command"], setting["command"]["arguments"]) == (
            "Add 'inc' to uvm.includeDirs", "uvm.addIncludeDir", [{"value": "inc"}])
        assert filelist["title"] == "Add +incdir+inc to alu_tb.f" and "isPreferred" not in filelist
        path = os.path.join(tb, "alu_tb.f")
        with open(path) as f:
            text = f.read()
        assert applied(text, edits_for(filelist, f"file://{path}")) == \
            text.replace("+incdir+tests\n", "+incdir+tests\n+incdir+inc\n")
    finally:
        c.close()
        shutil.rmtree(inc)


def test_a_stale_diagnostic_maps_to_the_finding_that_moved():
    # Two findings that read the same: the one the client's (older) diagnostic was about is the
    # one at the line its line moved to, not merely the nearest.
    from lsprotocol import types as T

    from uvmls.model import Diag, Range
    from uvmls.server import current_finding

    def diag(line):
        return Diag(Range("f", line, 4, line, 9), "component a is created without a parent", "error",
                    "create-no-parent", "uvm", data={"cls": "a", "at": [line, 4]})
    then = "".join(f"l{i}\n" for i in range(20))
    now = "\n" * 6 + then  # six lines above both
    sent = T.Diagnostic(T.Range(T.Position(9, 4), T.Position(9, 9)), "component a is created without a parent",
                        code="create-no-parent", source="uvm", data={"cls": "a", "at": [9, 4]})
    found = current_finding(sent, [diag(12), diag(15)], then, now)
    assert found.range.line == 15  # 9 + 6, although 12 is nearer to 9
    assert current_finding(sent, [diag(12), diag(15)], None, None).range.line == 12  # no text: nearest
    other = T.Diagnostic(sent.range, sent.message, code="create-no-parent", source="uvm", data={"cls": "b", "at": [9, 4]})
    assert current_finding(other, [diag(12)], then, now) is None
