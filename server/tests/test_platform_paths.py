"""Editor URIs and index keys agree without folding case-sensitive path components."""
import ntpath
import os
from types import SimpleNamespace

import pytest
from pygls import uris

from uvmls import project
from uvmls.server import path_to_uri, uri_to_path


@pytest.mark.parametrize("name", ["Mixed Case.sv", "a#b%25.sv", "unicode-µ.sv"])
def test_file_uri_round_trip(tmp_path, name):
    file = tmp_path / name
    file.write_text("module top; endmodule\n", encoding="utf-8")
    key = project.norm(str(file))
    assert uri_to_path(path_to_uri(str(file))) == key
    assert os.path.isfile(key)
    assert os.path.basename(key) == name


@pytest.mark.parametrize("path", [r"C:\Design With Spaces\MixedCase.sv", r"c:\Design\a#b%25.sv",
                                  r"\\Server\Share\MixedCase.sv"])
def test_windows_uri_round_trip_on_every_host(monkeypatch, path):
    monkeypatch.setattr(uris, "IS_WIN", True)
    monkeypatch.setattr(project, "os", SimpleNamespace(path=ntpath))
    key = project.norm(path)
    assert uri_to_path(path_to_uri(path)) == key
    assert key.endswith(ntpath.basename(path))
    if path[1] == ":":
        assert key[0] == path[0].upper()
    else:
        assert key.startswith(r"\\Server\Share")


def test_windows_drive_aliases_share_one_key(monkeypatch):
    monkeypatch.setattr(project, "os", SimpleNamespace(path=ntpath))
    assert project.norm(r"C:\Design\Top.sv") == project.norm(r"c:\Design\Top.sv")
    assert project.norm(r"C:\Design\Top.sv") != project.norm(r"C:\design\top.sv")


@pytest.mark.parametrize("uri", ["untitled:Top.sv", "https://example.org/Top.sv"])
def test_non_file_uris_are_unchanged(uri):
    assert uri_to_path(uri) == uri


@pytest.mark.parametrize("system,machine,target,binary", [
    ("linux", "x86_64", "linux-x64", "verible-verilog-format"),
    ("win32", "AMD64", "win32-x64", "verible-verilog-format.exe"),
    ("win32", "x86_64", "win32-x64", "verible-verilog-format.exe"),
    ("darwin", "arm64", "darwin-arm64", "verible-verilog-format"),
])
def test_bundled_formatter_selection(tmp_path, monkeypatch, system, machine, target, binary):
    from uvmls import formatting
    file = tmp_path / target / binary
    file.parent.mkdir()
    file.write_bytes(b"formatter")
    file.chmod(0o755)
    monkeypatch.setattr(formatting, "BUNDLED_ROOT", str(tmp_path))
    monkeypatch.setattr(formatting, "sys", SimpleNamespace(platform=system))
    monkeypatch.setattr(formatting.platform, "machine", lambda: machine)
    assert formatting.bundled() == project.norm(str(file))
    file.unlink()
    assert formatting.bundled() is None


def test_intel_macos_never_uses_bundled_arm_formatter(tmp_path, monkeypatch):
    from uvmls import formatting
    file = tmp_path / "darwin-arm64" / "verible-verilog-format"
    file.parent.mkdir()
    file.write_bytes(b"arm64 binary")
    monkeypatch.setattr(formatting, "BUNDLED_ROOT", str(tmp_path))
    monkeypatch.setattr(formatting, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(formatting.platform, "machine", lambda: "x86_64")
    assert formatting.bundled() is None


def test_index_lookups_normalize_aliases(tmp_path):
    from uvmls.fixes import FileTexts
    from uvmls.model import Range
    from uvmls.navigation import Navigator
    from uvmls.signatures import call_at
    from uvmls.workspace import Workspace

    text = 'module top;\n  function void go(int n); endfunction\n  initial go(1);\nendmodule\n'
    file = tmp_path / 'MixedCase.sv'
    file.write_text(text, encoding='utf-8')
    ws = Workspace()
    ws.configure(project.ProjectConfig(workspace_roots=[str(tmp_path)], bundled_uvm=False))
    key = project.norm(str(file))
    alias = os.path.join(str(tmp_path), '.', file.name)
    if os.name == 'nt':
        alias = alias[0].lower() + alias[1:]
    assert key in ws.indices
    assert ws.indices[key].files == {key}
    assert ws.roots_for(alias) == [key]
    assert ws.lines_of(alias) == ws.lines_of(key)
    assert ws.symbols_in(alias) == ws.symbols_in(key)
    assert ws.enclosing(alias, 2, 12) == ws.enclosing(key, 2, 12)
    assert ws.units_at(Range(alias, 2, 12, 2, 12)) == ws.units_at(Range(key, 2, 12, 2, 12))
    assert ws.structures_in(alias) == ws.structures_in(key)
    assert ws.folds_in(alias) == ws.folds_in(key)
    assert ws.directives_in(alias) == ws.directives_in(key)
    assert ws.invocations_by_file.get(alias) == ws.invocations_by_file.get(key)
    assert call_at(ws, alias, 2, 13) == call_at(ws, key, 2, 13)
    nav = Navigator(ws)
    assert nav.definition(alias, 2, 11, text) == nav.definition(key, 2, 11, text)
    assert nav.bound_member_hints(alias) == nav.bound_member_hints(key)
    current = FileTexts({alias: (text + '// unsaved\n', 2)})
    assert current.get(key).text == text + '// unsaved\n'
    assert current.get(alias) is current.get(key)


@pytest.mark.parametrize('path,start,expected', [
    (r'C:\Design\Top.sv', r'C:\Design', 'Top.sv'),
    (r'c:\Design\Top.sv', r'D:\Workspace', r'C:\Design\Top.sv'),
    (r'\\Server\Share\Top.sv', r'C:\Design', r'\\Server\Share\Top.sv'),
])
def test_relative_or_absolute_across_windows_drives(monkeypatch, path, start, expected):
    monkeypatch.setattr(project, 'os', SimpleNamespace(path=ntpath))
    assert project.relative_or_absolute(path, start) == expected


def test_relative_or_absolute_defaults_to_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert project.relative_or_absolute(str(tmp_path / 'Top.sv')) == 'Top.sv'
