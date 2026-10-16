"""The native setup check must exercise wheels on every target and Verible only where shipped."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


def load():
    file = Path(__file__).resolve().parents[2] / "tools" / "platform_smoke.py"
    spec = importlib.util.spec_from_file_location("platform_smoke", file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("system,machine,target,formatter", [
    ("linux", "x86_64", "linux-x64", "verible-verilog-format"),
    ("win32", "AMD64", "win32-x64", "verible-verilog-format.exe"),
    ("darwin", "arm64", "darwin-arm64", "verible-verilog-format"),
    ("darwin", "x86_64", "darwin-x64", None),
])
def test_native_smoke_checks_only_shipped_formatter(monkeypatch, system, machine, target, formatter):
    smoke = load()
    calls = []
    created = []
    monkeypatch.setattr(sys, "argv", ["platform_smoke.py", "--target", target])
    monkeypatch.setattr(smoke, "sys", SimpleNamespace(platform=system))
    monkeypatch.setattr(smoke.platform, "machine", lambda: machine)
    monkeypatch.setattr(smoke.venv, "EnvBuilder", lambda **kwargs: SimpleNamespace(create=created.append))
    monkeypatch.setattr(smoke.subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs)))
    smoke.main()
    assert len(created) == 1
    wheels = Path(smoke.__file__).resolve().parents[1] / "client" / "server" / "wheels"
    assert calls[0][0][1:7] == ["-m", "pip", "install", "--no-index", "--find-links", str(wheels)]
    assert calls[1][0][1:] == ["-m", "uvmls", "--version"]
    assert calls[2][0][1:4] == ["-m", "uvmls", "lint"]
    assert all(kwargs["check"] and "UVM_HOME" not in kwargs["env"] for _, kwargs in calls)
    assert len(calls) == (4 if formatter else 3)
    if formatter:
        assert Path(calls[3][0][0]).parts[-2:] == (target, formatter)
    else:
        assert not any("verible" in arg for argv, _ in calls for arg in argv)
