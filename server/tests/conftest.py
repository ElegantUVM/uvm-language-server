import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))

from uvmls.project import ProjectConfig  # noqa: E402
from uvmls.workspace import Workspace  # noqa: E402

EXAMPLE = os.path.normpath(os.path.join(HERE, "..", "..", "examples", "alu_tb"))
FILELIST = os.path.join(EXAMPLE, "alu_tb.f")


@pytest.fixture(scope="session")
def ws():
    """The example on the built-in UVM model (macro shim + class table)."""
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[EXAMPLE], filelists=[FILELIST], bundled_uvm=False))
    return w


@pytest.fixture(scope="session")
def uvm_ws():
    """Same testbench with the real UVM library; skipped unless UVM_HOME is set."""
    home = os.environ.get("UVM_HOME")
    if not home:
        pytest.skip("UVM_HOME not set")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[EXAMPLE], filelists=[FILELIST], uvm_home=home))
    return w


def position(rel: str, needle: str, offset: int = 0, occurrence: int = 0):
    """(path, line, col, text) of `needle` (plus offset) in an example file."""
    path = os.path.join(EXAMPLE, rel)
    with open(path) as f:
        text = f.read()
    idx = -1
    for _ in range(occurrence + 1):
        idx = text.index(needle, idx + 1)
    idx += offset
    line = text.count("\n", 0, idx)
    col = idx - (text.rfind("\n", 0, idx) + 1)
    return path, line, col, text
