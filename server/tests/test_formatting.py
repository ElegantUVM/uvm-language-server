"""Formatting through stdio with a recording executable, without requiring Verible."""

import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from test_code_actions import Doc, applied
from test_lsp import Client
from uvmls.project import BUNDLED_UVM

CAPABILITIES = {"textDocument": {"formatting": {}}}


class Formatter:
    def __init__(self, root, name="verible-verilog-format"):
        self.path = root / name
        self.record = root / (name + ".json")
        self.control = root / (name + ".control")
        self.set()
        self.path.write_text(
            f"#!{sys._base_executable}\n"
            "import json, os, pathlib, sys, time\n"
            f"cfg = json.loads(pathlib.Path({str(self.control)!r}).read_text())\n"
            "source = sys.stdin.buffer.read()\n"
            f"pathlib.Path({str(self.record)!r}).write_text(json.dumps({{\n"
            "    'argv': sys.argv[1:], 'stdin': source.decode('utf-8'),\n"
            "    'cwd': os.getcwd(), 'pid': os.getpid()}))\n"
            "sys.stderr.write(cfg.get('stderr', ''))\n"
            "sys.stderr.flush()\n"
            "time.sleep(cfg.get('delay', 0))\n"
            "out = cfg.get('output')\n"
            "sys.stdout.buffer.write(bytes(out) if isinstance(out, list) else\n"
            "    out.encode('utf-8') if out is not None else source.replace(b'BAD', b'good'))\n"
            "sys.exit(cfg.get('exit', 0))\n", encoding="utf-8")
        self.path.chmod(0o755)

    def set(self, **kwargs):
        self.control.write_text(json.dumps(kwargs))

    def called(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                return json.loads(self.record.read_text())
            except (OSError, ValueError):
                time.sleep(0.01)
        pytest.fail("formatter was not started")


@pytest.fixture
def fake(tmp_path):
    return Formatter(tmp_path)


@pytest.fixture
def clients():
    opened = []

    def start(root, formatter=None, capabilities=CAPABILITIES, initialize_index=False):
        c = Client()
        opened.append(c)
        c.settings = {"elaboration": "off", "useBundledUvm": False}
        if formatter is not None:
            c.settings["formatter"] = formatter
        params = {"processId": None, "capabilities": capabilities,
                  "initializationOptions": {"settings": c.settings}}
        if root is not None:
            params["rootUri"] = root.as_uri()
        c.initial = c.request("initialize", params)
        if initialize_index:
            c.notify("initialized", {})
            c.wait_for("uvm/indexChanged")
        return c

    yield start
    for c in opened:
        if c.proc.poll() is None:
            c.close()


def document(c, root, text="// BAD\n", name="t.sv"):
    path = root / name
    path.write_bytes(text.encode("utf-8"))
    return Doc(c, str(path))


def params(doc, **options):
    return {"textDocument": {"uri": doc.uri}, "options": {"tabSize": 2, "insertSpaces": True, **options}}


def format_doc(doc, **options):
    return doc.client.request("textDocument/formatting", params(doc, **options))


def pending(doc, method="textDocument/formatting", **extra):
    c = doc.client
    c.next_id += 1
    c.send({"jsonrpc": "2.0", "id": c.next_id, "method": method, "params": {**params(doc), **extra}})
    return c.next_id


def response(c, rid):
    while True:
        msg = c.read()
        if msg.get("id") == rid:
            return msg
        if "method" in msg:
            c.notifications.append(msg)


def dead(pid):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)
    pytest.fail(f"formatter child {pid} is still alive or unreaped")


@pytest.mark.parametrize("capability", [None, {}, {"dynamicRegistration": False}])
def test_document_capability(clients, tmp_path, capability):
    caps = {} if capability is None else {"textDocument": {"formatting": capability}}
    c = clients(tmp_path, capabilities=caps)
    assert bool(c.initial["capabilities"].get("documentFormattingProvider")) == (capability is not None)


@pytest.mark.parametrize("ending", ["", "\n", "\r\n"])
def test_unsaved_buffer_and_actual_end(clients, fake, tmp_path, ending):
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path, name="é space.sv")
    original = Path(doc.path).read_bytes()
    doc.change("// 😀 BAD" + ending)
    fake.set(output="// 😀 good\n")
    edits = format_doc(doc, tabSize=4, insertSpaces=False, trimTrailingWhitespace=True)
    assert applied(doc.text, edits) == "// 😀 good" + ending
    assert edits[0]["range"] == {"start": {"line": 0, "character": 0}, "end": {
        "line": 1 if ending else 0, "character": 0 if ending else 9}}
    record = fake.called()
    assert record["stdin"] == doc.text
    assert record["argv"] == ["--failsafe_success=false", "--indentation_spaces=4", "-"]
    assert record["cwd"] == str(tmp_path)
    assert Path(doc.path).read_bytes() == original


def test_utf8_positions(clients, fake, tmp_path):
    caps = {**CAPABILITIES, "general": {"positionEncodings": ["utf-8"]}}
    c = clients(tmp_path, {"path": str(fake.path)}, caps)
    doc = document(c, tmp_path, "// 😀 BAD")
    [edit] = format_doc(doc)
    assert edit["range"]["end"] == {"line": 0, "character": len(doc.text.encode("utf-8"))}


@pytest.mark.parametrize("text", ["// good\n", "// good", "// good\r\n"])
def test_unchanged_and_unopened(clients, fake, tmp_path, text):
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path, text)
    doc.close()
    assert format_doc(doc) == []
    assert fake.called()["stdin"] == text


@pytest.mark.parametrize("text", ["", "// BAD\n// mixed\r\n", "// BAD\r"])
def test_empty_or_unsupported_endings_do_not_spawn(clients, fake, tmp_path, text):
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path, text)
    assert format_doc(doc) == []
    assert not fake.record.exists()
    if text:
        c.wait_for("window/logMessage", lambda m: "line ending" in m["params"]["message"])


@pytest.mark.parametrize("path_setting", ["", "verible-verilog-format", "./verible-verilog-format",
                                         "${workspaceFolder}/verible-verilog-format", "$FORMATTER_BIN", "~/verible-verilog-format"])
def test_discovery_and_expansion(clients, fake, tmp_path, monkeypatch, path_setting):
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FORMATTER_BIN", str(fake.path))
    if path_setting.startswith("~/"):
        path_setting = "~/" + os.path.relpath(fake.path, os.path.expanduser("~"))
    c = clients(tmp_path, {"path": path_setting})
    doc = document(c, tmp_path)
    assert applied(doc.text, format_doc(doc)) == "// good\n"


def test_explicit_path_is_one_executable_and_wins(clients, fake, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    other = tmp_path / "tools with spaces"
    other.mkdir()
    selected = Formatter(other, "format;$(touch INJECTED)")
    selected.set(output="// explicit\n")
    c = clients(tmp_path, {"path": str(selected.path)})
    doc = document(c, tmp_path)
    assert applied(doc.text, format_doc(doc)) == "// explicit\n"
    assert not fake.record.exists() and not (tmp_path / "INJECTED").exists()


@pytest.mark.parametrize("kind", ["missing", "not_executable", "relative_without_root", "command"])
def test_bad_explicit_path_does_not_fall_back(clients, fake, tmp_path, monkeypatch, kind):
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    path = tmp_path / "other"
    if kind == "not_executable":
        path.write_text("no")
    setting = "./verible-verilog-format" if kind == "relative_without_root" else str(path)
    if kind == "command":
        setting = str(fake.path) + " --extra"
    c = clients(None if kind == "relative_without_root" else tmp_path, {"path": setting})
    doc = document(c, tmp_path)
    assert format_doc(doc) == format_doc(doc) == []
    logs = [m for m in c.notifications if m["method"] == "window/logMessage"]
    assert len(logs) == 1 and "uvm.formatter.path" in logs[0]["params"]["message"]
    assert not fake.record.exists()


@pytest.mark.parametrize("cfg,reason", [({"exit": 1, "output": "delete", "stderr": "failure"}, "status 1"),
                                       ({"output": [255]}, "UTF-8"), ({"output": ""}, "empty output")])
def test_failure_discards_stdout(clients, fake, tmp_path, cfg, reason):
    fake.set(**cfg)
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path)
    assert format_doc(doc) == []
    msg = c.wait_for("window/logMessage", lambda m: reason in m["params"]["message"])
    assert str(fake.path) in msg["params"]["message"]
    assert Path(doc.path).read_text() == doc.text


def test_timeout_kills_and_reaps(clients, fake, tmp_path):
    fake.set(delay=30, stderr="too slow " * 1000)
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path)
    start = time.monotonic()
    assert format_doc(doc) == []
    assert 4.5 < time.monotonic() - start < 10
    dead(fake.called()["pid"])
    msg = c.wait_for("window/logMessage", lambda m: "5 s" in m["params"]["message"])
    assert "too slow" in msg["params"]["message"] and len(msg["params"]["message"]) < 5000


def test_cancellation_and_responsiveness(clients, fake, tmp_path):
    fake.set(delay=30)
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path)
    rid = pending(doc)
    pid = fake.called()["pid"]
    start = time.monotonic()
    assert "classes" in c.request("uvm/stats", {})
    assert time.monotonic() - start < 1
    c.notify("$/cancelRequest", {"id": rid})
    assert response(c, rid)["error"]["code"] == -32800
    dead(pid)


def test_shutdown_cleans_up(clients, fake, tmp_path):
    fake.set(delay=30)
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path)
    pending(doc)
    pid = fake.called()["pid"]
    c.close()
    dead(pid)


@pytest.mark.parametrize("change", ["edit", "close", "reopen", "settings"])
def test_stale_output_is_discarded(clients, fake, tmp_path, change):
    fake.set(delay=0.5)
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path)
    rid = pending(doc)
    fake.called()
    if change == "edit":
        doc.change(doc.text + "// newer\n")
    elif change in ("close", "reopen"):
        doc.close()
        if change == "reopen":
            Doc(c, doc.path)
    else:
        c.notify("workspace/didChangeConfiguration", {"settings": {
            **c.settings, "formatter": {"path": str(fake.path) + "other"}}})
    assert response(c, rid)["result"] == []


@pytest.mark.parametrize("uri", ["untitled:t.sv", "https://example.invalid/t.sv", "file://remote/t.sv", None])
def test_ineligible_documents(clients, fake, tmp_path, uri):
    c = clients(tmp_path, {"path": str(fake.path)})
    if uri is None:
        uri = Path(BUNDLED_UVM, "uvm_pkg.sv").as_uri()
    assert c.request("textDocument/formatting", {
        "textDocument": {"uri": uri}, "options": {"tabSize": 2, "insertSpaces": True}}) == []
    assert not fake.record.exists()


def test_zero_indentation_does_not_spawn(clients, fake, tmp_path):
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path)
    assert format_doc(doc, tabSize=0) == []
    assert not fake.record.exists()


def test_missing_binary_is_retried_without_reindexing(clients, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    c = clients(tmp_path)
    doc = document(c, tmp_path)
    assert format_doc(doc) == format_doc(doc) == []
    logs = [m for m in c.notifications if m["method"] == "window/logMessage"]
    assert len(logs) == 1 and "uvm.formatter.path" in logs[0]["params"]["message"]
    fake = Formatter(tmp_path)
    assert applied(doc.text, format_doc(doc)) == "// good\n"
    fake.called()


def test_spawn_failure(clients, fake, tmp_path):
    fake.path.write_text("#!/nonexistent/interpreter\n")
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path)
    assert format_doc(doc) == []
    c.wait_for("window/logMessage", lambda m: "cannot start" in m["params"]["message"])


def test_library_and_language_filter_without_index(clients, fake, tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "uvm_pkg.sv").write_text("// BAD\n")
    c = clients(tmp_path, {"path": str(fake.path)})
    c.notify("workspace/didChangeConfiguration", {"settings": {**c.settings, "uvmHome": str(library)}})
    doc = document(c, library)
    assert format_doc(doc) == []
    doc = document(c, tmp_path, name="notes.txt")
    doc.close()
    c.notify("textDocument/didOpen", {"textDocument": {
        "uri": doc.uri, "languageId": "plaintext", "version": 2, "text": doc.text}})
    assert format_doc(doc) == []
    assert not fake.record.exists()
