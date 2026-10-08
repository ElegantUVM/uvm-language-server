"""Drive the real server process over stdio JSON-RPC, the way VS Code does."""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

import pytest

from conftest import EXAMPLE, FILELIST

SERVER_DIR = os.path.dirname(os.path.dirname(__file__))


class Client:
    def __init__(self):
        env = dict(os.environ, PYTHONPATH=SERVER_DIR)
        env.pop("UVM_HOME", None)  # default settings: the bundled UVM
        self.proc = subprocess.Popen([sys.executable, "-m", "uvmls", "--stdio"], cwd=SERVER_DIR,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        self.next_id = 0
        self.notifications = []
        self.messages = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def send(self, msg):
        body = json.dumps(msg).encode()
        self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.proc.stdin.flush()

    def _pump(self):
        """Read the server's messages in the background, so a wait can time out."""
        try:
            while True:
                headers = {}
                while True:
                    line = self.proc.stdout.readline()
                    if not line:
                        raise EOFError("server exited")
                    line = line.decode().strip()
                    if not line:
                        break
                    k, v = line.split(":", 1)
                    headers[k.lower()] = v.strip()
                self.messages.put(json.loads(self.proc.stdout.read(int(headers["content-length"]))))
        except (EOFError, ValueError, OSError) as e:
            self.messages.put(e)

    def read(self, timeout=30):
        try:
            msg = self.messages.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("no message from the server") from None
        if isinstance(msg, Exception):
            raise EOFError("server exited") from msg
        return msg

    def request(self, method, params):
        self.next_id += 1
        rid = self.next_id
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            msg = self.read()
            if msg.get("id") == rid and "method" not in msg:
                assert "error" not in msg, msg
                return msg["result"]
            if "method" in msg and "id" in msg:  # server -> client request
                self.send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
            elif "method" in msg:
                self.notifications.append(msg)

    def notify(self, method, params):
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def forget(self, *methods):
        """Drop queued notifications, so a later wait_for sees only what happens next."""
        self.notifications = [m for m in self.notifications if m["method"] not in methods]

    def wait_for(self, method, pred=lambda m: True, timeout=20):
        deadline = time.time() + timeout
        for i, m in enumerate(self.notifications):
            if m["method"] == method and pred(m):
                return self.notifications.pop(i)
        while time.time() < deadline:
            try:
                msg = self.read(timeout=max(0.01, deadline - time.time()))
            except TimeoutError:
                break
            if "method" in msg and "id" in msg:
                self.send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
            elif msg.get("method") == method and pred(msg):
                return msg
            elif "method" in msg:
                self.notifications.append(msg)
        raise TimeoutError(method)

    def close(self):
        try:
            self.request("shutdown", None)
            self.notify("exit", None)
            self.proc.wait(timeout=5)
        finally:
            if self.proc.poll() is None:
                self.proc.kill()


def uri(rel):
    return "file://" + os.path.join(EXAMPLE, rel)


def finished(m):
    return m["params"]["state"] in ("done", "skipped", "failed")


def start(root, **settings) -> Client:
    """A server of its own on `root`, initialized."""
    c = Client()
    c.request("initialize", {"processId": None, "rootUri": f"file://{root}", "capabilities": {},
                             "initializationOptions": {"settings": settings}})
    c.notify("initialized", {})
    return c


@pytest.fixture(scope="module")
def client():
    c = Client()
    c.request("initialize", {
        "processId": None, "rootUri": "file://" + EXAMPLE, "capabilities": {},
        "workspaceFolders": [{"uri": "file://" + EXAMPLE, "name": "alu_tb"}],
        "initializationOptions": {"settings": {"filelists": [FILELIST], "elaboration": "onSave"}},
    })
    c.notify("initialized", {})
    c.wait_for("uvm/indexChanged")
    # The first elaboration publishes everything again; let it finish before the tests start.
    status = c.wait_for("uvm/elaborationStatus", finished)["params"]
    assert status["state"] == "done" and status["errors"] == 0, status
    yield c
    c.close()


def test_stats_and_diagnostics(client):
    stats = client.request("uvm/stats", {})
    assert stats["classes"] == 19 and stats["mode"] == "filelist"
    assert stats["uvm"]["source"] == "bundled" and stats["uvm"]["version"] == "1.2"
    assert stats["libraryClasses"] > 300
    diag = client.wait_for("textDocument/publishDiagnostics",
                           lambda m: m["params"]["uri"].endswith("alu_monitor.svh"))
    assert [d["code"] for d in diag["params"]["diagnostics"]] == ["config-db-no-set"]
    # What its quick fixes need comes with it.
    [d] = diag["params"]["diagnostics"]
    assert isinstance(d["data"].pop("_uvmls_id"), int)
    assert d["data"] == {"field": "checks_enable", "at": [d["range"]["start"]["line"], d["range"]["start"]["character"]]}


def test_definition_and_hover(client):
    # alu_driver.svh line 40: "  repeat (m_cfg.idle_cycles) @(vif.drv_cb);"
    res = client.request("textDocument/definition", {
        "textDocument": {"uri": uri("agent/alu_driver.svh")}, "position": {"line": 39, "character": 18}})
    assert res and res[0]["uri"].endswith("alu_agent_config.svh") and res[0]["range"]["start"]["line"] == 7
    hov = client.request("textDocument/hover", {
        "textDocument": {"uri": uri("agent/alu_driver.svh")}, "position": {"line": 1, "character": 8}})
    assert "**driver**" in hov["contents"]["value"]


def test_custom_requests(client):
    g = client.request("uvm/classGraph", {})
    assert any(n["id"] == "alu_pkg::alu_driver" for n in g["nodes"])
    t = client.request("uvm/topology", {"test": "alu_error_test"})
    assert t["test"] == "alu_error_test" and t["root"]["children"][0]["name"] == "m_env"
    assert client.request("uvm/classAt", {"uri": uri("agent/alu_agent.svh"),
                                          "position": {"line": 4, "character": 4}}) == "alu_pkg::alu_driver"
    c = client.request("uvm/configDb", {})
    assert {r["field"] for r in c["fields"]} >= {"vif", "checks_enable"}


def test_class_at_answers_with_a_graph_node(client):
    """Show Class in Graph on a UVM type the graph leaves out focuses the class around the cursor."""
    ids = {n["id"] for n in client.request("uvm/classGraph", {})["nodes"]}
    drv = uri("agent/alu_driver.svh")
    with open(os.path.join(EXAMPLE, "agent/alu_driver.svh")) as f:
        lines = f.read().split("\n")

    def at(needle, offset=0):
        line = next(i for i, ln in enumerate(lines) if needle in ln)
        position = {"line": line, "character": lines[line].index(needle) + offset}
        return client.request("uvm/classAt", {"uri": drv, "position": position})

    for needle in ("uvm_phase phase", "uvm_config_db", "seq_item_port"):
        assert at(needle, 2) == "alu_pkg::alu_driver"
    assert at("uvm_driver #(", 2) == "uvm_pkg::uvm_driver" and "uvm_pkg::uvm_driver" in ids  # in the graph


def test_live_edit_updates_index_and_diagnostics(client):
    path = os.path.join(EXAMPLE, "env/alu_env.svh")
    with open(path) as f:
        text = f.read()
    client.forget("textDocument/publishDiagnostics")
    client.notify("textDocument/didOpen", {"textDocument": {
        "uri": uri("env/alu_env.svh"), "languageId": "systemverilog", "version": 1, "text": text}})
    edited = text.replace(
        '    m_agent = alu_agent::type_id::create("m_agent", this);',
        '    uvm_config_db #(bit)::set(this, "*", "checks_enable", 0);\n'
        '    m_agent = alu_agent::type_id::create("m_agent", this);')
    client.notify("textDocument/didChange", {
        "textDocument": {"uri": uri("env/alu_env.svh"), "version": 2},
        "contentChanges": [{"text": edited}]})
    # The planted config_db warning goes away once something sets checks_enable.
    client.wait_for("textDocument/publishDiagnostics",
                    lambda m: m["params"]["uri"].endswith("alu_monitor.svh") and not m["params"]["diagnostics"])
    rows = {r["field"]: r for r in client.request("uvm/configDb", {})["fields"]}
    assert rows["checks_enable"]["status"] == "ok"
    client.forget("textDocument/publishDiagnostics")
    client.notify("textDocument/didClose", {"textDocument": {"uri": uri("env/alu_env.svh")}})
    client.wait_for("textDocument/publishDiagnostics",
                    lambda m: m["params"]["uri"].endswith("alu_monitor.svh") and m["params"]["diagnostics"])


def test_use_bundled_uvm_setting(client):
    def configure(source, **settings):
        client.notifications = [m for m in client.notifications if m["method"] != "uvm/indexChanged"]
        client.notify("workspace/didChangeConfiguration",
                      {"settings": {"uvm": {"filelists": [FILELIST], **settings}}})
        msg = client.wait_for("uvm/indexChanged", lambda m: m["params"]["stats"]["uvm"]["source"] == source)
        return msg["params"]["stats"]

    client.forget("uvm/elaborationStatus")
    stats = configure("builtin", useBundledUvm=False)
    assert stats["libraryClasses"] == 0 and stats["classes"] == 19
    status = client.wait_for("uvm/elaborationStatus", finished)["params"]
    assert status["state"] == "skipped" and "built-in UVM model" in status["message"]
    assert configure("bundled")["libraryClasses"] > 300
    assert client.wait_for("uvm/elaborationStatus", finished)["params"]["state"] == "done"


def test_document_symbols_and_type_hierarchy(client):
    syms = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri("agent/alu_driver.svh")}})
    assert [s["name"] for s in syms][0] == "alu_driver"
    items = client.request("textDocument/prepareTypeHierarchy", {
        "textDocument": {"uri": uri("agent/alu_driver.svh")}, "position": {"line": 1, "character": 8}})
    subs = client.request("typeHierarchy/subtypes", {"item": items[0]})
    assert [s["name"] for s in subs] == ["alu_error_driver"]


def elab_diags(msg):
    return [d for d in msg["params"]["diagnostics"] if d.get("source") == "slang-elab"]


def test_elaboration_on_save(client):
    path = os.path.join(EXAMPLE, "agent/alu_driver.svh")
    with open(path) as f:
        text = f.read()
    u = uri("agent/alu_driver.svh")
    line = text[:text.index("m_cfg.vif")].count("\n")
    client.notify("textDocument/didOpen", {"textDocument": {
        "uri": u, "languageId": "systemverilog", "version": 1, "text": text}})
    broken = text.replace("m_cfg.vif", "m_cfg.vfi")
    client.notify("textDocument/didChange", {"textDocument": {"uri": u, "version": 2},
                                             "contentChanges": [{"text": broken}]})
    client.forget("textDocument/publishDiagnostics", "uvm/elaborationStatus")
    client.notify("textDocument/didSave", {"textDocument": {"uri": u}})
    msg = client.wait_for("textDocument/publishDiagnostics",
                          lambda m: m["params"]["uri"] == u and elab_diags(m))
    [d] = elab_diags(msg)
    assert d["code"] == "UnknownMember" and d["severity"] == 1
    assert d["message"] == "no member named 'vfi' in 'alu_agent_config'"
    assert d["range"]["start"]["line"] == line
    status = client.wait_for("uvm/elaborationStatus", finished)["params"]
    assert (status["state"], status["errors"], status["uvm"]) == ("done", 1, "bundled Accellera UVM 1.2")
    assert status["message"] == "top: tb_top"

    # Fixed and saved: the error goes away.
    client.notify("textDocument/didChange", {"textDocument": {"uri": u, "version": 3},
                                             "contentChanges": [{"text": text}]})
    client.forget("textDocument/publishDiagnostics", "uvm/elaborationStatus")
    client.notify("textDocument/didSave", {"textDocument": {"uri": u}})
    client.wait_for("textDocument/publishDiagnostics", lambda m: m["params"]["uri"] == u and not elab_diags(m))
    assert client.wait_for("uvm/elaborationStatus", finished)["params"]["errors"] == 0
    summary = client.request("uvm/elaborate", {})
    assert (summary["state"], summary["errors"], summary["warnings"]) == ("done", 0, 0)
    assert summary["seconds"] > 0 and summary["uvm"] == "bundled Accellera UVM 1.2"
    client.notify("textDocument/didClose", {"textDocument": {"uri": u}})


@pytest.mark.parametrize("prefix", ["", '`line 200 "generated.tpl" 0\n'])
def test_parse_errors_are_published_once(client, prefix):
    # Elaboration reports parse errors too; only the index's copy is published. The index
    # adds a hint to an include it cannot find, so its message differs from slang-elab's.
    # Both put them on the file's own lines, whatever a `line directive says.
    path = os.path.join(EXAMPLE, "agent/alu_driver.svh")
    with open(path) as f:
        text = f.read()
    u = uri("agent/alu_driver.svh")
    line = text[:text.index("m_cfg.vif;")].count("\n") + prefix.count("\n")
    broken = prefix + text.replace("// Drives alu_seq_items onto the pin interface.", '`include "no_such_file.svh"')
    broken = broken.replace("m_cfg.vif;", "m_cfg.vif")
    client.forget("textDocument/publishDiagnostics")
    client.notify("textDocument/didOpen", {"textDocument": {
        "uri": u, "languageId": "systemverilog", "version": 1, "text": broken}})
    msg = client.wait_for("textDocument/publishDiagnostics",
                          lambda m: m["params"]["uri"] == u and m["params"]["diagnostics"])
    client.forget("textDocument/publishDiagnostics", "uvm/elaborationStatus")
    client.notify("textDocument/didSave", {"textDocument": {"uri": u}})
    assert client.wait_for("uvm/elaborationStatus", finished)["params"]["errors"] == 2
    # The run's merged publish, unless it left what the client shows as it was.
    *_, msg = [msg] + [m for m in client.notifications
                       if m["method"] == "textDocument/publishDiagnostics" and m["params"]["uri"] == u]
    diags = sorted(msg["params"]["diagnostics"], key=lambda d: d["range"]["start"]["line"])
    assert [(d["range"]["start"]["line"], d["code"], d["source"], d["severity"]) for d in diags] == \
        [(prefix.count("\n"), "CouldNotOpenIncludeFile", "slang", 1), (line, "ExpectedToken", "slang", 1)]
    assert "uvm.includeDirs" in diags[0]["message"]  # the index's copy, an error as slang has it

    client.notify("textDocument/didChange", {"textDocument": {"uri": u, "version": 2},
                                             "contentChanges": [{"text": text}]})
    client.forget("uvm/elaborationStatus")
    client.notify("textDocument/didSave", {"textDocument": {"uri": u}})
    assert client.wait_for("uvm/elaborationStatus", finished)["params"]["errors"] == 0
    client.notify("textDocument/didClose", {"textDocument": {"uri": u}})


def test_elaboration_diagnostics_follow_edits_until_the_next_run(client):
    # Between saves the last run's errors move with the lines above them, and go away once the
    # lines they are on change: never back at stale lines, never next to the index's own copy.
    path = os.path.join(EXAMPLE, "agent/alu_driver.svh")
    with open(path) as f:
        text = f.read()
    u = uri("agent/alu_driver.svh")
    versions = iter(range(2, 100))
    broken = text.replace("m_cfg.vif", "m_cfg.vfi")
    syntax = text.replace("super.build_phase(phase);", "super.build_phase(phase)")
    line = broken[:broken.index("m_cfg.vfi")].count("\n")
    sline = text[:text.index("super.build_phase(phase);")].count("\n")
    view = []

    def edit(new_text, save=False):
        """The file's diagnostics in the client once the flush (and the run, if saved) is done."""
        client.forget("textDocument/publishDiagnostics", "uvm/elaborationStatus", "uvm/indexChanged")
        client.notify("textDocument/didChange", {"textDocument": {"uri": u, "version": next(versions)},
                                                 "contentChanges": [{"text": new_text}]})
        if save:
            client.notify("textDocument/didSave", {"textDocument": {"uri": u}})
        client.wait_for("uvm/indexChanged")
        if save:
            assert client.wait_for("uvm/elaborationStatus", finished)["params"]["state"] == "done"
        sent = [m for m in client.notifications
                if m["method"] == "textDocument/publishDiagnostics" and m["params"]["uri"] == u]
        if sent:
            view[:] = [(d["range"]["start"]["line"], d["source"], d["code"]) for d in sent[-1]["params"]["diagnostics"]]
        return sorted(view)

    client.notify("textDocument/didOpen", {"textDocument": {
        "uri": u, "languageId": "systemverilog", "version": 1, "text": text}})
    assert edit(broken, save=True) == [(line, "slang-elab", "UnknownMember")]
    assert edit("//\n//\n//\n" + broken) == [(line + 3, "slang-elab", "UnknownMember")]
    assert edit(broken) == [(line, "slang-elab", "UnknownMember")]
    assert edit(broken.replace("m_cfg.vfi", "m_cfg.vfj")) == []  # its own line changed
    assert edit(syntax, save=True) == [(sline, "slang", "ExpectedToken")]
    assert edit("//\n//\n" + syntax) == [(sline + 2, "slang", "ExpectedToken")]  # once, not twice
    assert edit("//\n//\n" + text) == []  # fixed without saving: no slang-elab copy comes back
    assert edit(text, save=True) == []
    client.notify("textDocument/didClose", {"textDocument": {"uri": u}})


def test_a_long_elaboration_report_is_capped(client):
    path = os.path.join(EXAMPLE, "tb/tb_top.sv")
    with open(path) as f:
        text = f.read()
    u = uri("tb/tb_top.sv")
    many = text.replace("endmodule", "".join(f"  initial nope_{i} = 1;\n" for i in range(150)) + "endmodule")
    client.notify("textDocument/didOpen", {"textDocument": {
        "uri": u, "languageId": "systemverilog", "version": 1, "text": many}})
    client.forget("textDocument/publishDiagnostics", "uvm/elaborationStatus")
    client.notify("textDocument/didSave", {"textDocument": {"uri": u}})
    status = client.wait_for("uvm/elaborationStatus", finished)["params"]
    assert status["errors"] == 150 and "Problems lists the first 100 of 150" in status["message"]
    *_, msg = [m for m in client.notifications
               if m["method"] == "textDocument/publishDiagnostics" and m["params"]["uri"] == u]
    assert len(elab_diags(msg)) == 100
    client.notify("textDocument/didChange", {"textDocument": {"uri": u, "version": 2},
                                             "contentChanges": [{"text": text}]})
    client.forget("uvm/elaborationStatus")
    client.notify("textDocument/didSave", {"textDocument": {"uri": u}})
    assert client.wait_for("uvm/elaborationStatus", finished)["params"]["errors"] == 0
    client.notify("textDocument/didClose", {"textDocument": {"uri": u}})


def test_elaboration_off_on_demand_and_on_change(client):
    u = uri("agent/alu_driver.svh")
    with open(os.path.join(EXAMPLE, "agent/alu_driver.svh")) as f:
        text = f.read()

    def configure(mode):
        client.forget("uvm/elaborationStatus", "textDocument/publishDiagnostics")
        client.notify("workspace/didChangeConfiguration",
                      {"settings": {"uvm": {"filelists": [FILELIST], "elaboration": mode}}})

    def published(with_elab):
        return client.wait_for("textDocument/publishDiagnostics",
                               lambda m: m["params"]["uri"] == u and bool(elab_diags(m)) == with_elab)

    configure("off")
    assert client.wait_for("uvm/elaborationStatus")["params"]["state"] == "off"
    client.notify("textDocument/didOpen", {"textDocument": {
        "uri": u, "languageId": "systemverilog", "version": 1, "text": text.replace("m_cfg.vif", "m_cfg.vfi")}})
    # Elaborate Now works whatever the setting, and compiles the unsaved text.
    assert client.request("uvm/elaborate", {})["errors"] == 1
    published(True)
    configure("onSave")  # turned on: runs at once
    assert client.wait_for("uvm/elaborationStatus", finished)["params"]["errors"] == 1
    configure("off")  # turned off: its diagnostics go away
    published(False)
    configure("onChange")
    assert client.wait_for("uvm/elaborationStatus", finished)["params"]["errors"] == 1
    client.forget("uvm/elaborationStatus", "textDocument/publishDiagnostics")
    client.notify("textDocument/didChange", {"textDocument": {"uri": u, "version": 2},
                                             "contentChanges": [{"text": text}]})
    published(False)  # no save needed
    assert client.wait_for("uvm/elaborationStatus", finished)["params"]["errors"] == 0
    configure("onSave")
    client.notify("textDocument/didClose", {"textDocument": {"uri": u}})


def test_watched_file_change_elaborates(client):
    client.forget("uvm/elaborationStatus")
    client.notify("workspace/didChangeWatchedFiles",
                  {"changes": [{"uri": uri("agent/alu_monitor.svh"), "type": 2}]})
    assert client.wait_for("uvm/elaborationStatus", finished)["params"]["state"] == "done"


def test_a_newer_run_supersedes_an_older_one(client):
    client.forget("uvm/elaborationStatus")
    ids = []
    for _ in range(2):
        client.next_id += 1
        ids.append(client.next_id)
        client.send({"jsonrpc": "2.0", "id": client.next_id, "method": "uvm/elaborate", "params": {}})
    results = {}
    while len(results) < 2:
        msg = client.read()
        if msg.get("id") in ids and "method" not in msg:
            results[msg["id"]] = msg["result"]
        elif "method" in msg:
            client.notifications.append(msg)
    # The first run is killed and reports nothing; both requests get the second run's status.
    done = [m["params"] for m in client.notifications if m["method"] == "uvm/elaborationStatus" and finished(m)]
    assert len(done) == 1 and results[ids[0]] == results[ids[1]] == done[0]


def test_an_edit_does_not_resend_unchanged_diagnostics(client):
    u = uri("agent/alu_driver.svh")
    with open(os.path.join(EXAMPLE, "agent/alu_driver.svh")) as f:
        text = f.read()
    client.forget("textDocument/publishDiagnostics", "uvm/indexChanged")
    client.notify("textDocument/didOpen", {"textDocument": {
        "uri": u, "languageId": "systemverilog", "version": 1, "text": text}})
    client.notify("textDocument/didChange", {"textDocument": {"uri": u, "version": 2},
                                             "contentChanges": [{"text": text + "// edited\n"}]})
    client.wait_for("uvm/indexChanged")  # sent after the flush's diagnostics, if any
    sent = [m["params"]["uri"] for m in client.notifications if m["method"] == "textDocument/publishDiagnostics"]
    assert sent == []  # alu_monitor.svh's one warning has not changed
    client.notify("textDocument/didChange", {"textDocument": {"uri": u, "version": 3},
                                             "contentChanges": [{"text": text}]})
    client.wait_for("uvm/indexChanged")  # closed unchanged: no elaboration either
    client.notify("textDocument/didClose", {"textDocument": {"uri": u}})


def test_elaboration_counts_what_problems_shows(tmp_path):
    # The index reports a missing include as a warning (with a hint); elaboration, as slang does,
    # as an error. Problems shows it once, as an error, and the status counts match.
    (tmp_path / "top.sv").write_text('`include "missing.svh"\nmodule top;\nendmodule\n')
    (tmp_path / "tb.f").write_text("top.sv\n")
    c = start(tmp_path, filelists=[str(tmp_path / "tb.f")], elaboration="onSave")
    try:
        status = c.wait_for("uvm/elaborationStatus", finished)["params"]
        *_, msg = [m for m in c.notifications if m["method"] == "textDocument/publishDiagnostics"
                   and m["params"]["uri"].endswith("/top.sv")]
        shown = [(d["code"], d["severity"]) for d in msg["params"]["diagnostics"]]
        assert shown == [("CouldNotOpenIncludeFile", 1)]
        assert (status["errors"], status["warnings"]) == (1, 0)
    finally:
        c.close()


def test_reverted_buffer_is_elaborated_again(client):
    a, b = uri("agent/alu_driver.svh"), uri("agent/alu_monitor.svh")
    with open(os.path.join(EXAMPLE, "agent/alu_driver.svh")) as f:
        ta = f.read()
    with open(os.path.join(EXAMPLE, "agent/alu_monitor.svh")) as f:
        tb = f.read()
    for u, t in ((a, ta), (b, tb)):
        client.notify("textDocument/didOpen", {"textDocument": {
            "uri": u, "languageId": "systemverilog", "version": 1, "text": t}})
    client.notify("textDocument/didChange", {"textDocument": {"uri": a, "version": 2},
                                             "contentChanges": [{"text": ta.replace("m_cfg.vif", "m_cfg.vfi")}]})
    client.forget("uvm/elaborationStatus")
    client.notify("textDocument/didSave", {"textDocument": {"uri": b}})  # compiles a's unsaved text
    assert client.wait_for("uvm/elaborationStatus", finished)["params"]["errors"] == 1
    client.forget("uvm/elaborationStatus")
    client.notify("textDocument/didChange", {"textDocument": {"uri": a, "version": 3},
                                             "contentChanges": [{"text": ta}]})  # File: Revert, no save
    status = client.wait_for("uvm/elaborationStatus", finished, timeout=10)["params"]
    for u in (a, b):
        client.notify("textDocument/didClose", {"textDocument": {"uri": u}})
    assert status["errors"] == 0


def test_open_file_changed_by_another_program_is_elaborated(tmp_path):
    ex = tmp_path / "alu_tb"
    shutil.copytree(EXAMPLE, ex)
    c = start(ex, filelists=[str(ex / "alu_tb.f")])
    try:
        assert c.wait_for("uvm/elaborationStatus", finished)["params"]["errors"] == 0
        path = ex / "agent" / "alu_driver.svh"
        u = f"file://{path}"
        text = path.read_text()
        c.notify("textDocument/didOpen", {"textDocument": {
            "uri": u, "languageId": "systemverilog", "version": 1, "text": text}})
        broken = text.replace("m_cfg.vif", "m_cfg.vfi")
        path.write_text(broken)  # git checkout, a generator, ...
        c.forget("uvm/elaborationStatus")
        # VS Code reloads the unmodified editor and its watcher reports the change; no didSave.
        c.notify("textDocument/didChange", {"textDocument": {"uri": u, "version": 2},
                                            "contentChanges": [{"text": broken}]})
        c.notify("workspace/didChangeWatchedFiles", {"changes": [{"uri": u, "type": 2}]})
        assert c.wait_for("uvm/elaborationStatus", finished, timeout=10)["params"]["errors"] == 1
    finally:
        c.close()


def test_elaboration_off_results_do_not_outlive_a_fix(client):
    u = uri("agent/alu_driver.svh")
    with open(os.path.join(EXAMPLE, "agent/alu_driver.svh")) as f:
        text = f.read()
    client.forget("uvm/elaborationStatus", "textDocument/publishDiagnostics")
    client.notify("workspace/didChangeConfiguration",
                  {"settings": {"uvm": {"filelists": [FILELIST], "elaboration": "off"}}})
    assert client.wait_for("uvm/elaborationStatus")["params"]["state"] == "off"
    client.notify("textDocument/didOpen", {"textDocument": {
        "uri": u, "languageId": "systemverilog", "version": 1, "text": text.replace("m_cfg.vif", "m_cfg.vfi")}})
    assert client.request("uvm/elaborate", {})["errors"] == 1
    client.wait_for("textDocument/publishDiagnostics", lambda m: m["params"]["uri"] == u and elab_diags(m))
    client.forget("uvm/elaborationStatus", "textDocument/publishDiagnostics")
    client.notify("textDocument/didChange", {"textDocument": {"uri": u, "version": 2},
                                             "contentChanges": [{"text": text}]})
    client.notify("textDocument/didSave", {"textDocument": {"uri": u}})
    status = client.wait_for("uvm/elaborationStatus", timeout=10)["params"]
    assert (status["state"], status["message"]) == ("off", "files changed since Elaborate Now")
    client.wait_for("textDocument/publishDiagnostics", lambda m: m["params"]["uri"] == u and not elab_diags(m))
    client.notify("textDocument/didClose", {"textDocument": {"uri": u}})
    client.forget("uvm/elaborationStatus", "textDocument/publishDiagnostics")
    client.notify("workspace/didChangeConfiguration",
                  {"settings": {"uvm": {"filelists": [FILELIST], "elaboration": "onSave"}}})
    assert client.wait_for("uvm/elaborationStatus", finished)["params"]["errors"] == 0


def set_elaboration(client, mode):
    client.notify("workspace/didChangeConfiguration",
                  {"settings": {"uvm": {"filelists": [FILELIST], "elaboration": mode}}})


def test_an_edit_in_on_change_mode_supersedes_a_run_in_progress(client):
    u = uri("agent/alu_driver.svh")
    with open(os.path.join(EXAMPLE, "agent/alu_driver.svh")) as f:
        text = f.read()
    v2 = text.replace("m_cfg.vif", "m_cfg.vfi")
    line = v2[:v2.index("m_cfg.vfi")].count("\n")
    set_elaboration(client, "onChange")
    client.notify("textDocument/didOpen", {"textDocument": {
        "uri": u, "languageId": "systemverilog", "version": 1, "text": text}})
    client.forget("uvm/elaborationStatus", "textDocument/publishDiagnostics")
    client.notify("textDocument/didChange", {"textDocument": {"uri": u, "version": 2},
                                             "contentChanges": [{"text": v2}]})
    client.wait_for("uvm/elaborationStatus", lambda m: m["params"]["state"] == "running")
    client.notify("textDocument/didChange", {"textDocument": {"uri": u, "version": 3},
                                             "contentChanges": [{"text": "//\n//\n//\n" + v2}]})
    assert client.wait_for("uvm/elaborationStatus", finished)["params"]["errors"] == 1
    time.sleep(1.5)
    client.request("uvm/stats", {})  # collects whatever else the server sent
    # The run on the older text was stopped: one result, for the text the editor has.
    assert not [m for m in client.notifications if m["method"] == "uvm/elaborationStatus" and finished(m)]
    *_, msg = [m for m in client.notifications
               if m["method"] == "textDocument/publishDiagnostics" and m["params"]["uri"] == u]
    assert [d["range"]["start"]["line"] for d in elab_diags(msg)] == [line + 3]
    client.forget("uvm/elaborationStatus")
    client.notify("textDocument/didChange", {"textDocument": {"uri": u, "version": 4},
                                             "contentChanges": [{"text": text}]})
    assert client.wait_for("uvm/elaborationStatus", finished)["params"]["errors"] == 0
    set_elaboration(client, "onSave")
    client.notify("textDocument/didClose", {"textDocument": {"uri": u}})


def test_auto_save_after_an_on_change_run_does_not_elaborate_again(client):
    u = uri("agent/alu_driver.svh")
    with open(os.path.join(EXAMPLE, "agent/alu_driver.svh")) as f:
        text = f.read()
    set_elaboration(client, "onChange")
    client.notify("textDocument/didOpen", {"textDocument": {
        "uri": u, "languageId": "systemverilog", "version": 1, "text": text}})
    client.forget("uvm/elaborationStatus")
    client.notify("textDocument/didChange", {"textDocument": {"uri": u, "version": 2},
                                             "contentChanges": [{"text": text.replace("m_cfg.vif", "m_cfg.vfi")}]})
    client.wait_for("uvm/elaborationStatus", lambda m: m["params"]["state"] == "running")
    client.notify("textDocument/didSave", {"textDocument": {"uri": u}})  # files.autoSave afterDelay
    assert client.wait_for("uvm/elaborationStatus", finished)["params"]["errors"] == 1
    time.sleep(1)
    client.request("uvm/stats", {})  # collects whatever else the server sent
    assert not [m for m in client.notifications if m["method"] == "uvm/elaborationStatus"]
    # A save of other text than the run compiled still elaborates.
    client.notify("textDocument/didChange", {"textDocument": {"uri": u, "version": 3},
                                             "contentChanges": [{"text": text}]})
    client.notify("textDocument/didSave", {"textDocument": {"uri": u}})  # before the onChange delay
    client.wait_for("uvm/elaborationStatus", lambda m: m["params"]["state"] == "running", timeout=0.8)
    assert client.wait_for("uvm/elaborationStatus", finished)["params"]["errors"] == 0
    set_elaboration(client, "onSave")
    client.notify("textDocument/didClose", {"textDocument": {"uri": u}})


def test_elaborate_now_answers_with_the_run_that_superseded_it(client):
    u = uri("agent/alu_driver.svh")
    with open(os.path.join(EXAMPLE, "agent/alu_driver.svh")) as f:
        text = f.read()
    set_elaboration(client, "onChange")
    client.notify("textDocument/didOpen", {"textDocument": {
        "uri": u, "languageId": "systemverilog", "version": 1, "text": text}})
    client.forget("uvm/elaborationStatus")
    client.next_id += 1
    rid = client.next_id
    client.send({"jsonrpc": "2.0", "id": rid, "method": "uvm/elaborate", "params": {}})
    client.wait_for("uvm/elaborationStatus", lambda m: m["params"]["state"] == "running")
    client.notify("textDocument/didChange", {"textDocument": {"uri": u, "version": 2},
                                             "contentChanges": [{"text": text.replace("m_cfg.vif", "m_cfg.vfi")}]})
    while True:
        msg = client.read()
        if msg.get("id") == rid:
            break
        if "method" in msg:
            client.notifications.append(msg)
    assert (msg["result"]["state"], msg["result"]["errors"]) == ("done", 1)
    client.forget("uvm/elaborationStatus")
    client.notify("textDocument/didChange", {"textDocument": {"uri": u, "version": 3},
                                             "contentChanges": [{"text": text}]})
    assert client.wait_for("uvm/elaborationStatus", finished)["params"]["errors"] == 0
    set_elaboration(client, "onSave")
    client.notify("textDocument/didClose", {"textDocument": {"uri": u}})


def test_cancelling_elaborate_does_not_stop_a_run_others_wait_on(client):
    client.forget("uvm/elaborationStatus")
    ids = []
    for _ in range(2):  # the second supersedes the first, so both wait on the second run
        client.next_id += 1
        ids.append(client.next_id)
        client.send({"jsonrpc": "2.0", "id": client.next_id, "method": "uvm/elaborate", "params": {}})
    for _ in range(2):
        client.wait_for("uvm/elaborationStatus", lambda m: m["params"]["state"] == "running")
    client.notify("$/cancelRequest", {"id": ids[0]})
    results = {}
    while len(results) < 2:
        msg = client.read()
        if msg.get("id") in ids and "method" not in msg:
            results[msg["id"]] = msg
        elif "method" in msg:
            client.notifications.append(msg)
    assert "error" not in results[ids[1]], results[ids[1]]
    assert results[ids[1]]["result"]["state"] == "done"


def _alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):  # gone before the open, or before the read
        return False


def _cpu_seconds(pid):
    with open(f"/proc/{pid}/stat") as f:
        fields = f.read().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_the_worker_ends_with_the_server(tmp_path):
    """A server that is killed or crashes runs no shutdown code; its worker must not outlive it."""
    spin = ["package p;", "  function automatic int spin(int s);", "    int a = s;",
            "    for (int i = 0; i < 300000; i++) a = a * 1103515245 + 12345;", "    return a;",
            "  endfunction", "endpackage", "module top;", "  import p::*;"]
    (tmp_path / "top.sv").write_text("\n".join(spin + [f"  localparam int P{i} = spin({i});" for i in range(100)]
                                               + ["endmodule", ""]))
    (tmp_path / "top.f").write_text("top.sv\n")
    c = start(tmp_path, filelists=[str(tmp_path / "top.f")], elaboration="onSave")
    worker = None
    try:
        c.wait_for("uvm/elaborationStatus", lambda m: m["params"]["state"] == "running")
        deadline = time.time() + 5
        while worker is None and time.time() < deadline:
            with open(f"/proc/{c.proc.pid}/task/{c.proc.pid}/children") as f:
                for pid in map(int, f.read().split()):
                    with open(f"/proc/{pid}/cmdline", "rb") as g:
                        if b"elab-worker" in g.read():
                            worker = pid
        assert worker, "no elaboration worker"
        while _cpu_seconds(worker) < 0.5 and time.time() < deadline + 5:  # it has its request
            time.sleep(0.05)
        c.proc.kill()  # as a crash or a plain kill: no shutdown, no exit
        c.proc.wait()
        deadline = time.time() + 3
        while _alive(worker) and time.time() < deadline:
            time.sleep(0.05)
        assert not _alive(worker), "the elaboration worker outlived the server"
    finally:
        if c.proc.poll() is None:
            c.proc.kill()
        if worker and _alive(worker):
            os.kill(worker, 9)
