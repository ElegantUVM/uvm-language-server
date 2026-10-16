"""The release files the wheel and the .vsix ship must stay in step with the repository's."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
ROOT = SERVER.parent


def test_license_and_notice_are_copies_of_the_root_files():
    for name in ("LICENSE", "NOTICE"):
        assert (SERVER / name).read_text(encoding="utf-8") == (ROOT / name).read_text(encoding="utf-8"), f"server/{name} differs from {name}"
    for name in ("LICENSE", "NOTICE", "CHANGELOG.md"):
        assert (ROOT / "client" / name).read_text(encoding="utf-8") == (ROOT / name).read_text(encoding="utf-8"), f"client/{name} differs"


def test_license_is_apache_2_and_notice_names_the_bundled_uvm():
    assert "Apache License" in (ROOT / "LICENSE").read_text(encoding="utf-8").splitlines()[0]
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "UVM 1.2" in notice and "Mentor Graphics" in notice and "Cadence" in notice and "Synopsys" in notice


def test_manifests_agree_on_licence_version_and_python_floor():
    pyproject = tomllib.loads((SERVER / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert pyproject["license"] == "Apache-2.0"
    assert pyproject["requires-python"] == ">=3.11"
    import json
    manifest = json.loads((ROOT / "client" / "package.json").read_text(encoding="utf-8"))
    assert manifest["license"] == "Apache-2.0"
    assert manifest["version"] == pyproject["version"]
    from uvmls import __version__
    assert __version__ == pyproject["version"]
    # The client's interpreter check must ask for the same floor the wheel declares.
    extension = (ROOT / "client" / "src" / "extension.ts").read_text(encoding="utf-8")
    assert "sys.version_info >= (3, 11)" in extension
    assert not re.search(r"Python 3\.10", extension)


def test_cli_reports_its_version(capsys):
    import pytest
    from uvmls import __version__, cli
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith(f"uvmls {__version__} (pyslang ")
