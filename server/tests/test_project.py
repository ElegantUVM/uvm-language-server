import os

from uvmls.project import (
    BUNDLED_UVM, ProjectConfig, build_source_set, discover_files, is_bundled, parse_filelist,
    uvm_version,
)

from conftest import EXAMPLE, FILELIST


def fake_uvm(home, rev: str) -> str:
    """A minimal UVM kit at `home`; returns its src directory."""
    src = home / "src"
    (src / "macros").mkdir(parents=True)
    (src / "uvm_pkg.sv").write_text('package uvm_pkg;\n`include "uvm_macros.svh"\n'
                                    'class uvm_object; endclass\nendpackage\n')
    (src / "uvm_macros.svh").write_text("`define uvm_object_utils(T)\n")
    fix = f"`define UVM_FIX_REV {rev[3:]}" if rev[3:] else "//`define UVM_FIX_REV a"
    (src / "macros" / "uvm_version_defines.svh").write_text(
        f"`define UVM_MAJOR_REV {rev[0]}\n`define UVM_MINOR_REV {rev[2]}\n{fix}\n")
    return str(src)


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
    assert r.timescale == "1ns/1ps"
    (tmp_path / "xrun.f").write_text("-timescale 1ps/1fs\ntb.sv\n")
    assert parse_filelist(str(tmp_path / "xrun.f")).timescale == "1ps/1fs"


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


def test_uvm_library_precedence(tmp_path, monkeypatch):
    setting = fake_uvm(tmp_path / "setting", "1.1d")
    env = fake_uvm(tmp_path / "env", "1.1c")
    proj, other = tmp_path / "proj", tmp_path / "other"
    own = fake_uvm(proj / "uvm", "1.2")
    other.mkdir()
    for d in (proj, other):
        (d / "tb.sv").write_text("module tb; endmodule\n")
    (proj / "tb.f").write_text(f"{own}/uvm_pkg.sv\ntb.sv\n")

    def choice(root, settings):
        ss = build_source_set(ProjectConfig.from_settings([str(root)], settings))
        # The bundled copy is only ever added when it is the library in use.
        assert any(is_bundled(r) for r in ss.roots) == (ss.uvm_source == "bundled")
        return ss.uvm_source, ss.uvm_dir, ss.uvm_version

    monkeypatch.setenv("UVM_HOME", str(tmp_path / "env"))
    assert choice(proj, {"uvmHome": str(tmp_path / "setting"), "filelists": ["tb.f"]}) == \
        ("configured", setting, "1.1d")
    assert choice(proj, {"filelists": ["tb.f"]}) == ("configured", env, "1.1c")
    typo = {"uvmHome": str(tmp_path / "typo")}  # holds no UVM: $UVM_HOME is next
    assert choice(proj, dict(typo, filelists=["tb.f"])) == ("configured", env, "1.1c")
    assert choice(other, typo) == ("configured", env, "1.1c")
    monkeypatch.delenv("UVM_HOME")
    assert choice(proj, {"filelists": ["tb.f"]}) == ("project", own, "1.2")
    assert choice(other, {}) == ("bundled", BUNDLED_UVM, "1.2")
    assert choice(other, {"useBundledUvm": False}) == ("builtin", None, None)


def test_project_uvm_on_include_path_or_behind_uvm_sv(tmp_path):
    kit = fake_uvm(tmp_path / "kit", "1.2")
    (tmp_path / "kit" / "src" / "uvm.sv").write_text('`include "uvm_pkg.sv"\n')
    (tmp_path / "tb.sv").write_text("module tb; endmodule\n")

    def source_set(filelist_text=None):
        fl = []
        if filelist_text is not None:
            (tmp_path / "tb.f").write_text(filelist_text)
            fl = [str(tmp_path / "tb.f")]
        ss = build_source_set(ProjectConfig(workspace_roots=[str(tmp_path)], filelists=fl))
        assert (ss.uvm_source, ss.uvm_dir) == ("project", kit)
        assert [r for r in ss.roots if r not in ss.library_roots] == [str(tmp_path / "tb.sv")]
        return ss

    # Only an include directory: its uvm_pkg.sv is indexed.
    assert source_set(f"+incdir+{kit}\ntb.sv\n").roots[0] == os.path.join(kit, "uvm_pkg.sv")
    # The uvm.sv wrapper the UVM README says to compile.
    assert source_set(f"{kit}/uvm.sv\ntb.sv\n").roots[0] == os.path.join(kit, "uvm.sv")
    # Discovery: every root under the kit is library code.
    source_set()


def test_a_listed_uvm_pkg_that_does_not_exist_is_not_the_uvm_library(tmp_path, monkeypatch):
    # $UVM_HOME set in the shell but not where the editor runs: the filelist names a missing file.
    monkeypatch.delenv("UVM_HOME", raising=False)
    (tmp_path / "tb.sv").write_text("module tb; endmodule\n")
    (tmp_path / "tb.f").write_text("+incdir+$UVM_HOME/src\n$UVM_HOME/src/uvm_pkg.sv\ntb.sv\n")
    ss = build_source_set(ProjectConfig.from_settings([str(tmp_path)], {"filelists": ["tb.f"]}))
    assert (ss.uvm_source, ss.uvm_dir, ss.uvm_version) == ("bundled", BUNDLED_UVM, "1.2")
    missing = os.path.join(str(tmp_path), "$UVM_HOME", "src", "uvm_pkg.sv")
    assert missing in ss.roots and missing not in ss.library_roots  # its read error stays visible
    assert ss.warnings == [f"{tmp_path / 'tb.f'}: $UVM_HOME is not set",
                           f"{missing} does not exist (is a variable in its path unset?); "
                           "it is not used as the UVM library"]
    # Nor does it hide a UVM on the include path.
    kit = fake_uvm(tmp_path / "kit", "1.1d")
    (tmp_path / "tb.f").write_text(f"+incdir+{kit}\n{tmp_path}/nope/uvm_pkg.sv\ntb.sv\n")
    ss = build_source_set(ProjectConfig.from_settings([str(tmp_path)], {"filelists": ["tb.f"]}))
    assert (ss.uvm_source, ss.uvm_dir) == ("project", kit)


def test_bad_uvm_home_warns_and_falls_back(tmp_path, monkeypatch):
    (tmp_path / "tb.sv").write_text("module tb; endmodule\n")
    ss = build_source_set(ProjectConfig(workspace_roots=[str(tmp_path)], uvm_home=str(tmp_path / "nope")))
    assert ss.uvm_source == "bundled"
    assert ss.warnings == [f"uvmHome {tmp_path / 'nope'} does not contain uvm_pkg.sv"]
    monkeypatch.setenv("UVM_HOME", str(tmp_path / "nada"))
    ss = build_source_set(ProjectConfig.from_settings([str(tmp_path)], {}))
    assert ss.uvm_source == "bundled"
    assert ss.warnings == [f"$UVM_HOME {tmp_path / 'nada'} does not contain uvm_pkg.sv"]


def test_uvm_version(tmp_path):
    assert uvm_version(BUNDLED_UVM) == "1.2"
    assert uvm_version(fake_uvm(tmp_path / "a", "1.1d")) == "1.1d"
    assert uvm_version(fake_uvm(tmp_path / "b", "1.2")) == "1.2"
    ieee = tmp_path / "ieee"
    (ieee / "base").mkdir(parents=True)
    (ieee / "base" / "uvm_version.svh").write_text(
        'parameter string UVM_VERSION_STRING = "Accellera:1800.2:UVM:2020.3.1";\n')
    assert uvm_version(str(ieee)) == "1800.2 2020.3.1"
    assert uvm_version(str(tmp_path)) is None


def test_discovery_skips_the_vendored_uvm(tmp_path):
    # A checkout of this project, with the copy that npm run package stages for the VSIX.
    for base in ("server", "client/server"):
        fake_uvm(tmp_path / base / "uvmls" / "third_party" / "uvm-1.2", "1.2")
    (tmp_path / "tb.sv").write_text("module tb; endmodule\n")
    assert discover_files([str(tmp_path)], []) == [str(tmp_path / "tb.sv")]
    ss = build_source_set(ProjectConfig(workspace_roots=[str(tmp_path)]))
    assert ss.uvm_source == "bundled"
    assert ss.roots == [os.path.join(BUNDLED_UVM, "uvm_pkg.sv"), str(tmp_path / "tb.sv")]
