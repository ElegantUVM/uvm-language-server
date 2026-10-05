"""Registration diagnostics and fixes must recommend the same macro invocation."""

import pytest

from test_fixes import findings, index, offered


@pytest.mark.parametrize("base,kind,parent", [
    ("uvm_driver", "component", ", this"), ("uvm_object", "object", ""),
])
def test_parameterized_registration_message_matches_fix(tmp_path, base, kind, parent):
    text = '''import uvm_pkg::*;
class target #(type T = int, int N = 2) extends BASE;
endclass
class env extends uvm_env;
  target #(int, 2) a;
  target #(byte, 4) b;
  function void build_phase(uvm_phase phase);
    a = target #(int, 2)::type_id::create("a"PARENT);
    b = target #(byte, 4)::type_id::create("b"PARENT);
  endfunction
endclass
'''.replace("BASE", base).replace("PARENT", parent)
    w = index(tmp_path, {"t.sv": text})
    ds = findings(w, "create-unregistered")
    assert len(ds) == 2
    invocation = f"`uvm_{kind}_param_utils(target #(T, N))"
    for d in ds:
        assert d.message == f"target is not registered with the factory (add {invocation})"
        [fix] = offered(w, d)
        assert fix.title == f"Add {invocation} to class 'target'"
