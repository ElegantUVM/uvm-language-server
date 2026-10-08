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


IGNORE = "Ignore '{}' on this line"


def own_fixes(actions: list) -> list:
    """A lint finding's actions but the last, which is always Ignore."""
    assert actions and actions[-1]["title"] == IGNORE.format(actions[-1]["diagnostics"][0]["code"])
    return actions[:-1]


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
    a, ignore = doc.actions([d])
    assert (a["title"], a["kind"], a["isPreferred"], a["diagnostics"]) == ("Pass 'this' as the parent", "quickfix", True, [d])
    assert (ignore["title"], ignore["kind"], ignore["diagnostics"]) == ("Ignore 'create-no-parent' on this line", "quickfix", [d])
    assert "isPreferred" not in ignore
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
    [a] = own_fixes(doc.actions([d]))
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
    [a] = own_fixes(doc.actions([d], trigger=2))
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
    [a] = own_fixes(doc.actions([d]))
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
        [a] = own_fixes(doc.actions([d]))
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
        a, ignore = doc.actions([d])
        assert ignore["title"] == IGNORE.format("create-no-parent")
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
    [a] = own_fixes(env.actions([d]))
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
        [a] = own_fixes(env.actions([d]))
        assert applied(planted, edits_for(a, sb.uri, None)) == original
        env.close()
    finally:
        with open(sb_path, "w") as f:
            f.write(original)
        client.notify("workspace/didChangeWatchedFiles", {"changes": [{"uri": sb.uri, "type": 2}]})
    published(client, env_path, lambda ds: not ds)


def test_registration_hidden_in_an_include_has_no_actions(tmp_path):
    from test_fixes import COMPONENT
    text = 'import uvm_pkg::*;\n`include "uvm_macros.svh"\n' + COMPONENT
    path = tmp_path / "t.sv"
    path.write_text(text)
    (tmp_path / "alu_tb.f").write_text("t.sv\n")
    (tmp_path / "registration.svh").write_text('`include "utils.svh"\n')
    (tmp_path / "utils.svh").write_text("`project_component_utils(drv)\n")
    c = start(str(tmp_path))
    try:
        doc = Doc(c, str(path))
        doc.change(text.replace("  `uvm_component_utils(drv)\n", ""))
        ds = doc.diagnostics(lambda ds: any(d["code"] == "create-unregistered" for d in ds))
        [d, _] = [d for d in ds if d["code"] == "create-unregistered"]
        assert any(a.get("isPreferred") for a in doc.actions([d]))
        assert doc.actions([], only=["source.fixAll.uvm"])
        doc.change(text.replace("`uvm_component_utils(drv)", '`include "registration.svh"'))
        assert doc.actions([d]) == []
        assert doc.actions([], only=["source.fixAll.uvm"]) == []
    finally:
        c.close()


def test_fix_all(client, tb):
    doc = Doc(client, agent(tb))
    original = doc.text
    doc.change(original.replace('create("m_driver", this)', 'create("m_driver")')
               .replace('create("m_sequencer", this)', 'create("m_sequencer")'))
    ds = doc.diagnostics(lambda ds: len(ds) == 2)
    acts = doc.actions(ds[:1])  # the lightbulb on one of them; never the source action unasked
    assert [(a["title"], a["kind"], a.get("isPreferred")) for a in acts] == [
        ("Pass 'this' as the parent", "quickfix", True),
        ("Fix all 2 'create-no-parent' problems in this file", "quickfix", None),
        ("Ignore 'create-no-parent' on this line", "quickfix", None)]
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


def test_ignore(client, tb):
    """Ignore edits the open document at its version. The directive clears the finding while the
    text is unsaved, and fix-all leaves that finding alone from then on. slang's get no Ignore."""
    doc = Doc(client, agent(tb))
    original = doc.text
    planted = original.replace('create("m_driver", this)', 'create("m_driver")')
    doc.change(planted)
    [d] = doc.diagnostics(lambda ds: ds)
    fix, ignore = doc.actions([d])
    assert (fix["title"], ignore["title"]) == ("Pass 'this' as the parent", "Ignore 'create-no-parent' on this line")
    waived = planted.replace('create("m_driver");', 'create("m_driver");  // uvm-lint: ignore create-no-parent')
    assert applied(doc.text, edits_for(ignore, doc.uri, doc.version)) == waived
    doc.change(waived)
    doc.diagnostics(lambda ds: not ds)
    doc.change(waived.replace('create("m_sequencer", this)', 'create("m_sequencer")'))
    [d2] = doc.diagnostics(lambda ds: ds)
    assert d2["range"]["start"]["line"] == d["range"]["start"]["line"] + 1
    [a] = doc.actions([], only=["source.fixAll"])
    assert applied(doc.text, edits_for(a, doc.uri, doc.version)) == waived
    doc.change('`include "nowhere.svh"\n' + original)
    [d3] = doc.diagnostics(lambda ds: ds)
    assert (d3["source"], d3["code"]) == ("slang", "CouldNotOpenIncludeFile") and doc.actions([d3]) == []
    doc.change(original)
    doc.diagnostics(lambda ds: not ds)
    doc.close()


def test_commands_only_for_a_client_that_has_them(client, tb):
    mon = os.path.join(tb, "agent", "alu_monitor.svh")
    doc = Doc(client, mon)
    doc.change("\n" + doc.text)  # moves the planted finding, so it is published again
    [d] = doc.diagnostics(lambda ds: ds)
    assert d["code"] == "config-db-no-set" and d["severity"] == 3  # optional get: a note
    assert [a["title"] for a in doc.actions([d])] == [IGNORE.format("config-db-no-set")]
    doc.close()
    c = start(tb, clientCommands=["uvm.showConfigDb"])
    try:
        doc = Doc(c, mon)
        doc.change("\n" + doc.text)
        [d] = doc.diagnostics(lambda ds: ds)
        [a] = own_fixes(doc.actions([d]))
        assert (a["title"], a["command"]["command"], a["command"]["arguments"]) == (
            "Show 'checks_enable' in the config_db cross-reference", "uvm.showConfigDb", [{"field": "checks_enable"}])
        assert "isPreferred" not in a and "edit" not in a
    finally:
        c.close()


def test_missing_include(tb):
    # The editor owns settings changes; unsynced filelists cannot be edited safely.
    inc = os.path.join(tb, "inc")
    os.makedirs(os.path.join(inc, "sub"))
    with open(os.path.join(inc, "sub", "hdr.svh"), "w") as f:
        f.write("// nothing\n")
    c = start(tb, clientCommands=["uvm.addIncludeDir"])
    try:
        doc = Doc(c, agent(tb))
        doc.change('`include "sub/hdr.svh"\n' + doc.text)
        [d] = doc.diagnostics(lambda ds: ds)
        assert (d["source"], d["code"], d["data"]["include"]) == ("slang", "CouldNotOpenIncludeFile", "sub/hdr.svh")
        [setting] = doc.actions([d])
        assert (setting["title"], setting["command"]["command"], setting["command"]["arguments"]) == (
            "Add 'inc' to uvm.includeDirs", "uvm.addIncludeDir", [{"dir": inc, "value": "inc"}])
        assert "edit" not in setting
        c.notify('workspace/didChangeConfiguration', {'settings': {'uvm': {
            'filelists': [os.path.join(tb, 'alu_tb.f')], 'elaboration': 'off', 'includeDirs': ['inc']}}})
        doc.diagnostics(lambda ds: not ds)
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
    assert current_finding(sent, [diag(12), diag(15)], None, None) is None
    assert current_finding(sent, [diag(9), diag(15)], None, None).range.line == 9
    other = T.Diagnostic(sent.range, sent.message, code="create-no-parent", source="uvm", data={"cls": "b", "at": [9, 4]})
    assert current_finding(other, [diag(12)], then, now) is None


@pytest.mark.parametrize('delete', [False, True])
@pytest.mark.parametrize('data', [False, True])
@pytest.mark.parametrize('republished', [False, True])
def test_removed_finding_does_not_match_its_neighbour(tb, delete, data, republished):
    client = start(tb)
    doc = Doc(client, agent(tb))
    original = doc.text
    call = '    m_monitor = alu_monitor::type_id::create("m_monitor");\n'
    planted = original.replace('    m_monitor = alu_monitor::type_id::create("m_monitor", this);\n',
                               call.replace('m_monitor', 'mon1') + call.replace('m_monitor', 'mon2'))
    call = call.replace('m_monitor', 'mon1')
    # Same-length names give both findings identical ranges after deleting the first.
    try:
        doc.change(planted)
        ds = doc.diagnostics(lambda ds: len(ds) == 2)
        old = dict(ds[0])
        assert doc.actions([old])
        if not data:
            old.pop('data', None)
        changed = planted.replace(call, '' if delete else call.replace('");', '", this);'), 1)
        # A second edit below the survivor must not make it part of the first edit.
        doc.change(changed.replace('endclass', '// edited below both calls\nendclass'))
        if republished:
            current = doc.diagnostics(lambda ds: len(ds) == 1)
        assert doc.actions([old]) == []
        if not republished:
            current = doc.diagnostics(lambda ds: len(ds) == 1)
        assert doc.actions([old]) == []
        assert own_fixes(doc.actions(current))  # the survivor still has its own identity
    finally:
        doc.change(original)
        client.close()


def test_stale_finding_only_matches_unchanged_lines():
    from uvmls.model import Diag, Range
    from uvmls.server import current_finding, lsp_diagnostic

    def diag(line):
        return Diag(Range('f', line, 0, line, 4), 'same', 'warning', 'create-no-parent', 'uvm')
    then = 'head\nfirst\nsecond\ntail\n'
    sent = lsp_diagnostic(diag(1))
    assert current_finding(sent, [diag(2)], then, then.replace('first', 'fixed')) is None
    assert current_finding(sent, [diag(1), diag(2)], then, then.replace('first', 'edited')) is None
    assert current_finding(sent, [diag(1)], then, then.replace('first\n', '')) is None
    assert current_finding(sent, [diag(2)], then, then) is None


@pytest.mark.parametrize('data', [False, True])
def test_diagnostic_identity_survives_republication(tb, data):
    client = start(tb)
    doc = Doc(client, agent(tb))
    original = doc.text
    try:
        doc.change(original.replace('create("m_driver", this)', 'create("m_driver")'))
        [old] = doc.diagnostics(lambda ds: ds)
        if not data:
            old.pop('data', None)
        for i in range(2):
            doc.change('// inserted\n' + doc.text.replace('endclass', '// below\nendclass'))
            [current] = doc.diagnostics(lambda ds: ds)
            [fix] = own_fixes(doc.actions([old]))
            assert applied(doc.text, edits_for(fix, doc.uri, doc.version)) == doc.text.replace(
                'create("m_driver")', 'create("m_driver", this)')
            assert current['range']['start']['line'] == old['range']['start']['line'] + i + 1
    finally:
        client.close()


def test_full_reindex_moves_an_unopened_finding(tmp_path):
    root = tmp_path / 'tb'
    shutil.copytree(EXAMPLE, root)
    path = root / 'agent' / 'alu_agent.svh'
    planted = path.read_text().replace('create("m_driver", this)', 'create("m_driver")')
    path.write_text(planted)
    c = start(str(root))
    try:
        [d] = published(c, str(path), lambda ds: ds)
        text = '// inserted\n// another\n' + planted
        path.write_text(text)
        extra = root / 'new.sv'
        extra.write_text('// new file\n')
        c.notify('workspace/didChangeWatchedFiles', {'changes': [
            {'uri': path.as_uri(), 'type': 2}, {'uri': extra.as_uri(), 'type': 1}]})
        # The finding is republished two lines down once the full re-index is done. Asking for a
        # fix before that may get no actions: the request does not wait past its deadline.
        published(c, str(path), lambda ds: [x['range']['start']['line'] for x in ds] == [d['range']['start']['line'] + 2])
        acts = c.request('textDocument/codeAction', {
            'textDocument': {'uri': path.as_uri()}, 'range': d['range'], 'context': {'diagnostics': [d]}})
        fix, ignore = acts
        assert applied(text, edits_for(fix, path.as_uri())) == text.replace('create("m_driver")', 'create("m_driver", this)')
        assert applied(text, edits_for(ignore, path.as_uri())) == text.replace(
            'create("m_driver");', 'create("m_driver");  // uvm-lint: ignore create-no-parent')
    finally:
        c.close()


@pytest.mark.parametrize('separator', ['\f', '\v', '\x1c', '\x1d', '\x1e', '\x85', '\u2028', '\u2029'])
def test_incremental_edits_use_lsp_lines(client, tb, separator):
    path = agent(tb)
    with open(path) as f:
        original = f.read()
    call = '    m_monitor = alu_monitor::type_id::create("m_monitor", this);\n'
    original = '// page' + separator + '\n' + original.replace(call, call + call)
    doc = Doc(client, path, original)
    try:
        lines = original.split('\n')
        line = [i for i, text in enumerate(lines) if text + '\n' == call][1]
        col = lines[line].index(', this')
        rng = {'start': {'line': line, 'character': col}, 'end': {'line': line, 'character': col + 6}}
        doc.version += 1
        doc.text = applied(original, [{'range': rng, 'newText': ''}])
        client.notify('textDocument/didChange', {'textDocument': {'uri': doc.uri, 'version': doc.version},
                                                'contentChanges': [{'range': rng, 'text': ''}]})
        [d] = doc.diagnostics(lambda ds: any(x['code'] == 'create-no-parent' for x in ds))
        assert d['range']['start']['line'] == line
        [fix] = own_fixes(doc.actions([d]))
        assert applied(doc.text, edits_for(fix, doc.uri, doc.version)) == original
    finally:
        doc.close()


def test_document_lines_preserve_non_lsp_separators():
    from uvmls.documents import TextDocument
    doc = TextDocument('file:///t.sv', source='a\fb\r\nc\rd\n')
    assert doc.lines == ('a\fb\r\n', 'c\r', 'd\n')


def test_per_code_fix_all_is_emitted_once_for_a_selection(client, tb):
    doc = Doc(client, agent(tb))
    original = doc.text
    try:
        doc.change(original.replace('create("m_driver", this)', 'create("m_driver")')
                   .replace('create("m_sequencer", this)', 'create("m_sequencer")'))
        ds = doc.diagnostics(lambda ds: len(ds) == 2)
        acts = client.request('textDocument/codeAction', {
            'textDocument': {'uri': doc.uri},
            'range': {'start': ds[0]['range']['start'], 'end': ds[-1]['range']['end']},
            'context': {'diagnostics': ds}})
        bulk = [a for a in acts if a['title'].startswith('Fix all')]
        assert len(bulk) == 1
        assert applied(doc.text, edits_for(bulk[0], doc.uri, doc.version)) == original
        individual = [a for a in acts if a['title'] == "Pass 'this' as the parent"]
        assert len(individual) == 2
        assert {a['diagnostics'][0]['range']['start']['line'] for a in individual} == {
            d['range']['start']['line'] for d in ds}
    finally:
        doc.change(original)
        doc.close()


def test_per_code_fix_all_only_attaches_to_findings_it_fixes(client, tb):
    doc = Doc(client, agent(tb))
    original = doc.text
    helper = '''  static function alu_monitor make_mon();
    return alu_monitor::type_id::create("mon");
  endfunction
'''
    try:
        doc.change(original.replace('create("m_driver", this)', 'create("m_driver")')
                   .replace('create("m_sequencer", this)', 'create("m_sequencer")')
                   .replace('endclass', helper + 'endclass'))
        ds = doc.diagnostics(lambda ds: len(ds) == 3)
        assert [a['title'] for a in doc.actions(ds[-1:])] == [
            'Pass null as the parent', IGNORE.format('create-no-parent')]
        for d in ds[:2]:
            [bulk] = [a for a in doc.actions([d]) if a['title'].startswith('Fix all')]
            assert len(bulk['diagnostics']) == 2
        assert len([a for a in doc.actions(ds) if a['title'].startswith('Fix all')]) == 1
    finally:
        doc.change(original)
        doc.close()


@pytest.mark.parametrize('commands,expected', [
    (5, frozenset()), (True, frozenset()), (1.5, frozenset()),
    ('uvm.showConfigDb', frozenset()), ({'uvm.showConfigDb': True}, frozenset()),
    (None, frozenset()), (False, frozenset()),
    (['uvm.showConfigDb', 5, None], frozenset({'uvm.showConfigDb'})),
])
def test_initialize_validates_client_commands(commands, expected):
    from lsprotocol import types as T
    from pygls.workspace import Workspace as LspWorkspace
    from uvmls.server import UvmLanguageServer, initialize

    ls = UvmLanguageServer()
    ls.protocol._workspace = LspWorkspace(None)
    initialize(ls, T.InitializeParams(capabilities=T.ClientCapabilities(), initialization_options={
        'settings': {'elaboration': 'off'}, 'clientCommands': commands}))
    assert ls.client_commands == expected


@pytest.mark.skipif(os.environ.get('UVMLS_PERF') != '1', reason='set UVMLS_PERF=1 for the generated project budgets')
def test_large_project_code_action_budgets(tmp_path):
    import asyncio
    import time

    from lsprotocol import types as T
    from tools.gen_big import generate
    from uvmls.documents import Workspace as LspWorkspace
    from uvmls.lints import lint
    from uvmls.server import UvmLanguageServer, code_action, code_lens, inlay_hint, lsp_diagnostic, signature_help
    from uvmls.server import document_highlight

    async def run():
        filelist = generate(tmp_path, classes=2400)
        driver = tmp_path / 'classes/node_0000.sv'
        driver.write_text(driver.read_text().replace('extends uvm_component', 'extends uvm_driver #(node_0001)').replace(
            'function void setup();', 'function void setup();\n    req = rsp;'))
        bench = tmp_path / 'bench.sv'
        bench.write_text(bench.read_text().replace('extends uvm_env', 'extends uvm_test').replace(
            'super.build_phase(phase);', 'super.build_phase(phase);\n'
            '    set_type_override_by_type(node_0000::get_type(), node_0001::get_type());'))
        ls = UvmLanguageServer()
        ls.workspace_roots = [str(tmp_path)]
        ls.settings = {'filelists': [str(filelist)], 'useBundledUvm': False, 'elaboration': 'off'}
        ls.protocol._workspace = LspWorkspace(tmp_path.as_uri())
        ls.protocol.client_capabilities = T.ClientCapabilities(
            text_document=T.TextDocumentClientCapabilities(code_lens=T.CodeLensClientCapabilities(),
                                                            inlay_hint=T.InlayHintClientCapabilities()))
        ls.client_commands = frozenset({'uvm.addIncludeDir'})
        ls.ws.configure(ls.config())
        assert len(ls.ws.classes) == 2400
        ls.published = lint(ls.ws)
        for d in ls.ws.user_diagnostics():
            ls.published.setdefault(d.range.path, []).append(d)
        ls.diag_texts = dict(ls.ws.texts)
        path = tmp_path / 'bench.sv'
        ds = [lsp_diagnostic(d) for d in ls.published[str(path)]]
        parents = [d for d in ds if d.code == 'create-no-parent']
        configs = [d for d in ds if d.code == 'config-db-no-set']
        includes = [d for d in ds if d.code == 'CouldNotOpenIncludeFile']
        assert len(parents) == len(configs) == 20 and len(includes) == 1
        shapes = [
            ('empty', [], None, 1),
            ('one fix', parents[:1], None, 10),
            ('config fix', configs[:1], None, 10),
            ('20 config fixes', configs, None, 50),
            ('source.fixAll', [], [T.CodeActionKind.SourceFixAll], 25),
            ('cold include', includes, None, 25),
        ]
        for name, incoming, only, target in shapes:
            samples = []
            for _ in range(20):
                if name == 'cold include':
                    ls.ws._include_candidates = None
                params = T.CodeActionParams(T.TextDocumentIdentifier(path.as_uri()),
                                             incoming[0].range if incoming else T.Range(T.Position(0, 0), T.Position(0, 0)),
                                             T.CodeActionContext(incoming, only=only))
                start = time.perf_counter()
                actions = await code_action(ls, params)
                samples.append((time.perf_counter() - start) * 1000)
                if name == 'empty':
                    assert actions is None
                else:
                    assert actions
                if name == 'source.fixAll':
                    assert len(actions) == 1 and len(actions[0].diagnostics) == 20
            p95 = sorted(samples)[18]
            print(f'{name}: p95 {p95:.2f} ms (target {target} ms)')
            assert p95 < target
        params = T.CodeLensParams(T.TextDocumentIdentifier(path.as_uri()))
        await code_lens(ls, params)
        samples = []
        for _ in range(20):
            start = time.perf_counter()
            lenses = await code_lens(ls, params)
            samples.append((time.perf_counter() - start) * 1000)
            assert sum(l.command.title.startswith('$(database)') for l in lenses) == 20
        p95 = sorted(samples)[18]
        print(f'config lenses: p95 {p95:.2f} ms (target 50 ms)')
        assert p95 < 50
        for name, source, count in [('factory hints', path, 1), ('bound member hints', driver, 2)]:
            params = T.InlayHintParams(T.TextDocumentIdentifier(source.as_uri()),
                                      T.Range(T.Position(0, 0), T.Position(10000, 0)))
            await inlay_hint(ls, params)
            samples = []
            for _ in range(20):
                start = time.perf_counter()
                hints = await inlay_hint(ls, params)
                samples.append((time.perf_counter() - start) * 1000)
                assert len(hints) == count
            p95 = sorted(samples)[18]
            print(f'{name}: p95 {p95:.2f} ms (target 50 ms)')
            assert p95 < 50
        source = driver.read_text()
        # The generated child also has its own constructor; use a typed direct call in shim mode.
        source = source.replace("super.new(", "node_0000::new(")
        ls.ws.update_document(str(driver), source)
        offset = source.index("node_0000::new(") + len("node_0000::new(")
        prefix = source[:offset]
        params = T.SignatureHelpParams(T.TextDocumentIdentifier(driver.as_uri()),
                                       T.Position(prefix.count("\n"), len(prefix.rsplit("\n", 1)[-1])))
        await signature_help(ls, params)
        samples = []
        for _ in range(20):
            start = time.perf_counter()
            help = await signature_help(ls, params)
            samples.append((time.perf_counter() - start) * 1000)
            assert help and len(help['signatures'][0]['parameters']) == 2
        p95 = sorted(samples)[18]
        print(f'signature help: p95 {p95:.2f} ms (target 50 ms)')
        assert p95 < 50
        params = T.DocumentHighlightParams(T.TextDocumentIdentifier(driver.as_uri()), T.Position(3, 7))
        await document_highlight(ls, params)
        samples = []
        for _ in range(20):
            start = time.perf_counter()
            result = await document_highlight(ls, params)
            samples.append((time.perf_counter() - start) * 1000)
            assert len(result) == 2
        p95 = sorted(samples)[18]
        print(f'document highlights: p95 {p95:.2f} ms (target 50 ms)')
        assert p95 < 50
    asyncio.run(run())
