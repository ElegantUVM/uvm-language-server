"""Non-ANSI formal declarations count as constructor arguments, but cannot have defaults."""

import pytest

from test_fixes import CTORS, all_fixed, findings, fix_once, index, offered, user_errors


@pytest.mark.parametrize("parent_type", ["uvm_component", "int"])
def test_factory_ctor_non_ansi_component(tmp_path, parent_type):
    text = CTORS.replace("function new(string name, uvm_component parent);",
                         "function new; input string name; input " + parent_type + " parent;")
    w = index(tmp_path, {"t.sv": '`include "uvm_macros.svh"\n' + text}, bundled=True)
    ds = findings(w, "factory-ctor")
    if parent_type == "uvm_component":
        assert ds == [] and user_errors(w) == []
    else:
        [d] = ds
        assert "the factory passes a uvm_component as argument 2" in d.message
        assert offered(w, d) == []


def test_factory_ctor_non_ansi_object_has_no_default_fix(tmp_path):
    text = CTORS.replace('function new(string name = "item");', 'function new; input string name;')
    w = index(tmp_path, {"t.sv": text})
    [d] = findings(w, "factory-ctor")
    assert "new()" in d.message and "ANSI" in d.message
    assert offered(w, d) == []
    assert all_fixed(w, str(tmp_path / "t.sv"), "factory-ctor") == ([], [])


def test_factory_ctor_accepts_non_ansi_base_arguments(tmp_path):
    text = CTORS.replace("function new(string name, uvm_component parent);",
                         "function new; input string name; input uvm_component parent;") + '''
class child extends drv;
  `uvm_component_utils(child)
endclass
'''
    w = index(tmp_path, {"t.sv": '`include "uvm_macros.svh"\n' + text}, bundled=True)
    ds = findings(w, "factory-ctor")
    assert [d.data["cls"] for d in ds] == ["child"]
    fix = fix_once(w, "factory-ctor")
    assert fix.safe and fix.preferred
    assert findings(w, "factory-ctor") == [] and user_errors(w) == []
