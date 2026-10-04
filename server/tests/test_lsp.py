"""Drive the real server process over stdio JSON-RPC, the way VS Code does."""

import json
import os
import subprocess
import sys
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

    def send(self, msg):
        body = json.dumps(msg).encode()
        self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.proc.stdin.flush()

    def read(self):
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
        return json.loads(self.proc.stdout.read(int(headers["content-length"])))

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
            msg = self.read()
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
