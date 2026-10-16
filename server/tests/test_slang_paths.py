import ctypes
import os
import shutil
import sys
from types import SimpleNamespace

import pytest

from uvmls import project
from uvmls.elab import elaborate
from uvmls.indexer import Indexer


@pytest.fixture
def windows_paths(monkeypatch):
    monkeypatch.setattr(project, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(project, "_SLANG_PATHS", {})
    monkeypatch.setattr(project, "_NATIVE_PATHS", {})


def test_windows_path_buffer_grows(monkeypatch):
    calls = []
    result = "C:\\CAFE~1\\top.sv"

    def convert(path, buffer, size):
        calls.append((path, size))
        if not size:
            return 4
        if size <= len(result):
            return len(result) + 1
        buffer.value = result
        return len(result)

    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: SimpleNamespace(GetShortPathNameW=convert), raising=False)
    assert project._windows_path("C:\\café\\top.sv", "GetShortPathNameW") == result
    assert [size for _, size in calls] == [0, 4, len(result) + 2]


@pytest.mark.parametrize("failure_at", [0, 1])
def test_windows_path_api_failure_keeps_the_path(monkeypatch, failure_at):
    calls = []

    def convert(path, buffer, size):
        calls.append(size)
        return 0 if len(calls) > failure_at else 20

    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: SimpleNamespace(GetLongPathNameW=convert), raising=False)
    assert project._windows_path("missing~1", "GetLongPathNameW") == "missing~1"


def test_slang_path_round_trip_and_cache(tmp_path, monkeypatch, windows_paths):
    native, short = str(tmp_path / "café" / "top.sv"), str(tmp_path / "CAFE~1" / "top.sv")
    calls = []

    def convert(path, function):
        calls.append((path, function))
        return short if function == "GetShortPathNameW" else native

    monkeypatch.setattr(project, "_windows_path", convert)
    assert project.slang_path(native) == short
    assert project.slang_path(native) == short
    assert project.from_slang_path(short) == project.norm(native)
    assert calls == [(native, "GetShortPathNameW")]
    project._SLANG_PATHS.clear()
    project._NATIVE_PATHS.clear()
    assert project.from_slang_path(short) == project.norm(native)
    assert project.from_slang_path(short) == project.norm(native)
    assert project.slang_path(native) == short
    assert calls[-1] == (short, "GetLongPathNameW") and len(calls) == 2
    monkeypatch.setattr(project, "_PATH_CACHE_LIMIT", 1)
    assert project.slang_path(native + ".other") == short
    assert len(project._SLANG_PATHS) == len(project._NATIVE_PATHS) == 1


@pytest.mark.parametrize("converted", ["unchanged", "non-ascii"])
def test_unavailable_short_names_are_not_cached(tmp_path, monkeypatch, windows_paths, converted):
    native = str(tmp_path / "café.sv")
    native_path = tmp_path / "café.sv"
    native_path.write_bytes(b"module top; endmodule\n")
    monkeypatch.setattr(project, "_windows_path", lambda p, fn: p if converted == "unchanged" else p + "é")
    assert project.slang_path(native) == native
    assert not project._SLANG_PATHS
    message = (f"slang cannot open non-ASCII path '{native}' on Windows and the volume has no short names; "
               "move the project or enable them (fsutil 8dot3name)")
    assert project.slang_path_error(native) == message
    assert elaborate({"roots": [native]})["error"] == message
    monkeypatch.setattr(project, "_windows_path", lambda p, fn: str(tmp_path / "CAFE~1.SV"))
    assert project.slang_path(native) != native  # a failed lookup must not outlive file creation


def test_indexer_logs_unavailable_short_names_once(tmp_path, monkeypatch, windows_paths, caplog):
    native = tmp_path / "café.sv"
    native.write_bytes(b"module top; endmodule\n")
    monkeypatch.setattr(project, "_windows_path", lambda p, fn: p)
    indexer = Indexer([], {}, use_shim=False)
    indexer.parse(str(native))
    indexer.parse(str(native))
    assert caplog.messages == [project.slang_path_error(str(native))]


def test_ascii_and_non_windows_paths_do_not_call_windows(tmp_path, monkeypatch):
    def unexpected(*args):
        pytest.fail("unexpected Windows path conversion")

    monkeypatch.setattr(project, "_windows_path", unexpected)
    monkeypatch.setattr(project, "sys", SimpleNamespace(platform="linux"))
    path = str(tmp_path / "café~1.sv")
    assert project.slang_path(path) == path
    assert project.from_slang_path(path) == project.norm(path)
    monkeypatch.setattr(project, "sys", SimpleNamespace(platform="win32"))
    path = str(tmp_path / "top.sv")
    assert project.slang_path(path) == path
    assert project.from_slang_path(path) == project.norm(path)


@pytest.fixture
def mapped_sources(tmp_path, monkeypatch):
    root, includes = tmp_path / "café", tmp_path / "entête"
    root.mkdir()
    includes.mkdir()
    top, header = root / "top.sv", includes / "types.svh"
    top.write_bytes(b'`include "types.svh"\nmodule top; endmodule\n')
    header.write_bytes(b"class stored; endclass\n")
    alias_root, alias_includes = tmp_path / "CAFE~1", tmp_path / "HEAD~1"
    shutil.copytree(root, alias_root)
    shutil.copytree(includes, alias_includes)
    pairs = [(str(root), str(alias_root)), (str(includes), str(alias_includes))]
    sent, received = [], []

    def replace(path, pairs):
        path = project.norm(path)
        for old, new in pairs:
            if path == old or path.startswith(old + os.sep):
                return new + path[len(old):]
        return path

    def to_slang(path):
        sent.append(path)
        return replace(path, pairs)

    def from_slang(path):
        received.append(path)
        return replace(path, [(alias, native) for native, alias in pairs])

    monkeypatch.setattr(project, "slang_path", to_slang)
    monkeypatch.setattr(project, "from_slang_path", from_slang)
    return top, header, sent, received


@pytest.mark.parametrize("overrides", [False, True])
def test_indexer_round_trip_through_slang_paths(mapped_sources, overrides):
    top, header, sent, received = mapped_sources
    edits = {str(top): '\r\n`include "types.svh"\r\n`include "missing.svh"\r\nmodule edited_top; endmodule\r\n',
             str(header): "\r\nclass edited; endclass\r\n"} if overrides else {}
    idx = Indexer([str(header.parent)], {}, use_shim=False).parse(str(top), edits, complete=True)
    assert idx.files == {str(top), str(header)}
    [cls] = idx.classes
    assert (cls.name, cls.range.path, cls.range.line, cls.range.col) == (
        "edited" if overrides else "stored", str(header), 1 if overrides else 0, 6)
    [unit] = idx.units
    assert (unit.name, unit.range.path, unit.range.line) == (
        "edited_top" if overrides else "top", str(top), 3 if overrides else 1)
    assert idx.includes[str(top)][0][0] == str(header)
    if overrides:
        [diag] = idx.diagnostics
        assert diag.code == "CouldNotOpenIncludeFile" and diag.range.path == str(top)
        assert diag.range.line == 2 and diag.data == {"include": "missing.svh"}
    else:
        assert idx.diagnostics == []
    assert str(top) in sent and str(header.parent) in sent
    assert any("CAFE~1" in path for path in received)
    assert any("HEAD~1" in path for path in received)


def test_elaboration_round_trip_through_slang_paths(mapped_sources):
    top, header, sent, received = mapped_sources
    req = {"roots": [str(top)], "includeDirs": [str(header.parent)], "tops": ["edited_top"],
           "overrides": {str(top): '\r\n`include "types.svh"\r\nmodule edited_top; endmodule\r\n',
                         str(header): "\r\nclass edited;\r\n int value;\r\n int value;\r\nendclass\r\n"}}
    result = elaborate(req)
    assert result["error"] is None, result
    assert result["tops"] == ["edited_top"]
    [diag] = result["diagnostics"]
    assert (diag["file"], diag["line"], diag["col"], diag["code"]) == (str(header), 3, 5, "Redefinition")
    assert [(note["file"], note["line"], note["col"]) for note in diag["related"]] == [(str(header), 2, 5)]
    assert str(top) in sent and str(header) in sent and str(header.parent) in sent
    assert "edited_top" not in sent
    assert any("HEAD~1" in path for path in received)
    clean = elaborate({"roots": [str(top)], "includeDirs": [str(header.parent)]})
    assert clean["error"] is None and clean["diagnostics"] == [] and clean["tops"] == ["top"]


@pytest.mark.parametrize("flag", ["-f", "-F"])
def test_driver_filelist_path_is_converted(mapped_sources, flag):
    top, header, sent, received = mapped_sources
    filelist = top.parent / "files.f"
    alias = top.parent.parent / "CAFE~1" / "files.f"
    content = f'"{top.parent.parent / "CAFE~1" / "top.sv"}"\n'
    filelist.write_text(content, encoding="utf-8", newline="")
    alias.write_text(content, encoding="utf-8", newline="")
    result = elaborate({"roots": [], "includeDirs": [str(header.parent)], "args": [flag, str(filelist)]})
    assert result["error"] is None and result["diagnostics"] == [], result
    assert result["tops"] == ["top"] and str(filelist) in sent


@pytest.mark.parametrize("flag", ["-I", "--include-directory"])
def test_driver_include_argument_is_converted(mapped_sources, flag):
    top, header, sent, received = mapped_sources
    result = elaborate({"roots": [str(top)], "args": [flag, str(header.parent)]})
    assert result["error"] is None and result["diagnostics"] == [], result
    assert result["tops"] == ["top"] and str(header.parent) in sent


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows 8.3 filesystem names")
@pytest.mark.parametrize("header_name", ["types.svh", "café.svh"])
def test_real_windows_unicode_project(tmp_path, header_name):
    root = tmp_path / "café"
    root.mkdir()
    top, header = root / "top.sv", root / header_name
    top.write_bytes(f'`include "{header_name}"\nmodule top; endmodule\n'.encode("utf-8"))
    header.write_bytes(b"class stored; endclass\n")
    if not all(project.slang_path(str(p)).isascii() for p in (top, header)):
        pytest.skip("ASCII 8.3 short names are unavailable for this path; the volume may have them disabled")
    assert project.from_slang_path(project.slang_path(str(top))) == project.norm(str(top))
    idx = Indexer([str(root)], {}, use_shim=False).parse(str(top), {str(header): "\nclass edited; endclass\n"})
    assert [cls.name for cls in idx.classes] == ["edited"], idx.diagnostics
    [cls] = idx.classes
    assert (cls.name, cls.range.path, cls.range.line) == ("edited", str(header), 1)
    result = elaborate({"roots": [str(top)], "includeDirs": [str(root)],
                        "overrides": {str(top): f'`include "{header_name}"\nmodule top; initial missing = 1; endmodule\n'}})
    assert result["error"] is None, result
    [diag] = result["diagnostics"]
    assert (diag["file"], diag["line"], diag["code"]) == (str(top), 1, "UndeclaredIdentifier")
