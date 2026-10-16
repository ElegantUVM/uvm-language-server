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
        assert key[0] == path[0].lower()
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
