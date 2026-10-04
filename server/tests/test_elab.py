"""Full elaboration with slang (elab.py), its worker process and the elab CLI command."""

import json
import os
import shutil
import subprocess
import sys

import pytest

from uvmls.cli import main as cli
from uvmls.elab import elaborate, request_for, skip_reason, uvm_label
from uvmls.project import BUNDLED_UVM, ProjectConfig
from uvmls.workspace import Workspace

from conftest import EXAMPLE, FILELIST, position

SERVER_DIR = os.path.dirname(os.path.dirname(__file__))
DRIVER = os.path.join(EXAMPLE, "agent", "alu_driver.svh")
AGENT = os.path.join(EXAMPLE, "agent", "alu_agent.svh")


def read(path):
    with open(path) as f:
        return f.read()


def edited(path, old, new):
    text = read(path)
    assert old in text
    return text.replace(old, new, 1)


def errors(result):
    return [d for d in result["diagnostics"] if d["severity"] == "error"]


def test_example_elaborates_cleanly(bundled_ws):
    r = elaborate(request_for(bundled_ws))
    assert r["error"] is None and r["tops"] == ["tb_top"]
    assert r["diagnostics"] == [] and r["counts"]["errors"] == r["counts"]["warnings"] == 0
    assert 0 < r["seconds"] < 10


def test_unknown_member_and_argument_count_in_an_unsaved_include(bundled_ws):
    # alu_driver.svh is included by alu_pkg.sv; its unsaved text is what gets compiled.
    text = edited(DRIVER, "vif = m_cfg.vif;", "vif = m_cfg.vfi;")
    text = text.replace("    drive_item(req);", "    drive_item(req, 5);")
    r = elaborate(request_for(bundled_ws, {DRIVER: text}))
    found = {(d["file"], d["line"], d["code"]): d for d in errors(r)}
    assert len(found) == 2 and r["counts"]["errors"] == 2
    _, line, col, _ = position("agent/alu_driver.svh", "m_cfg.vif", 6)
    member = found[(DRIVER, line, "UnknownMember")]
    assert member["message"] == "no member named 'vfi' in 'alu_agent_config'"
    assert (member["col"], member["endLine"], member["endCol"]) == (col, line, col + 3)
    _, line, _, _ = position("agent/alu_driver.svh", "drive_item(req);")
    assert "too many arguments" in found[(DRIVER, line, "TooManyArguments")]["message"]
    assert read(DRIVER).count("m_cfg.vfi") == 0  # the file on disk is untouched


def test_unsaved_root_file(bundled_ws):
    top = os.path.join(EXAMPLE, "tb", "tb_top.sv")  # a compilation root, not an include
    r = elaborate(request_for(bundled_ws, {top: edited(top, "rst_n = 1;", "rst_n = no_such_signal;")}))
    _, line, col, _ = position("tb/tb_top.sv", "rst_n = 1;", len("rst_n = "))
    assert [(d["file"], d["line"], d["col"], d["code"]) for d in r["diagnostics"]] == \
        [(top, line, col, "UndeclaredIdentifier")]


def test_notes_become_related_information(bundled_ws):
    text = edited(AGENT, "    ap = m_monitor.ap;\n", "    ap = m_monitor.ap;\n    m_driver.drive_itme(null);\n")
    [d] = errors(elaborate(request_for(bundled_ws, {AGENT: text})))
    assert d["file"] == AGENT and d["code"] == "UnknownMember"
    _, line, col, _ = position("agent/alu_driver.svh", "drive_item(alu_seq_item item);")
    assert d["related"] == [{"file": DRIVER, "line": line, "col": col, "message": "did you mean 'drive_item'?"}]


def test_diagnostics_inside_macro_expansions_land_in_the_user_file(bundled_ws):
    # A macro argument is reported where the user wrote it ...
    text = edited(DRIVER, '`uvm_fatal("NOCFG",', "`uvm_fatal(NOCFG_ID,")
    # ... and an error in a macro body at the whole invocation: uvm_object_utils on a component
    # generates a create() that calls new() without the parent argument.
    text = text.replace("`uvm_component_utils(alu_error_driver)", "`uvm_object_utils(alu_error_driver)")
    r = elaborate(request_for(bundled_ws, {DRIVER: text}))
    assert {d["file"] for d in errors(r)} == {DRIVER}
    _, line, col, _ = position("agent/alu_driver.svh", '"NOCFG"')
    [arg] = [d for d in errors(r) if d["code"] == "UndeclaredIdentifier"]
    assert (arg["line"], arg["col"], arg["endCol"]) == (line, col, col + len("NOCFG_ID"))
    _, line, col, _ = position("agent/alu_driver.svh", "`uvm_component_utils(alu_error_driver)")
    body = [d for d in errors(r) if d["code"] == "TooFewArguments"]
    assert body and all((d["line"], d["col"], d["endLine"]) == (line, col, line) for d in body)
    assert body[0]["endCol"] == col + len("`uvm_object_utils(alu_error_driver)")
    assert any("expanded from macro 'uvm_object_utils'" in n["message"] for n in body[0]["related"])


def test_library_diagnostics_are_never_reported(bundled_ws):
    uvm_driver = os.path.join(BUNDLED_UVM, "comps", "uvm_driver.svh")
    text = edited(uvm_driver, "endclass", "  function void broken(); undefined_thing = 1; endfunction\nendclass")
    r = elaborate(request_for(bundled_ws, {uvm_driver: text}))
    assert r["diagnostics"] == [] and r["counts"]["libraryErrors"] == 1


def test_uvm_macros_are_predefined_when_a_file_does_not_include_them(tmp_path):
    (tmp_path / "t.sv").write_text("import uvm_pkg::*;\nclass c extends uvm_object;\n  `uvm_object_utils(c)\n"
                                   "  function new(string name = \"c\"); super.new(name); endfunction\n"
                                   "  function void f(); `uvm_info(\"F\", \"hi\", UVM_LOW) x = 1; endfunction\n"
                                   "endclass\n")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    r = elaborate(request_for(w))
    assert [(d["line"], d["code"]) for d in r["diagnostics"]] == [(4, "UndeclaredIdentifier")]
    assert r["tops"] == []  # no top module: packages and classes are still checked


def test_missing_file_is_reported_and_the_rest_still_elaborates(tmp_path):
    (tmp_path / "top.sv").write_text("module top; initial y = 1; endmodule\n")
    (tmp_path / "tb.f").write_text("top.sv\nmissing.sv\n")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)], filelists=[str(tmp_path / "tb.f")]))
    r = elaborate(request_for(w))
    assert [(os.path.basename(d["file"]), d["code"]) for d in r["diagnostics"]] == \
        [("missing.sv", "io"), ("top.sv", "UndeclaredIdentifier")]
    assert r["tops"] == ["top"]


def test_skip_reasons(ws, bundled_ws, tmp_path):
    assert skip_reason(bundled_ws) is None
    assert "built-in UVM model" in skip_reason(ws)
    assert uvm_label(bundled_ws.sources) == "bundled Accellera UVM 1.2"
    # Two testbenches in one workspace: discovery would compile both tops and both packages.
    for name in ("tb_a", "tb_b"):
        shutil.copytree(EXAMPLE, tmp_path / name)
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    reason = skip_reason(w)
    assert "alu, alu_env_pkg, alu_if and 4 more are defined in more than one file" in reason
    assert "uvm.filelists" in reason
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)], filelists=[str(tmp_path / "tb_a" / "alu_tb.f")]))
    assert skip_reason(w) is None


def run_worker(payload: bytes):
    env = dict(os.environ, PYTHONPATH=SERVER_DIR)
    p = subprocess.run([sys.executable, "-m", "uvmls", "elab-worker"], input=payload,
                       capture_output=True, env=env, timeout=60)
    assert p.returncode == 0
    return json.loads(p.stdout)


def test_worker_round_trip(bundled_ws):
    text = edited(DRIVER, "vif = m_cfg.vif;", "vif = m_cfg.vfi;")
    r = run_worker(json.dumps(request_for(bundled_ws, {DRIVER: text})).encode())
    assert r["error"] is None and r["tops"] == ["tb_top"]
    assert [(d["file"], d["code"]) for d in r["diagnostics"]] == [(DRIVER, "UnknownMember")]
    # Failures come back as a result, never as a traceback on stdout.
    assert run_worker(b"{not json")["error"].startswith("bad elaboration request")
    assert run_worker(json.dumps({"roots": [], "args": ["--no-such-option"]}).encode())["error"]


@pytest.fixture
def broken_example(tmp_path):
    root = tmp_path / "alu_tb"
    shutil.copytree(EXAMPLE, root)
    drv = root / "agent" / "alu_driver.svh"
    drv.write_text(edited(DRIVER, "vif = m_cfg.vif;", "vif = m_cfg.vfi;"))
    return root


def test_cli_exit_codes(capsys, monkeypatch, broken_example):
    monkeypatch.delenv("UVM_HOME", raising=False)

    def elab(*args):
        with pytest.raises(SystemExit) as e:
            cli(["elab", *args])
        return e.value.code, capsys.readouterr()

    code, out = elab(EXAMPLE)  # discovery mode: indexed first, to look for duplicate design units
    assert code == 0 and "0 error(s), 0 warning(s)" in out.err
    # With a filelist nothing needs the index, so it is not built.
    monkeypatch.setattr(Workspace, "reindex_all", lambda self: pytest.fail("indexed"))
    code, out = elab(EXAMPLE, "-f", FILELIST)
    assert code == 0 and out.out == "" and "0 error(s), 0 warning(s)" in out.err
    code, out = elab(str(broken_example), "-f", str(broken_example / "alu_tb.f"), "--top", "tb_top")
    _, line, col, _ = position("agent/alu_driver.svh", "m_cfg.vif", 6)
    assert code == 1
    assert out.out.endswith(f"alu_driver.svh:{line + 1}:{col + 1}: error: no member named 'vfi' in "
                            "'alu_agent_config' [UnknownMember]\n")
    code, out = elab(str(broken_example), "-f", str(broken_example / "alu_tb.f"), "--json")
    assert code == 1 and json.loads(out.out)["counts"]["errors"] == 1
    code, out = elab(EXAMPLE, "-f", FILELIST, "--no-bundled-uvm")
    assert code == 2 and "elaboration skipped: the built-in UVM model" in out.err
