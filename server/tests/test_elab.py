"""Full elaboration with slang (elab.py), its worker process and the elab CLI command."""

import json
import os
import shutil
import subprocess
import sys

import pytest

from uvmls.cli import main as cli
from uvmls.elab import MAX_LISTED, MAX_PER_FILE, elaborate, request_for, skip_reason, uvm_label
from uvmls.project import BUNDLED_UVM, ProjectConfig
from uvmls.workspace import Workspace

from conftest import EXAMPLE, FILELIST, position

SERVER_DIR = os.path.dirname(os.path.dirname(__file__))
DRIVER = os.path.join(EXAMPLE, "agent", "alu_driver.svh")
AGENT = os.path.join(EXAMPLE, "agent", "alu_agent.svh")
SCOREBOARD = os.path.join(EXAMPLE, "env", "alu_scoreboard.svh")
REGISTRY = os.path.join(BUNDLED_UVM, "base", "uvm_registry.svh")


def read(path):
    with open(path, encoding="utf-8") as f:
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


def test_option_failure_includes_slangs_diagnostic_and_restores_stderr(bundled_ws, capfd):
    req = request_for(bundled_ws, args=["--not-an-elaboration-option"])
    result = elaborate(req)
    assert "slang did not accept the elaboration options" in result["error"]
    assert "unknown command line argument '--not-an-elaboration-option'" in result["error"]
    os.write(2, b"stderr restored\n")
    assert capfd.readouterr().err == "stderr restored\n"
    assert elaborate(request_for(bundled_ws))["error"] is None


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


def test_line_directives_do_not_move_diagnostics(bundled_ws):
    # The index places ranges in the file's own lines; elaboration must agree, or one error is
    # published twice, once past the end of the file.
    top = os.path.join(EXAMPLE, "tb", "tb_top.sv")
    text = edited(top, "rst_n = 1;", "rst_n = no_such_signal;")
    r = elaborate(request_for(bundled_ws, {top: '`line 500 "generated.tpl" 0\n' + text}))
    _, line, col, _ = position("tb/tb_top.sv", "rst_n = 1;", len("rst_n = "))
    assert [(d["file"], d["line"], d["col"], d["endLine"], d["code"]) for d in r["diagnostics"]] == \
        [(top, line + 1, col, line + 1, "UndeclaredIdentifier")]


def test_workspace_and_library_behind_symlinks(tmp_path):
    # slang resolves symlinks; unsaved text must still apply, diagnostics keep the paths the
    # editor uses, and the UVM library is still recognised.
    (tmp_path / "real").mkdir()
    shutil.copytree(EXAMPLE, tmp_path / "real" / "alu_tb")
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    (tmp_path / "uvm").symlink_to(os.path.dirname(BUNDLED_UVM), target_is_directory=True)
    root = tmp_path / "link" / "alu_tb"
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(root)], filelists=[str(root / "alu_tb.f")],
                              uvm_home=str(tmp_path / "uvm")))
    drv, top = str(root / "agent" / "alu_driver.svh"), str(root / "tb" / "tb_top.sv")
    uvm_driver = os.path.join(BUNDLED_UVM, "comps", "uvm_driver.svh")
    req = request_for(w, {
        drv: edited(DRIVER, "vif = m_cfg.vif;", "vif = m_cfg.vfi;"),
        top: edited(os.path.join(EXAMPLE, "tb", "tb_top.sv"), "rst_n = 1;", "rst_n = no_such_signal;"),
        str(tmp_path / "uvm" / "src" / "comps" / "uvm_driver.svh"):
            edited(uvm_driver, "endclass", "  function void broken(); undefined_thing = 1; endfunction\nendclass"),
    })
    for library in (req["library"], [os.path.realpath(d) for d in req["library"]]):  # either name
        r = elaborate(dict(req, library=library))
        assert sorted((d["file"], d["code"]) for d in r["diagnostics"]) == \
            [(drv, "UnknownMember"), (top, "UndeclaredIdentifier")]
        assert r["counts"]["libraryErrors"] == 1


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
    assert {d["file"] for d in errors(r)} == {DRIVER, REGISTRY}  # the factory's create_object() too
    _, line, col, _ = position("agent/alu_driver.svh", '"NOCFG"')
    [arg] = [d for d in errors(r) if d["code"] == "UndeclaredIdentifier"]
    assert (arg["line"], arg["col"], arg["endCol"]) == (line, col, col + len("NOCFG_ID"))
    _, line, col, _ = position("agent/alu_driver.svh", "`uvm_component_utils(alu_error_driver)")
    body = [d for d in errors(r) if d["code"] == "TooFewArguments" and d["file"] == DRIVER]
    assert body and all((d["line"], d["col"], d["endLine"]) == (line, col, line) for d in body)
    assert body[0]["endCol"] == col + len("`uvm_object_utils(alu_error_driver)")
    assert any("expanded from macro 'uvm_object_utils'" in n["message"] for n in body[0]["related"])


def test_an_info_task_is_not_its_own_related_information(tmp_path):
    # slang prints a Note-severity diagnostic ($info) as a "note:" line itself, and, as the
    # module has two instances, after an "in instance: top.a.g" line.
    (tmp_path / "t.sv").write_text(
        "`define CHECK(w) if (w < 8) begin : m $info(\"m is %0d\", w); end\n"
        "module sub #(parameter int W = 3) ();\n"
        "  if (W < 8) begin : g\n    $info(\"W is %0d\", W);\n  end\n"
        "  `CHECK(W)\n"
        "endmodule\n"
        "module top; sub #(3) a(); sub #(4) b(); endmodule\n", encoding="utf-8", newline="")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    r = elaborate(request_for(w))
    assert [(d["line"], d["severity"], d["message"], [n["message"] for n in d["related"]])
            for d in r["diagnostics"]] == [
        (3, "info", "$info encountered: W is 3", []),
        (3, "info", "$info encountered: W is 4", []),
        (5, "info", "$info encountered: m is 3", ["expanded from macro 'CHECK'"]),
        (5, "info", "$info encountered: m is 4", ["expanded from macro 'CHECK'"])]


def test_a_message_quoting_latin1_text_keeps_the_other_diagnostics(tmp_path):
    # A legacy Latin-1 file: slang's message quotes its bytes, which are not UTF-8.
    (tmp_path / "top.sv").write_bytes(b'module top;\n  if (1) begin : g\n    $info("temp\xe9rature hors limites");\n'
                                      b'  end\n  initial x = 1;\nendmodule\n')
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    r = elaborate(request_for(w))
    assert r["error"] is None
    found = {(d["line"], d["code"]): d["message"] for d in r["diagnostics"]}
    assert found[(2, "InfoTask")] == "$info encountered: temp\ufffdrature hors limites"
    assert found[(4, "UndeclaredIdentifier")] == "use of undeclared identifier 'x'"


def test_driver_and_assertion_checks_from_the_analysis_pass(tmp_path):
    # slang runs these after elaboration, in its analysis pass; simulators reject them too.
    (tmp_path / "top.sv").write_text("module top(input logic b);\n  logic a, c;\n  assign a = b;\n"
                                     "  initial a = 0;\n  assign c = b;\n  assign c = ~b;\n"
                                     "  assert property (b |=> c);\nendmodule\n", encoding="utf-8", newline="")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    r = elaborate(request_for(w))
    assert [(d["line"], d["code"], d["severity"]) for d in r["diagnostics"]] == \
        [(3, "MixedVarAssigns", "error"), (5, "MultipleContAssigns", "error"), (6, "AssertionNoClock", "error")]
    assert [(n["line"], n["message"]) for n in r["diagnostics"][0]["related"]] == [(2, "also assigned here")]


def test_no_error_limit_hides_later_errors(tmp_path):
    # slang's default limit stopped after about 64 errors: the one in tb_top was never reported.
    for i in range(70):
        (tmp_path / f"old_{i:03}.sv").write_text(f"module old_{i:03}; logic a; assign a = undeclared_{i};\nendmodule\n", encoding="utf-8", newline="")
    (tmp_path / "tb_top.sv").write_text("module tb_top; logic a; assign a = planted_err; endmodule\n", encoding="utf-8", newline="")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    r = elaborate(request_for(w))
    assert r["counts"]["errors"] == len(r["diagnostics"]) == 71
    assert "use of undeclared identifier 'planted_err'" in {d["message"] for d in r["diagnostics"]}
    # Errors inside the library count toward no limit either.
    lib = tmp_path / "lib"
    lib.mkdir()
    for i in range(70):
        (lib / f"p_{i:03}.sv").write_text(f"package p_{i:03}; int x = missing_{i}; endpackage\n", encoding="utf-8", newline="")
    req = {"roots": [str(p) for p in sorted(lib.iterdir())] + [str(tmp_path / "tb_top.sv")],
           "library": [str(lib)]}
    r = elaborate(req)
    assert [d["message"] for d in r["diagnostics"]] == ["use of undeclared identifier 'planted_err'"]
    assert (r["counts"]["errors"], r["counts"]["libraryErrors"]) == (1, 70)


def test_listed_diagnostics_are_capped_and_counted(tmp_path):
    body = "".join(f"    x = nope_{i};\n" for i in range(MAX_LISTED + 500))
    (tmp_path / "big.sv").write_text(f"module big; int x;\n  initial begin\n{body}  end\nendmodule\n", encoding="utf-8", newline="")
    (tmp_path / "top.sv").write_text("module top; big b(); initial y = 1; endmodule\n", encoding="utf-8", newline="")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    r = elaborate(request_for(w))
    listed = [(os.path.basename(d["file"]), d["line"]) for d in r["diagnostics"]]
    assert listed == [("big.sv", i + 2) for i in range(MAX_PER_FILE)] + [("top.sv", 0)]  # the first of each file
    assert r["counts"]["errors"] == MAX_LISTED + 501 and r["counts"]["omitted"] == MAX_LISTED + 500 - MAX_PER_FILE


def without_parent(text):
    """alu_error_driver (nothing derives from it) with a constructor that takes no parent."""
    old = "  function new(string name, uvm_component parent);\n    super.new(name, parent);\n  endfunction"
    i = text.rindex(old)
    return text[:i] + "  function new(string name);\n    super.new(name, null);\n  endfunction" + text[i + len(old):]


def test_errors_the_design_causes_inside_the_library_are_reported(bundled_ws):
    # The factory's registry calls new(name, parent): the error is in uvm_registry.svh, but the
    # design's class causes it. Errors the library reports on its own stay hidden.
    r = elaborate(request_for(bundled_ws, {DRIVER: without_parent(read(DRIVER))}))
    [d] = r["diagnostics"]
    assert (d["file"], d["code"]) == (REGISTRY, "TooManyArguments")
    assert d["message"] == ("too many arguments for 'new'; expected 1 but 2 were provided (in uvm_pkg::"
                            'uvm_component_registry#(alu_error_driver,"alu_error_driver")::create_component)')
    _, line, col, _ = position("agent/alu_driver.svh", "alu_error_driver extends")
    assert d["related"] == [{"file": DRIVER, "line": line, "col": col, "message": "T = alu_error_driver"}]
    assert (r["counts"]["errors"], r["counts"]["libraryErrors"]) == (1, 0)
    # An analysis imp whose owner has no write(): slang's "did you mean" points at the design.
    text = edited(SCOREBOARD, "function void write(", "function void wrte(")
    [d] = elaborate(request_for(bundled_ws, {SCOREBOARD: text}))["diagnostics"]
    assert (d["file"], d["code"]) == (os.path.join(BUNDLED_UVM, "tlm1", "uvm_analysis_port.svh"), "UnknownMember")
    _, line, _, _ = position("env/alu_scoreboard.svh", "function void write(")
    assert [(os.path.basename(n["file"]), n["line"], n["message"]) for n in d["related"]] == [
        ("alu_scoreboard.svh", line, "did you mean 'wrte'?"), ("alu_seq_item.svh", 1, "T = alu_seq_item"),
        ("alu_scoreboard.svh", 1, "IMP = alu_scoreboard")]


def test_errors_the_library_reports_on_its_own_stay_hidden():
    # UVM 1.2 does not compile cleanly under slang with QUESTA defined; none of that is the design's.
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[EXAMPLE], filelists=[FILELIST], defines={"QUESTA": ""}))
    r = elaborate(request_for(w))
    assert r["diagnostics"] == [] and r["counts"]["errors"] == 0 and r["counts"]["libraryErrors"] > 0


def test_library_diagnostics_are_never_reported(bundled_ws):
    uvm_driver = os.path.join(BUNDLED_UVM, "comps", "uvm_driver.svh")
    text = edited(uvm_driver, "endclass", "  function void broken(); undefined_thing = 1; endfunction\nendclass")
    r = elaborate(request_for(bundled_ws, {uvm_driver: text}))
    assert r["diagnostics"] == [] and r["counts"]["libraryErrors"] == 1


def test_uvm_macros_are_predefined_when_a_file_does_not_include_them(tmp_path):
    (tmp_path / "t.sv").write_text("import uvm_pkg::*;\nclass c extends uvm_object;\n  `uvm_object_utils(c)\n"
                                   "  function new(string name = \"c\"); super.new(name); endfunction\n"
                                   "  function void f(); `uvm_info(\"F\", \"hi\", UVM_LOW) x = 1; endfunction\n"
                                   "endclass\n", encoding="utf-8", newline="")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    r = elaborate(request_for(w))
    assert [(d["line"], d["code"]) for d in r["diagnostics"]] == [(4, "UndeclaredIdentifier")]
    assert r["tops"] == []  # no top module: packages and classes are still checked


def test_missing_file_is_reported_and_the_rest_still_elaborates(tmp_path):
    (tmp_path / "top.sv").write_text("module top; initial y = 1; endmodule\n", encoding="utf-8", newline="")
    (tmp_path / "tb.f").write_text("top.sv\nmissing.sv\n", encoding="utf-8", newline="")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)], filelists=[str(tmp_path / "tb.f")]))
    r = elaborate(request_for(w))
    assert [(os.path.basename(d["file"]), d["code"]) for d in r["diagnostics"]] == \
        [("missing.sv", "io"), ("top.sv", "UndeclaredIdentifier")]
    assert r["tops"] == ["top"]


def test_a_listed_uvm_pkg_that_does_not_exist_is_one_error(tmp_path, monkeypatch):
    # Not the project's own UVM: the bundled one is compiled, and the missing file is reported.
    monkeypatch.delenv("UVM_HOME", raising=False)
    shutil.copytree(EXAMPLE, tmp_path / "tb")
    (tmp_path / "tb" / "env.f").write_text("+incdir+$UVM_HOME/src\n$UVM_HOME/src/uvm_pkg.sv\n-f alu_tb.f\n", encoding="utf-8", newline="")
    w = Workspace()
    w.configure(ProjectConfig.from_settings([str(tmp_path / "tb")], {"filelists": ["env.f"]}))
    assert uvm_label(w.sources) == "bundled Accellera UVM 1.2" and w.stats()["libraryClasses"] > 300
    r = elaborate(request_for(w))
    assert [(os.path.basename(d["file"]), d["code"]) for d in r["diagnostics"]] == [("uvm_pkg.sv", "io")]
    assert [d.code for d in w.user_diagnostics()] == ["io"]


def test_uvm_home_replaces_a_uvm_the_project_compiles(tmp_path, monkeypatch):
    # Two copies of uvm_pkg would be a duplicate definition, and a mix of both in the index.
    monkeypatch.delenv("UVM_HOME", raising=False)
    shutil.copytree(BUNDLED_UVM, tmp_path / "own" / "src")
    (tmp_path / "tb.f").write_text(f"+incdir+own/src\nown/src/uvm_pkg.sv\n-f {FILELIST}\n", encoding="utf-8", newline="")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)], filelists=[str(tmp_path / "tb.f")],
                              uvm_home=os.path.dirname(BUNDLED_UVM)))
    assert len(w.classes_by_name["uvm_driver"]) == 1
    r = elaborate(request_for(w))
    assert r["error"] is None and r["tops"] == ["tb_top"] and r["diagnostics"] == []
    # Discovery: the vendored copy's files are not roots either.
    os.remove(tmp_path / "tb.f")
    shutil.copytree(EXAMPLE, tmp_path / "alu", ignore=shutil.ignore_patterns("*.f"))
    monkeypatch.setenv("UVM_HOME", os.path.dirname(BUNDLED_UVM))
    w.configure(ProjectConfig.from_settings([str(tmp_path)], {}))
    assert w.sources.uvm_source == "configured" and len(w.classes_by_name["uvm_driver"]) == 1
    assert skip_reason(w) is None
    r = elaborate(request_for(w))
    assert r["error"] is None and r["tops"] == ["tb_top"] and r["diagnostics"] == []


def test_filelist_timescale_reaches_slang(tmp_path):
    # Some design elements with a `timescale and some without is an error, unless the
    # simulator is given a default, as filelists often do.
    (tmp_path / "dut.sv").write_text("`timescale 1ns/1ps\nmodule dut; endmodule\n", encoding="utf-8", newline="")
    (tmp_path / "top.sv").write_text("module top; dut u(); endmodule\n", encoding="utf-8", newline="")
    codes = {}
    for name, extra in (("plain.f", ""), ("vcs.f", "-timescale=1ns/1ps\n")):
        (tmp_path / name).write_text(f"dut.sv\ntop.sv\n{extra}", encoding="utf-8", newline="")
        w = Workspace()
        w.configure(ProjectConfig(workspace_roots=[str(tmp_path)], filelists=[str(tmp_path / name)]))
        codes[name] = [d["code"] for d in elaborate(request_for(w))["diagnostics"]]
    assert codes == {"plain.f": ["MissingTimeScale"], "vcs.f": []}


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
    assert "alu, alu_env_pkg, alu_if and 4 more would be defined more than once" in reason
    assert "uvm.filelists" in reason
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)], filelists=[str(tmp_path / "tb_a" / "alu_tb.f")]))
    assert skip_reason(w) is None
    # A file open in the editor but outside the compile does not count.
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path / "tb_a")]))
    w.update_document(str(tmp_path / "elsewhere.sv"), "module tb_top; endmodule\n")
    assert len(w.units_by_name["tb_top"]) == 2 and skip_reason(w) is None


def test_discovery_leaves_out_headers_nothing_includes(tmp_path, monkeypatch, capsys):
    # An old copy nothing includes, and a header reached only through a macro-built `include
    # path, compile only inside their package: as roots of their own they would fail at $unit.
    root = tmp_path / "alu_tb"
    shutil.copytree(EXAMPLE, root)
    (root / "agent" / "alu_driver_old.svh").write_text(
        read(DRIVER).replace("alu_error_driver", "alu_error_driver_old").replace("alu_driver", "alu_driver_old"), encoding="utf-8", newline="")
    pkg = root / "agent" / "alu_pkg.sv"
    pkg.write_text(read(pkg).replace('`include "alu_driver.svh"',
                                     '`define DRIVER_SVH "alu_driver.svh"\n  `include `DRIVER_SVH'), encoding="utf-8", newline="")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(root)]))
    assert w.sources.mode == "discovery" and "alu_driver_old" in w.classes_by_name  # still indexed
    r = elaborate(request_for(w))
    assert r["diagnostics"] == [] and r["tops"] == ["tb_top"]
    monkeypatch.delenv("UVM_HOME", raising=False)
    with pytest.raises(SystemExit) as e:
        cli(["elab", str(root)])
    assert e.value.code == 0 and "0 error(s)" in capsys.readouterr().err
    # A package in a .svh file that nothing includes is still compiled.
    pkg.rename(root / "agent" / "alu_pkg.svh")
    w.configure(ProjectConfig(workspace_roots=[str(root)]))
    r = elaborate(request_for(w))
    assert r["diagnostics"] == [] and r["tops"] == ["tb_top"]


def test_a_package_and_a_module_may_share_a_name(tmp_path):
    (tmp_path / "uart_pkg.sv").write_text("package uart; typedef logic [7:0] byte_t; endpackage\n", encoding="utf-8", newline="")
    (tmp_path / "uart.sv").write_text("module uart(input logic clk); uart::byte_t data; endmodule\n"
                                      "module top; logic clk; uart u(.clk(clk)); endmodule\n", encoding="utf-8", newline="")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    assert skip_reason(w) is None
    assert errors(elaborate(request_for(w))) == []
    # An interface shares the module's name space: that is a real clash.
    (tmp_path / "uart_if.sv").write_text("interface uart; endinterface\n", encoding="utf-8", newline="")
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    assert "uart would be defined more than once" in skip_reason(w)


def test_skip_reason_for_a_file_included_by_two_tops(tmp_path):
    # One clk_gen in the index, but discovery compiles it once for each top that includes it.
    (tmp_path / "common").mkdir()
    (tmp_path / "common" / "clk_gen.sv").write_text("module clk_gen; endmodule\n", encoding="utf-8", newline="")
    for name in ("tb_a", "tb_b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / f"{name}.sv").write_text(f'`include "clk_gen.sv"\nmodule {name}; clk_gen u(); endmodule\n', encoding="utf-8", newline="")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    assert len(w.units_by_name["clk_gen"]) == 1
    assert skip_reason(w).startswith("clk_gen would be defined more than once")
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)], exclude=["**/tb_b/**"]))
    assert skip_reason(w) is None and errors(elaborate(request_for(w))) == []
    # A top that includes the UVM package itself reaches it from its own root and the library's.
    (tmp_path / "uvm_tb").mkdir()
    (tmp_path / "uvm_tb" / "tb.sv").write_text('`include "uvm_macros.svh"\n`include "uvm_pkg.sv"\n'
                                              "module tb_top; import uvm_pkg::*; initial run_test(); endmodule\n", encoding="utf-8", newline="")
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path / "uvm_tb")]))
    assert skip_reason(w) is None and errors(elaborate(request_for(w))) == []


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
    drv.write_text(edited(DRIVER, "vif = m_cfg.vif;", "vif = m_cfg.vfi;"), encoding="utf-8", newline="")
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
    drv = broken_example / "agent" / "alu_driver.svh"
    drv.write_text(without_parent(read(DRIVER)), encoding="utf-8", newline="")
    code, out = elab(str(broken_example), "-f", str(broken_example / "alu_tb.f"))
    assert code == 1 and "uvm_registry.svh:" in out.out and "1 error(s)" in out.err
    code, out = elab(EXAMPLE, "-f", FILELIST, "--no-bundled-uvm")
    assert code == 2 and "elaboration skipped: the built-in UVM model" in out.err
    assert "drop --no-bundled-uvm or pass --uvm-home" in out.err


def test_did_you_mean_carries_both_names(tmp_path):
    # What the quick fix replaces, and with what: from slang's arguments, or its "did you mean" note.
    (tmp_path / "t.sv").write_text("class c;\n  int buildit;\n  function void f();\n    buildt = 1;\n"
                                   "    this.buildt = 2;\n    this.zzz = 3;\n  endfunction\nendclass\n", encoding="utf-8", newline="")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    found = [(d["line"], d["code"], d.get("data")) for d in elaborate(request_for(w))["diagnostics"]]
    assert found == [(3, "TypoIdentifier", {"from": "buildt", "to": "buildit"}),
                     (4, "UnknownMember", {"from": "buildt", "to": "buildit"}),
                     (5, "UnknownMember", None)]  # nothing close enough to suggest


@pytest.mark.parametrize("kind", ["lsp", "mcp"])
def test_worker_spawn_with_pipes_and_pythonpath(tmp_path, monkeypatch, kind):
    import asyncio
    from uvmls.mcp import McpServer
    from uvmls.server import UvmLanguageServer

    path = tmp_path / "worker source.sv"
    path.write_text("module worker_top; endmodule\n", encoding="utf-8", newline="")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)], bundled_uvm=False))
    request = request_for(w)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(["missing-one", "missing-two"]))
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")

    async def run():
        if kind == "lsp":
            ls = UvmLanguageServer()
            return await ls.run_worker(ls._elab_run, request)
        return await McpServer(w)._run_worker(request)

    result = asyncio.run(run())
    assert result["error"] is None, result
    assert result["tops"] == ["worker_top"], result
    assert result["diagnostics"] == [], result


@pytest.mark.parametrize("flags", [[], ["--json"], ["--sarif", "-"], ["--sarif", "-", "--json"]])
def test_cli_failed_elaboration_explains_exit_two(tmp_path, monkeypatch, capsys, flags):
    import uvmls.cli as module
    from uvmls.elab import _failed

    (tmp_path / "top.sv").write_text("module top; endmodule\n", encoding="utf-8", newline="")
    result = {**_failed("worker could not load café.sv"), "seconds": 0.0}
    monkeypatch.setattr(module, "elaborate", lambda request: result)
    with pytest.raises(SystemExit) as exc:
        cli(["elab", str(tmp_path), *flags])
    assert exc.value.code == 2
    out = capsys.readouterr()
    assert "elaboration failed: worker could not load café.sv" in out.err
    if "--sarif" in flags:
        assert json.loads(out.out)["runs"][0]["results"] == []
