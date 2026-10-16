import os

import pytest

from uvmls.project import (
    BUNDLED_UVM, DEFAULT_EXCLUDES, ProjectConfig, build_source_set, discover_files, is_bundled,
    parse_filelist, uvm_version,
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
    assert [os.path.relpath(r, EXAMPLE).replace(os.sep, "/") for r in ss.roots][:2] == ["rtl/alu.sv", "agent/alu_if.sv"]
    assert os.path.join(EXAMPLE, "agent") in ss.include_dirs
    assert not ss.has_real_uvm_macros


def test_discovery_mode_skips_included_files():
    ss = build_source_set(ProjectConfig(workspace_roots=[EXAMPLE]))
    rel = {os.path.relpath(r, EXAMPLE).replace(os.sep, "/") for r in ss.roots}
    assert ss.mode == "discovery"
    assert "agent/alu_pkg.sv" in rel
    assert "agent/alu_driver.svh" not in rel  # included by alu_pkg.sv


@pytest.mark.parametrize("strict", [False, True])
def test_filelist_options(tmp_path, monkeypatch, strict):
    monkeypatch.setenv("MY_IP", str(tmp_path / "ip"))
    (tmp_path / "ip").mkdir()
    (tmp_path / "ip" / "ip.f").write_text("+incdir+$MY_IP/inc\n$(MY_IP)/ip_pkg.sv\n")
    (tmp_path / "top.f").write_text(
        "// comment\n-timescale=1ns/1ps +acc -sverilog\n"
        "+define+A=1+B -DC=2\n-f ${MY_IP}/ip.f\n"
        "-ntb_opts uvm\n"
        "tb.sv  # trailing comment\n"
    )
    r = parse_filelist(str(tmp_path / "top.f"), strict=strict)
    assert r.defines == {"A": "1", "B": "", "C": "2"}
    assert r.include_dirs == [str(tmp_path / "ip" / "inc")]
    assert r.files == [str(tmp_path / "ip" / "ip_pkg.sv"), str(tmp_path / "tb.sv")]
    assert r.timescale == "1ns/1ps"
    (tmp_path / "xrun.f").write_text("-timescale 1ps/1fs\ntb.sv\n")
    assert parse_filelist(str(tmp_path / "xrun.f"), strict=strict).timescale == "1ps/1fs"


@pytest.mark.parametrize("flag", ["-f", "-F", "-file", "-filelist"])
def test_filelist_read_errors_can_be_fatal(tmp_path, flag):
    path = tmp_path / "tb.f"
    path.write_text(f"top.sv\n{flag} absent.f\n")
    result = parse_filelist(str(path))
    assert result.files == [str(tmp_path / "top.sv")]
    assert len(result.warnings) == 1 and "cannot read filelist" in result.warnings[0]
    with pytest.raises(FileNotFoundError) as exc:
        parse_filelist(str(path), strict=True)
    assert exc.value.filename == str(tmp_path / "absent.f")


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


@pytest.mark.parametrize("how", ["setting", "env"])
@pytest.mark.parametrize("mode", ["filelist", "discovery"])
def test_configured_uvm_replaces_the_projects_own(tmp_path, monkeypatch, how, mode):
    home = fake_uvm(tmp_path / "home", "1.2")
    proj = tmp_path / "proj"
    own = fake_uvm(proj / "ext" / "uvm", "1.1d")
    (proj / "tb.sv").write_text("module tb; endmodule\n")
    (proj / "tb.f").write_text(f"+incdir+{own}\n{own}/uvm_pkg.sv\ntb.sv\n")
    settings = {"filelists": ["tb.f"]} if mode == "filelist" else {}
    monkeypatch.delenv("UVM_HOME", raising=False)
    if how == "env":
        monkeypatch.setenv("UVM_HOME", str(tmp_path / "home"))
    else:
        settings["uvmHome"] = str(tmp_path / "home")
    ss = build_source_set(ProjectConfig.from_settings([str(proj)], settings))
    assert (ss.uvm_source, ss.uvm_dir, ss.uvm_version) == ("configured", home, "1.2")
    assert ss.roots == [os.path.join(home, "uvm_pkg.sv"), str(proj / "tb.sv")]
    assert not [d for d in ss.include_dirs if d == own or d.startswith(own + os.sep)]
    assert ss.warnings == [f"the UVM library in {own} is left out: {home} (uvmHome or $UVM_HOME) replaces it"]


def test_the_configured_uvm_under_another_name_is_compiled_once(tmp_path):
    home = fake_uvm(tmp_path / "home", "1.2")
    (tmp_path / "link").symlink_to(tmp_path / "home", target_is_directory=True)
    (tmp_path / "tb.sv").write_text("module tb; endmodule\n")
    (tmp_path / "tb.f").write_text(f"+incdir+{home}\n{home}/uvm_pkg.sv\ntb.sv\n")
    ss = build_source_set(ProjectConfig.from_settings(
        [str(tmp_path)], {"uvmHome": str(tmp_path / "link"), "filelists": ["tb.f"]}))
    assert ss.uvm_dir == str(tmp_path / "link" / "src")
    assert ss.roots == [str(tmp_path / "link" / "src" / "uvm_pkg.sv"), str(tmp_path / "tb.sv")]
    assert home not in ss.include_dirs and ss.warnings == []  # the same kit: nothing to warn about


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


@pytest.mark.parametrize("name", [p.removeprefix("**/").removesuffix("/**") for p in DEFAULT_EXCLUDES])
def test_default_excludes_apply_below_the_workspace_root(tmp_path, name):
    # A project kept in ~/work is indexed; a work/ directory inside it (a simulator library) is not.
    root = tmp_path / name / "proj"
    for d in (root, root / name, root / "sim" / name):
        d.mkdir(parents=True)
        (d / "tb.sv").write_text("module tb; endmodule\n")
    assert discover_files([str(root)], []) == [str(root / "tb.sv")]
    assert build_source_set(ProjectConfig(workspace_roots=[str(root)])).roots[-1] == str(root / "tb.sv")


def test_exclude_patterns_are_globs_below_each_root_unless_absolute(tmp_path):
    roots = [tmp_path / "b", tmp_path / "work" / "a"]
    for root in roots:
        for f in ("tb.sv", "legacy/old.sv", "rtl/legacy/old.sv", "rtl/alu.sv", "rtl/alu_old.sv"):
            (root / f).parent.mkdir(parents=True, exist_ok=True)
            (root / f).write_text("module m; endmodule\n")
    every = sorted(str(p) for p in tmp_path.rglob("*.sv"))

    def left_out(*exclude):
        kept = discover_files([str(r) for r in roots], list(exclude))
        return [os.path.relpath(f, tmp_path).replace(os.sep, "/") for f in every if f not in kept]

    def in_both(*files):
        return sorted(f"{r}/{f}" for r in ("b", "work/a") for f in files)

    assert left_out("legacy/**") == in_both("legacy/old.sv")  # not rtl/legacy
    assert left_out("**/legacy") == in_both("legacy/old.sv", "rtl/legacy/old.sv")
    assert left_out("*.sv", "rtl/*_old.sv") == in_both("tb.sv", "rtl/alu_old.sv")  # * stays in one directory
    assert left_out("legacy/**/old.sv", "{rtl/alu,tb}.sv") == in_both("legacy/old.sv", "rtl/alu.sv", "tb.sv")
    assert left_out("**/a/**", "**/work/**") == []  # the roots' own paths are not matched
    assert left_out("[z-a]/**") == []  # a pattern that cannot compile leaves nothing out
    # An absolute pattern names one root's files.
    assert left_out(f"{roots[0]}/rtl/**") == ["b/rtl/alu.sv", "b/rtl/alu_old.sv", "b/rtl/legacy/old.sv"]
    settings = {"exclude": [f"{roots[0]}/legacy/**"]}
    ss = build_source_set(ProjectConfig.from_settings([str(roots[0])], settings))
    assert str(roots[0] / "legacy" / "old.sv") not in ss.roots and str(roots[0] / "tb.sv") in ss.roots
