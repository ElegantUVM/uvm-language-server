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


@pytest.fixture(scope="module")
def client():
    c = Client()
    c.request("initialize", {
        "processId": None, "rootUri": "file://" + EXAMPLE, "capabilities": {},
        "workspaceFolders": [{"uri": "file://" + EXAMPLE, "name": "alu_tb"}],
        "initializationOptions": {"settings": {"filelists": [FILELIST]}},
    })
    c.notify("initialized", {})
    c.wait_for("uvm/indexChanged")
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

    stats = configure("builtin", useBundledUvm=False)
    assert stats["libraryClasses"] == 0 and stats["classes"] == 19
    assert configure("bundled")["libraryClasses"] > 300


def test_document_symbols_and_type_hierarchy(client):
    syms = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri("agent/alu_driver.svh")}})
    assert [s["name"] for s in syms][0] == "alu_driver"
    items = client.request("textDocument/prepareTypeHierarchy", {
        "textDocument": {"uri": uri("agent/alu_driver.svh")}, "position": {"line": 1, "character": 8}})
    subs = client.request("typeHierarchy/subtypes", {"item": items[0]})
    assert [s["name"] for s in subs] == ["alu_error_driver"]
