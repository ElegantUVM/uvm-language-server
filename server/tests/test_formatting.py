"""Formatting through stdio with a recording executable, without requiring Verible."""

import asyncio
import json
import os
import platform
import signal
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from test_code_actions import Doc, applied
from test_lsp import Client
from conftest import EXAMPLE
from uvmls import formatting
from uvmls.indexer import SHIM_FILE
from uvmls.project import BUNDLED_UVM, norm

CAPABILITIES = {"textDocument": {"formatting": {}, "rangeFormatting": {}}}


class Formatter:
    def __init__(self, root, name="verible-verilog-format"):
        self.path = root / (name + ".exe" if os.name == "nt" else name)
        self.record = root / (name + ".json")
        self.control = root / (name + ".control")
        self.set()
        script = (
            f"#!{sys._base_executable}\n"
            "import json, os, pathlib, subprocess, sys, time\n"
            f"cfg = json.loads(pathlib.Path({str(self.control)!r}).read_text())\n"
            "source = sys.stdin.buffer.read()\n"
            "child = None\n"
            "if cfg.get('child'):\n"
            "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'],\n"
            "        start_new_session=cfg.get('detached', False))\n"
            f"pathlib.Path({str(self.record)!r}).write_text(json.dumps({{\n"
            "    'argv': sys.argv[1:], 'stdin': source.decode('utf-8'),\n"
            "    'cwd': os.getcwd(), 'pid': os.getpid(), 'child': child.pid if child else None}))\n"
            "sys.stderr.write(cfg.get('stderr', ''))\n"
            "sys.stderr.flush()\n"
            "time.sleep(cfg.get('delay', 0))\n"
            "out = cfg.get('output')\n"
            "sys.stdout.buffer.write(bytes(out) if isinstance(out, list) else\n"
            "    out.encode('utf-8') if out is not None else source.replace(b'BAD', b'good'))\n"
            "sys.exit(cfg.get('exit', 0))\n")
        if os.name == "nt":
            # pip's launcher runs the same recording script as a real executable, without a shell.
            from pip._vendor.distlib.scripts import ScriptMaker
            source = root / "formatter-source"
            source.mkdir(exist_ok=True)
            source.joinpath(name).write_text("#!python\n" + script.split("\n", 1)[1], encoding="utf-8")
            maker = ScriptMaker(str(source), str(root))
            maker.executable = sys.executable
            maker.make(name)
        else:
            self.path.write_text(script, encoding="utf-8")
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

    def start(root, formatter=None, capabilities=CAPABILITIES, initialize_index=False, **settings):
        c = Client()
        opened.append(c)
        c.settings = {"elaboration": "off", "useBundledUvm": False, **settings}
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


def dead(pid, reaped=True):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if os.name == "nt":
            # os.kill(pid, 0) terminates a process on Windows; query its wait handle instead.
            import _winapi
            try:
                handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, pid)
            except OSError:
                return
            try:
                if _winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_OBJECT_0:
                    return
            finally:
                _winapi.CloseHandle(handle)
            time.sleep(0.01)
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        if not reaped:
            try:
                if Path(f"/proc/{pid}/stat").read_text().split(")", 1)[1].split()[0] == "Z":
                    return  # Orphaned descendants are reaped by the system, not the server.
            except OSError:
                pass
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
    assert norm(record["cwd"]) == norm(str(tmp_path))
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
                                         "${workspaceFolder}/verible-verilog-format", "$FORMATTER_BIN",
                                         "$REL_FORMATTER_BIN", "~/verible-verilog-format"])
def test_discovery_and_expansion(clients, fake, tmp_path, monkeypatch, path_setting):
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FORMATTER_BIN", str(fake.path))
    monkeypatch.setenv("REL_FORMATTER_BIN", "./" + fake.path.name)
    if os.name == "nt":
        path_setting = path_setting.replace("verible-verilog-format", fake.path.name)
    if path_setting == "$REL_FORMATTER_BIN":
        monkeypatch.setenv("PATH", "")
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


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
@pytest.mark.parametrize("ranged", [False, True])
@pytest.mark.parametrize("detached", [False, True])
@pytest.mark.parametrize("action", ["timeout", "cancel", "shutdown", "parent_exit"])
def test_descendants_cannot_hold_formatter_pipes(clients, fake, tmp_path, ranged, detached, action):
    fake.set(child=True, detached=detached, delay=0 if action == "parent_exit" else 8,
             stderr="descendant holds pipes")
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path)
    start = time.monotonic()
    rid = pending(doc, "textDocument/rangeFormatting", range=selection((0, 0), (1, 0))) if ranged else pending(doc)
    record = fake.called()
    try:
        if action == "cancel":
            start = time.monotonic()
            c.notify("$/cancelRequest", {"id": rid})
            assert response(c, rid)["error"]["code"] == -32800
        elif action == "shutdown":
            start = time.monotonic()
            c.request("shutdown", None)
            c.notify("exit", None)
        else:
            assert response(c, rid)["result"] == []
            c.wait_for("window/logMessage", lambda m: "5 s" in m["params"]["message"])
        assert time.monotonic() - start < (7 if action in ("timeout", "parent_exit") else 2.5)
        if action == "shutdown":
            # Well before the descendant's 8 s; how long the interpreter takes to exit depends on load.
            c.proc.wait(timeout=5)
        dead(record["pid"])
        if not detached:
            dead(record["child"], reaped=False)
        assert Path(doc.path).read_text() == doc.text
    finally:
        for pid in (record["pid"], record["child"]):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


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


@pytest.mark.parametrize("bundled", [False, True])
def test_macro_shim_is_library(clients, fake, tmp_path, bundled):
    (tmp_path / "t.sv").write_text('`include "uvm_macros.svh"\nmodule m; endmodule\n')
    c = clients(tmp_path, {"path": str(fake.path)}, initialize_index=True, useBundledUvm=bundled)
    shim = Doc(c, SHIM_FILE)
    assert format_doc(shim) == format_range(shim, (0, 0), (1, 0)) == []
    assert not fake.record.exists()


@pytest.mark.parametrize("ranged", [False, True])
def test_lone_surrogate_does_not_spawn(clients, fake, tmp_path, ranged):
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path)
    doc.change('// BAD\n// "\ud83d"\n')  # JSON carries it as the escape \ud83d
    assert (format_range(doc, (0, 0), (1, 0)) if ranged else format_doc(doc)) == []
    c.wait_for("window/logMessage", lambda m: "line 2 holds a lone UTF-16 surrogate" in m["params"]["message"])
    assert not fake.record.exists()
    doc.change("// BAD\n")
    assert applied(doc.text, format_doc(doc)) == "// good\n"


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


@pytest.fixture
def bundled(tmp_path, monkeypatch):
    """A runnable fake in the host's bundled layout, with an empty PATH."""
    target = {"win32": "win32-x64", "darwin": "darwin-arm64"}.get(sys.platform, "linux-x64")
    (tmp_path / "bundle" / target).mkdir(parents=True)
    copy = Formatter(tmp_path / "bundle" / target)
    copy.set(output="// bundled\n")
    monkeypatch.setattr(formatting, "BUNDLED_ROOT", str(tmp_path / "bundle"))
    machine = {"win32-x64": "AMD64", "darwin-arm64": "arm64"}.get(target, "x86_64")
    monkeypatch.setattr(platform, "machine", lambda: machine)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    return copy


def test_lookup_order(bundled, fake, tmp_path, monkeypatch):
    roots = [str(tmp_path)]
    assert formatting.executable("", roots) == norm(str(bundled.path))
    monkeypatch.setenv("PATH", str(tmp_path))  # fake is verible-verilog-format in tmp_path
    assert formatting.executable("", roots) == norm(str(fake.path))
    (tmp_path / "elsewhere").mkdir()
    explicit = Formatter(tmp_path / "elsewhere", "mine")
    assert formatting.executable(str(explicit.path), roots) == str(explicit.path)
    assert formatting.executable("mine", roots) is None  # not on PATH: no fallback
    assert formatting.executable(str(tmp_path / "missing"), roots) is None
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    assert formatting.executable(str(tmp_path / "missing"), roots) is None
    assert formatting.executable("verible-verilog-format", roots) is None


@pytest.mark.parametrize("system,machine", [("darwin", "i686"), ("win32", "ARM64"), ("linux", "aarch64"),
                                            ("linux", "i686")])
def test_bundled_copy_rejects_unsupported_platforms(bundled, tmp_path, monkeypatch, system, machine):
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: None)  # which() is platform-specific
    assert formatting.executable("", [str(tmp_path)]) is None


def test_bundled_copy_must_exist(bundled, tmp_path):
    bundled.path.unlink()
    assert formatting.executable("", [str(tmp_path)]) is None
    bundled.path.mkdir()
    assert formatting.executable("", [str(tmp_path)]) is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX execute permission bits")
def test_bundled_copy_exec_bit_is_repaired(bundled, tmp_path, monkeypatch):
    bundled.path.chmod(0o644)
    assert formatting.executable("", [str(tmp_path)]) == str(bundled.path)
    assert bundled.path.stat().st_mode & 0o755 == 0o755
    bundled.path.chmod(0o644)

    def refused(path, mode):
        raise PermissionError(path)

    monkeypatch.setattr(os, "chmod", refused)
    assert formatting.executable("", [str(tmp_path)]) is None
    assert bundled.path.stat().st_mode & 0o111 == 0


def test_bundled_copy_runs_and_is_announced_once(bundled, fake, tmp_path, monkeypatch):
    logs = []
    formatter = formatting.Formatter(logs.append)
    settings = formatting.Settings()

    def run(text="// BAD\n"):
        return asyncio.run(formatter.run(text, str(tmp_path / "t.sv"), 2, settings, [str(tmp_path)]))

    assert run() == "// bundled\n"
    assert bundled.called()["argv"] == ["--failsafe_success=false", "--indentation_spaces=2", "-"]
    assert run("// BAD BAD\n") == "// bundled\n"
    assert logs == [f"formatter: using {norm(str(bundled.path))!r} (the bundled copy)"]
    monkeypatch.setenv("PATH", str(tmp_path))
    assert run() == "// good\n"
    assert logs[1:] == [f"formatter: using {norm(str(fake.path))!r} (PATH)"]
    assert asyncio.run(formatter.run("// BAD\n", str(tmp_path / "t.sv"), 2, formatting.Settings(path="/missing"),
                                     [str(tmp_path)])) is None
    assert len(logs) == 3 and "uvm.formatter.path" in logs[2]


def test_removed_directory_runs_in_nearest_existing_one(clients, fake, tmp_path):
    proj = tmp_path / "proj"
    (proj / "sub" / "deeper").mkdir(parents=True)
    c = clients(proj, {"path": str(fake.path)})
    doc = document(c, proj / "sub" / "deeper")
    shutil.rmtree(proj / "sub")
    doc.change("// BAD unsaved\n")
    assert applied(doc.text, format_doc(doc)) == "// good unsaved\n"
    assert norm(fake.called()["cwd"]) == norm(str(proj))
    assert not (proj / "sub").exists()


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


@pytest.mark.skipif(os.name != "posix", reason="FIFO holds source discovery open over stdio")
@pytest.mark.parametrize("ranged", [False, True])
def test_project_library_is_protected_during_discovery(clients, fake, tmp_path, ranged):
    library = tmp_path / "library"
    nested = library / "base"
    nested.mkdir(parents=True)
    (library / "uvm_pkg.sv").write_text("package uvm_pkg; endpackage\n")
    (library / "uvm_macros.svh").write_text("// macros\n")
    targets = [library / "lib.svh", nested / "nested.svh"]
    for target in targets:
        target.write_text("// BAD\n")
    alias = tmp_path / "alias.svh"
    alias.symlink_to(targets[1])
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "linked.svh").write_text("// BAD\n")
    (library / "linked.svh").symlink_to(storage / "linked.svh")
    targets += [alias, library / "linked.svh"]
    filelist = tmp_path / "tb.f"
    os.mkfifo(filelist)
    entered, release = threading.Event(), threading.Event()

    def hold_filelist():
        with filelist.open("w") as f:
            entered.set()
            release.wait(10)
            f.write("library/uvm_pkg.sv\nlibrary/lib.svh\n")

    writer = threading.Thread(target=hold_filelist, daemon=True)
    writer.start()
    c = clients(tmp_path, {"path": str(fake.path)}, filelists=[str(filelist)])
    c.notify("initialized", {})
    request = (lambda doc: format_range(doc, (0, 0), (1, 0))) if ranged else format_doc
    try:
        assert entered.wait(5)
        docs = [Doc(c, str(path)) for path in targets]
        for doc in docs:
            assert request(doc) == []
            assert Path(doc.path).read_text() == doc.text
        assert not fake.record.exists()
        user = document(c, tmp_path)
        assert applied(user.text, request(user)) == "// good\n"
        assert not any(m["method"] == "uvm/indexChanged" for m in c.notifications)
    finally:
        release.set()
        writer.join(timeout=5)
    c.wait_for("uvm/indexChanged")
    assert c.request("uvm/stats", {})["uvm"]["dir"] == str(library)
    fake.record.unlink()
    for doc in docs:
        assert request(doc) == []
    assert not fake.record.exists()


@pytest.mark.parametrize("ranged", [False, True])
@pytest.mark.parametrize("indexed", [False, True])
def test_library_files_through_links(clients, fake, tmp_path, ranged, indexed):
    uvm, store, proj = tmp_path / "uvm" / "src", tmp_path / "store", tmp_path / "proj"
    for d in (uvm, store / "base", proj):
        d.mkdir(parents=True)
    for name in ("component", "base/object.svh", "shared.svh"):
        (store / name).write_text("// BAD\n")
    (uvm / "uvm_component.svh").symlink_to(store / "component")
    (uvm / "base").symlink_to(store / "base", target_is_directory=True)
    (uvm / "uvm_macros.svh").write_text("// macros\n")
    (uvm / "uvm_pkg.sv").write_text('package uvm_pkg;\n`include "uvm_component.svh"\n'
                                    '`include "base/object.svh"\n`include "../../store/shared.svh"\nendpackage\n')
    (proj / "alias.svh").symlink_to(store / "shared.svh")
    c = clients(proj, {"path": str(fake.path)}, initialize_index=indexed, uvmHome=str(uvm.parent))
    targets = [uvm / "uvm_component.svh", uvm / "base" / "object.svh"]
    if indexed:
        assert c.request("uvm/stats", {})["uvm"]["source"] == "configured"
        # Only indexing finds a library include outside the library, reached through an alias.
        targets += [store / "shared.svh", proj / "alias.svh"]
    request = (lambda doc: format_range(doc, (0, 0), (1, 0))) if ranged else format_doc
    for target in targets:
        doc = Doc(c, str(target))
        assert request(doc) == []
        doc.close()
    assert not fake.record.exists()
    user = document(c, proj)
    assert applied(user.text, request(user)) == "// good\n"


@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize("layout", ["above_workspace", "unlisted_wrapper"])
def test_lone_uvm_pkg_ancestor_is_not_a_library(clients, fake, tmp_path, indexed, layout):
    proj = tmp_path / "work" / "proj"
    (proj / "tb").mkdir(parents=True)
    (proj / "tb" / "t.sv").write_text("// BAD\n")
    settings = {"useBundledUvm": True}
    if layout == "above_workspace":
        (tmp_path / "uvm_pkg.sv").write_text("package uvm_pkg; endpackage\n")
    else:
        (proj / "uvm_pkg.sv").write_text("package uvm_pkg; endpackage\n")
        (proj / "tb.f").write_text("tb/t.sv\n")
        settings["filelists"] = [str(proj / "tb.f")]
    c = clients(proj, {"path": str(fake.path)}, initialize_index=indexed, **settings)
    if indexed:
        stats = c.request("uvm/stats", {})
        assert stats["uvm"]["source"] == "bundled" and stats["files"] == 1
    doc = Doc(c, str(proj / "tb" / "t.sv"))
    assert applied(doc.text, format_doc(doc)) == "// good\n"
    assert applied(doc.text, format_range(doc, (0, 0), (1, 0))) == "// good\n"
    # A UVM source tree stays library code even where the project uses another UVM.
    unused = tmp_path / "uvm-1.1d" / "src"
    (unused / "base").mkdir(parents=True)
    for name in ("uvm_pkg.sv", "uvm_macros.svh", "base/uvm_object.svh"):
        (unused / name).write_text("// BAD\n")
    assert format_doc(Doc(c, str(unused / "base" / "uvm_object.svh"))) == []


def selection(start, end):
    return {"start": {"line": start[0], "character": start[1]},
            "end": {"line": end[0], "character": end[1]}}


def format_range(doc, start, end):
    return doc.client.request("textDocument/rangeFormatting", {**params(doc), "range": selection(start, end)})


@pytest.mark.parametrize("names", [[], ["formatting"], ["rangeFormatting"], ["formatting", "rangeFormatting"]])
def test_independent_formatter_capabilities(clients, tmp_path, names):
    c = clients(tmp_path, {"enabled": False, "path": "/missing"},
                {"textDocument": {name: {"dynamicRegistration": False} for name in names}})
    caps = c.initial["capabilities"]
    assert bool(caps.get("documentFormattingProvider")) == ("formatting" in names)
    assert bool(caps.get("documentRangeFormattingProvider")) == ("rangeFormatting" in names)


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
@pytest.mark.parametrize("start,end,lines,edit_range", [
    ((1, 3), (2, 0), "2-2", ((1, 0), (2, 0))),
    ((1, 3), (1, 3), "2-2", ((1, 0), (2, 0))),
    ((1, 3), (2, 1), "2-3", ((1, 0), (3, 0))),
    ((0, 0), (3, 0), "1-3", ((0, 0), (3, 0))),
])
def test_complete_touched_lines(clients, fake, tmp_path, ending, start, end, lines, edit_range):
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path, ending.join(["// before", "// 😀 BAD", "// after", ""]))
    original = Path(doc.path).read_bytes()
    edits = format_range(doc, start, end)
    assert edits == [{"range": selection(*edit_range), "newText": ending.join(
        doc.text.replace("BAD", "good").split(ending)[edit_range[0][0]:edit_range[1][0]]) + ending}]
    assert applied(doc.text, edits) == doc.text.replace("BAD", "good")
    record = fake.called()
    assert record["stdin"] == doc.text
    assert record["argv"] == ["--failsafe_success=false", "--indentation_spaces=2", "--lines=" + lines, "-"]
    assert Path(doc.path).read_bytes() == original


@pytest.mark.parametrize("newline,start,end,expected_end", [
    ("", (1, 5), (1, 9), (1, 9)),
    ("\n", (1, 5), (2, 0), (2, 0)),
    ("\n", (2, 0), (2, 0), (2, 0)),
])
def test_range_at_eof(clients, fake, tmp_path, newline, start, end, expected_end):
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path, "// before\n// 😀 BAD" + newline)
    edits = format_range(doc, start, end)
    assert edits[0]["range"] == selection((1, 0), expected_end)
    assert applied(doc.text, edits) == doc.text.replace("BAD", "good")
    assert "--lines=2-2" in fake.called()["argv"]


@pytest.mark.parametrize("start,end", [((0, 0), (5, 0)), ((1, 0), (0, 0)), ((0, 3), (0, 2)),
                                       ((1, 10), (1, 10)), ((2, 1), (2, 1)), ((1, 4), (1, 4))])
def test_invalid_range_does_not_spawn(clients, fake, tmp_path, start, end):
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path, "// before\n// 😀 BAD\n")
    assert format_range(doc, start, end) == []
    assert not fake.record.exists()


@pytest.mark.parametrize("output", ["// changed\n// good\n// after\n", "// before\n// good\n// changed\n",
                                    "// before\n// good\n"])
def test_range_never_clips_changes_outside_lines(clients, fake, tmp_path, output):
    fake.set(output=output)
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path, "// before\n// BAD\n// after\n")
    assert format_range(doc, (1, 0), (2, 0)) == []
    c.wait_for("window/logMessage", lambda m: "beyond the requested lines" in m["params"]["message"])


def test_range_can_change_line_count(clients, fake, tmp_path):
    fake.set(output="// before\n// good\n// added\n// after\n")
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path, "// before\n// BAD\n// after\n")
    edits = format_range(doc, (1, 0), (2, 0))
    assert edits == [{"range": selection((1, 0), (2, 0)), "newText": "// good\n// added\n"}]
    assert applied(doc.text, edits) == "// before\n// good\n// added\n// after\n"
    doc.change(applied(doc.text, edits))
    assert format_range(doc, (1, 0), (3, 0)) == []


def test_range_rejecting_lines_option_and_stale_result(clients, fake, tmp_path):
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path)
    fake.set(exit=1, stderr="unknown --lines", output="should not be used")
    assert format_range(doc, (0, 0), (1, 0)) == []
    c.wait_for("window/logMessage", lambda m: "unknown --lines" in m["params"]["message"])
    fake.record.unlink()
    fake.set(delay=0.5)
    rid = pending(doc, "textDocument/rangeFormatting", range=selection((0, 0), (1, 0)))
    fake.called()
    doc.change("// changed\n")
    assert response(c, rid)["result"] == []


def test_range_utf8_units(clients, fake, tmp_path):
    caps = {**CAPABILITIES, "general": {"positionEncodings": ["utf-8"]}}
    c = clients(tmp_path, {"path": str(fake.path)}, caps)
    doc = document(c, tmp_path, "// 😀 BAD")
    assert format_range(doc, (0, 4), (0, 4)) == []
    assert not fake.record.exists()
    [edit] = format_range(doc, (0, 7), (0, 7))
    assert edit["range"] == selection((0, 0), (0, len(doc.text.encode("utf-8"))))
    assert edit["newText"] == "// 😀 good"


def configure(c, **settings):
    c.settings = {**c.settings, **settings}
    c.notify("workspace/didChangeConfiguration", {"settings": {"uvm": c.settings}})


@pytest.mark.parametrize("mode", ["off", "onSave"])
def test_formatter_settings_do_not_index_or_elaborate(clients, fake, tmp_path, mode):
    (tmp_path / "t.sv").write_text("// BAD\n")
    c = clients(tmp_path, {"path": str(fake.path)}, initialize_index=True, elaboration=mode)
    if mode != "off":
        c.wait_for("uvm/elaborationStatus", lambda m: m["params"]["state"] != "running")
    doc = Doc(c, str(tmp_path / "t.sv"))
    c.wait_for("uvm/indexChanged")
    c.request("uvm/stats", {})
    c.forget("uvm/indexChanged", "uvm/elaborationStatus", "window/logMessage")
    for cfg in [{"enabled": False, "path": str(fake.path)}, {"enabled": False, "path": "/missing"}]:
        configure(c, formatter=cfg)
        assert format_doc(doc) == format_range(doc, (0, 0), (1, 0)) == []
        assert not fake.record.exists()
        assert not any(m["method"] == "window/logMessage" for m in c.notifications)
    configure(c, formatter={"enabled": True, "path": "/missing"})
    assert format_doc(doc) == []
    configure(c, formatter={"enabled": True, "path": str(fake.path)})
    assert applied(doc.text, format_doc(doc)) == "// good\n"
    for method in ("uvm/indexChanged", "uvm/elaborationStatus"):
        with pytest.raises(TimeoutError):
            c.wait_for(method, timeout=0.35)
    configure(c, defines={"CHANGED": "1"}, formatter={"enabled": False})
    c.wait_for("uvm/indexChanged")


def test_settings_change_back_during_range_discards_result(clients, fake, tmp_path):
    fake.set(delay=0.5)
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path)
    rid = pending(doc, "textDocument/rangeFormatting", range=selection((0, 0), (1, 0)))
    fake.called()
    configure(c, formatter={"path": str(fake.path), "enabled": False})
    configure(c, formatter={"path": str(fake.path)})
    assert response(c, rid)["result"] == []


def test_disabled_initialization_does_not_log_or_spawn(clients, fake, tmp_path):
    c = clients(tmp_path, {"enabled": False, "path": str(fake.path)})
    doc = document(c, tmp_path)
    assert format_doc(doc) == format_range(doc, (0, 0), (1, 0)) == []
    assert not fake.record.exists()
    assert not any(m["method"] == "window/logMessage" for m in c.notifications)


def test_unopened_disk_changes_during_run(clients, fake, tmp_path):
    fake.set(delay=0.5)
    c = clients(tmp_path, {"path": str(fake.path)})
    doc = document(c, tmp_path)
    doc.close()
    rid = pending(doc)
    fake.called()
    Path(doc.path).write_text("// newer\n")
    assert response(c, rid)["result"] == []


@pytest.mark.parametrize("encoding", ["utf-16", "utf-8"])
def test_alu_unsaved_document_and_selection(clients, fake, tmp_path, encoding):
    root = tmp_path / "alu_tb"
    shutil.copytree(EXAMPLE, root)
    c = clients(root, {"path": str(fake.path)},
                {**CAPABILITIES, "general": {"positionEncodings": [encoding]}},
                initialize_index=True, filelists=[str(root / "alu_tb.f")], useBundledUvm=True)
    doc = Doc(c, str(root / "agent" / "alu_agent.svh"))
    disk = Path(doc.path).read_bytes()
    line = next(line for line in doc.text.splitlines() if "m_driver    =" in line)
    wanted = "/*😀*/" + line
    changed = wanted.replace("    =", "          =")
    original = doc.text
    doc.change(original.replace(line, changed))
    expected = original.replace(line, wanted)
    fake.set(output=expected)
    edits = format_doc(doc)
    assert applied(doc.text, edits) == expected
    n = doc.text[:doc.text.index(changed)].count("\n")
    units = len(changed[:changed.index("m_driver")].encode(encoding + ("-le" if encoding == "utf-16" else "")))
    units //= 2 if encoding == "utf-16" else 1
    [edit] = format_range(doc, (n, units), (n + 1, 0))
    assert edit["range"] == selection((n, 0), (n + 1, 0))
    assert applied(doc.text, [edit]) == expected
    assert fake.called()["stdin"] == doc.text
    assert "--lines=" + str(n + 1) + "-" + str(n + 1) in fake.called()["argv"]
    assert Path(doc.path).read_bytes() == disk


@pytest.mark.parametrize("ranged", [False, True])
@pytest.mark.parametrize("ending", ["\n", "\r\n", ""])
def test_real_verible_matches_direct_invocation(clients, tmp_path, ranged, ending):
    binary = shutil.which("verible-verilog-format")
    if binary is None:
        pytest.skip("real verible-verilog-format is unavailable; recording fixtures do not prove Verible parity")
    source = "module t;\n  initial begin\n    a=  1;\n    b = 2;\n  end\nendmodule\n"
    source = source.replace("\n", "\r\n") if ending == "\r\n" else source if ending else source.rstrip("\n")
    c = clients(tmp_path, {"path": binary})
    doc = document(c, tmp_path, source)
    argv = [binary, "--failsafe_success=false", "--indentation_spaces=2"]
    if ranged:
        argv.append("--lines=3-3")
    direct = subprocess.run([*argv, "-"], input=source.encode("utf-8"), cwd=tmp_path,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=True)
    expected = direct.stdout.decode("utf-8").replace("\r\n", "\n")
    if ending:
        if not expected.endswith("\n"):
            expected += "\n"
    else:
        expected = expected.rstrip("\n")
    if ending == "\r\n":
        expected = expected.replace("\n", "\r\n")
    edits = format_range(doc, (2, 3), (3, 0)) if ranged else format_doc(doc)
    assert applied(doc.text, edits) == expected
    doc.change(expected)
    assert (format_range(doc, (2, 3), (3, 0)) if ranged else format_doc(doc)) == []
    assert Path(doc.path).read_bytes() == source.encode("utf-8")


DRIVER = 'm_driver    = alu_driver::type_id::create("m_driver", this);'
SPACED = DRIVER.replace("    = ", "      =     ")


def alu_agent(clients, tmp_path, formatter=None):
    """A copy of the example indexed on the bundled UVM, with the agent open as the editor has it."""
    root = tmp_path / "alu_tb"
    shutil.copytree(EXAMPLE, root)
    c = clients(root, formatter, initialize_index=True, filelists=[str(root / "alu_tb.f")], useBundledUvm=True)
    return c, Doc(c, str(root / "agent" / "alu_agent.svh"))


def test_alu_path_lookup_then_configured_executable(clients, fake, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    c, doc = alu_agent(clients, tmp_path)
    disk, fixture = Path(doc.path).read_bytes(), doc.text
    doc.change(fixture.replace(DRIVER, SPACED))
    fake.set(output=fixture)
    assert applied(doc.text, format_doc(doc)) == fixture
    record = fake.called()
    assert record["stdin"] == doc.text
    assert record["argv"] == ["--failsafe_success=false", "--indentation_spaces=2", "-"]
    assert Path(doc.path).read_bytes() == disk
    fake.record.unlink()
    other = tmp_path / "tools with spaces"
    other.mkdir()
    selected = Formatter(other)
    selected.set(output=fixture.replace("m_driver;", "m_driver;  // selected"))
    configure(c, formatter={"path": str(selected.path)})
    assert applied(doc.text, format_doc(doc)) == fixture.replace("m_driver;", "m_driver;  // selected")
    assert selected.called()["stdin"] == doc.text
    assert not fake.record.exists()
    assert Path(doc.path).read_bytes() == disk


def lines_of(text, n, ending):
    """The text before line n, line n itself and the text after it."""
    lines = text.split(ending)
    return ending.join(lines[:n]) + ending, lines[n] + ending, ending.join(lines[n + 1:])


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
@pytest.mark.parametrize("empty", [False, True])
def test_alu_driver_line_range(clients, fake, tmp_path, ending, empty):
    c, doc = alu_agent(clients, tmp_path, {"path": str(fake.path)})
    disk = Path(doc.path).read_bytes()
    fixture = doc.text.replace("\n", ending)
    doc.change(fixture.replace(DRIVER, SPACED))
    buffer = doc.text
    n = buffer[:buffer.index(SPACED)].count("\n")
    column = buffer.split(ending)[n].index("=")
    end = (n, column) if empty else (n + 1, 0)  # character 0 of the sequencer line
    assert "m_sequencer = " in buffer.split(ending)[n + 1]
    fake.set(output=fixture)
    edits = format_range(doc, (n, column), end)
    record = fake.called()
    assert record["stdin"] == buffer
    assert record["argv"] == ["--failsafe_success=false", "--indentation_spaces=2", f"--lines={n + 1}-{n + 1}", "-"]
    prefix, line, suffix = lines_of(buffer, n, ending)
    assert edits == [{"range": selection((n, 0), (n + 1, 0)), "newText": lines_of(fixture, n, ending)[1]}]
    result = applied(buffer, edits)
    assert result == fixture
    assert result.encode().startswith(prefix.encode()) and result.encode().endswith(suffix.encode())
    # Verible also changing a line outside the requested ones gives nothing, not a clipped edit.
    fake.set(output=fixture.replace("m_sequencer = alu_sequencer", "m_sequencer =  alu_sequencer"))
    assert format_range(doc, (n, column), end) == []
    c.wait_for("window/logMessage", lambda m: "beyond the requested lines" in m["params"]["message"])
    assert Path(doc.path).read_bytes() == disk


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
@pytest.mark.parametrize("lines", [None, 1, 2])
def test_real_verible_on_alu_agent(clients, tmp_path, ending, lines):
    binary = shutil.which("verible-verilog-format")
    if binary is None:
        pytest.skip("real verible-verilog-format is unavailable; recording fixtures do not prove Verible parity")
    c, doc = alu_agent(clients, tmp_path, {"path": binary})
    disk = Path(doc.path).read_bytes()
    doc.change(doc.text.replace(DRIVER, SPACED).replace("\n", ending))
    buffer = doc.text
    n = buffer[:buffer.index(SPACED)].count("\n")
    column = buffer.split(ending)[n].index("=")
    argv = [binary, "--failsafe_success=false", "--indentation_spaces=2"]
    if lines:
        argv.append(f"--lines={n + 1}-{n + lines}")
    direct = subprocess.run([*argv, "-"], input=buffer.encode("utf-8"), cwd=os.path.dirname(doc.path),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=True)
    expected = direct.stdout.decode("utf-8").replace("\r\n", "\n").replace("\n", ending)
    request = (lambda: format_range(doc, (n, column), (n + lines, 0))) if lines else (lambda: format_doc(doc))
    assert applied(buffer, request()) == expected
    if lines != 1:  # Verible leaves a lone line of an aligned pair as it is
        assert expected != buffer
    doc.change(expected)
    assert request() == []
    assert Path(doc.path).read_bytes() == disk


def test_alu_failures_then_recovery_without_reindexing(clients, fake, tmp_path):
    missing = tmp_path / "missing dir" / "verible-verilog-format"
    c, doc = alu_agent(clients, tmp_path, {"path": str(missing)})
    disk, fixture = Path(doc.path).read_bytes(), doc.text
    doc.change(fixture.replace(DRIVER, SPACED))
    c.wait_for("uvm/indexChanged")
    c.request("uvm/stats", {})
    c.forget("uvm/indexChanged", "uvm/elaborationStatus", "window/logMessage")

    def fails(reason):
        assert format_doc(doc) == []
        message = c.wait_for("window/logMessage", lambda m: reason in m["params"]["message"])["params"]["message"]
        assert Path(doc.path).read_bytes() == disk
        return message

    assert str(missing) in fails("uvm.formatter.path")
    fake.set(exit=1, stderr="formatter failure", output="// would delete the source\n")
    configure(c, formatter={"path": str(fake.path)})
    message = fails("status 1")
    assert str(fake.path) in message and "formatter failure" in message
    fake.record.unlink()
    fake.set(delay=30)
    fails("5 s")
    dead(fake.called()["pid"])
    fake.record.unlink()
    fake.set(output=fixture)
    configure(c, formatter={"path": str(fake.path), "enabled": False})
    assert format_doc(doc) == format_range(doc, (0, 0), (1, 0)) == []
    assert not fake.record.exists()
    configure(c, formatter={"path": str(fake.path), "enabled": True})
    assert applied(doc.text, format_doc(doc)) == fixture
    for method in ("uvm/indexChanged", "uvm/elaborationStatus"):
        with pytest.raises(TimeoutError):
            c.wait_for(method, timeout=0.35)
    fake.record.unlink()
    fake.set(delay=30)
    rid = pending(doc)
    pid = fake.called()["pid"]
    c.notify("$/cancelRequest", {"id": rid})
    assert response(c, rid)["error"]["code"] == -32800
    dead(pid)
    fake.record.unlink()
    fake.set(delay=0.5, output=fixture)
    rid = pending(doc)
    fake.called()
    doc.change(doc.text.replace(SPACED, SPACED + " // typed meanwhile"))
    assert response(c, rid)["result"] == []
    assert Path(doc.path).read_bytes() == disk
