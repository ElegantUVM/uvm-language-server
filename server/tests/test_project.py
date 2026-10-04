import os

from uvmls.project import ProjectConfig, build_source_set, parse_filelist

from conftest import EXAMPLE, FILELIST


def test_filelist_mode_uses_listed_files_as_roots():
    ss = build_source_set(ProjectConfig(workspace_roots=[EXAMPLE], filelists=[FILELIST], bundled_uvm=False))
    assert ss.mode == "filelist"
    assert [os.path.relpath(r, EXAMPLE) for r in ss.roots][:2] == ["rtl/alu.sv", "agent/alu_if.sv"]
    assert os.path.join(EXAMPLE, "agent") in ss.include_dirs
    assert not ss.has_real_uvm_macros


def test_discovery_mode_skips_included_files():
    ss = build_source_set(ProjectConfig(workspace_roots=[EXAMPLE]))
    rel = {os.path.relpath(r, EXAMPLE) for r in ss.roots}
    assert ss.mode == "discovery"
    assert "agent/alu_pkg.sv" in rel
    assert "agent/alu_driver.svh" not in rel  # included by alu_pkg.sv


def test_filelist_options(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_IP", str(tmp_path / "ip"))
    (tmp_path / "ip").mkdir()
    (tmp_path / "ip" / "ip.f").write_text("+incdir+$MY_IP/inc\n$(MY_IP)/ip_pkg.sv\n")
    (tmp_path / "top.f").write_text(
        "// comment\n-timescale=1ns/1ps +acc -sverilog\n"
        "+define+A=1+B -DC=2\n-f ${MY_IP}/ip.f\n"
        "-ntb_opts uvm\n"
        "tb.sv  # trailing comment\n"
    )
    r = parse_filelist(str(tmp_path / "top.f"))
    assert r.defines == {"A": "1", "B": "", "C": "2"}
    assert r.include_dirs == [str(tmp_path / "ip" / "inc")]
    assert r.files == [str(tmp_path / "ip" / "ip_pkg.sv"), str(tmp_path / "tb.sv")]


def test_repo_root_discovery_ignores_macro_shims(tmp_path):
    # Indexing a checkout of this project must not mistake the bundled shim for real UVM.
    shim = tmp_path / "server" / "uvmls" / "shim"
    shim.mkdir(parents=True)
    (shim / "uvm_macros.svh").write_text("`define uvm_info(A,B,C)\n")
    other = tmp_path / "vendor"
    other.mkdir()
    (other / "uvm_macros.svh").write_text("`define uvm_info(A,B,C)\n")
    (tmp_path / "tb.sv").write_text("module tb; endmodule\n")
    ss = build_source_set(ProjectConfig(workspace_roots=[str(tmp_path)], bundled_uvm=False))
    assert not ss.has_real_uvm_macros
    assert all("uvmls" not in r for r in ss.roots)
