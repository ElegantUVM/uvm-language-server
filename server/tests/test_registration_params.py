"""Registration edits must not assign local parameters from a class port list."""

import pytest

from test_fixes import PARAMS, all_fixed, apply, findings, fix_once, index, user_errors


@pytest.mark.parametrize("ports,macro", [
    ("int W = 8, localparam int D = W * 2", "uvm_component_param_utils(drv #(W))"),
    ("parameter int W = 8, localparam int D = W * 2, type T = int",
     "uvm_component_param_utils(drv #(W))"),
    ("type T = int, localparam type U = T", "uvm_component_param_utils(drv #(T))"),
    ("localparam int D = 2, int E = 3", "uvm_component_utils(drv)"),
    ("localparam int D = 2, parameter int W = 8, int N = 3",
     "uvm_component_param_utils(drv #(W, N))"),
])
def test_register_leaves_out_localparams(tmp_path, ports, macro):
    text = '''`include "uvm_macros.svh"
import uvm_pkg::*;
class drv #(PORTS) extends uvm_driver;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
endclass
class env extends uvm_env;
  `uvm_component_utils(env)
  drv #() d;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    d = drv #()::type_id::create("d", this);
  endfunction
endclass
'''.replace("PORTS", ports)
    w = index(tmp_path, {"t.sv": text}, bundled=True)
    path = str(tmp_path / "t.sv")
    changes, fixed = all_fixed(w, path, "create-unregistered")
    assert len(fixed) == 1 and "`" + macro in apply(text, changes)
    fix = fix_once(w, "create-unregistered")
    assert fix.title == f"Add `{macro} to class 'drv'" and fix.safe
    assert not findings(w, "create-unregistered") and user_errors(w) == []


@pytest.mark.parametrize("local", ["localparam int M = 3", "localparam type U = int"])
def test_utils_param_leaves_out_localparams(tmp_path, local):
    text = PARAMS.replace("type T = int, int N = 2", "type T = int, int N = 2, " + local) \
        .replace("`uvm_component_param_utils(drv #(T, N))", "`uvm_component_utils(drv)")
    w = index(tmp_path, {"t.sv": '`include "uvm_macros.svh"\n' + text}, bundled=True)
    assert user_errors(w) == []
    fix = fix_once(w, "utils-param")
    assert fix.title == "Change to `uvm_component_param_utils(drv #(T, N))"
    assert not findings(w, "utils-param") and user_errors(w) == []
