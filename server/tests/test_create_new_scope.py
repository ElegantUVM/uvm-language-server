"""Factory rewrites need a type scope valid at the constructor call."""

import pytest

from test_fixes import NEWS, findings, fix_once, index, offered, user_errors


@pytest.mark.parametrize("local", [False, True])
def test_create_new_default_specialization(tmp_path, local):
    text = NEWS.replace("pdrv #(item) d;", "pdrv d;").replace(
        'd = pdrv#(item)::type_id::create("d", this)', 'd = new("d", this)')
    if local:
        text = text.replace("  pdrv d;\n", "").replace("    mon local_m;", "    mon local_m;\n    pdrv d;")
    w = index(tmp_path, {"t.sv": '`include "uvm_macros.svh"\n' + text}, bundled=True)
    assert user_errors(w) == []
    [d] = findings(w, "create-new")
    assert d.message.endswith("use pdrv#()::type_id::create")
    fix = fix_once(w, "create-new")
    assert fix.preferred and fix.title == "Create 'd' with pdrv#()::type_id::create"
    assert 'd = pdrv#()::type_id::create("d", this)' in (tmp_path / "t.sv").read_text()
    assert not findings(w, "create-new") and user_errors(w) == []


@pytest.mark.parametrize("decl", ["agent", "agent_t"])
def test_create_new_withholds_an_inherited_type_spelling(tmp_path, decl):
    text = '''`include "uvm_macros.svh"
package agent_pkg;
  import uvm_pkg::*;
  class agent extends uvm_agent;
    `uvm_component_utils(agent)
    function new(string name, uvm_component parent); super.new(name, parent); endfunction
  endclass
endpackage
package env_pkg;
  import uvm_pkg::*;
  import agent_pkg::*;
  typedef agent agent_t;
  class base_env extends uvm_env;
    DECL a;
    function new(string name, uvm_component parent); super.new(name, parent); endfunction
  endclass
endpackage
package test_pkg;
  import uvm_pkg::*;
  import env_pkg::*;
  class child_env extends base_env;
    function new(string name, uvm_component parent); super.new(name, parent); endfunction
    function void build_phase(uvm_phase phase);
      a = new("a", this);
    endfunction
  endclass
endpackage
'''.replace("DECL", decl)
    w = index(tmp_path, {"t.sv": text}, bundled=True)
    assert user_errors(w) == []
    [d] = findings(w, "create-new")
    assert offered(w, d) == []


def test_create_new_default_specialization_through_typedef(tmp_path):
    text = NEWS.replace("typedef mon mon_t;", "typedef pdrv mon_t;").replace(
        'mt = mon_t::type_id::create("mt", this)', 'mt = new("mt", this)')
    w = index(tmp_path, {"t.sv": '`include "uvm_macros.svh"\n' + text}, bundled=True)
    assert user_errors(w) == []
    fix_once(w, "create-new")
    assert 'mt = mon_t::type_id::create("mt", this)' in (tmp_path / "t.sv").read_text()
    assert not findings(w, "create-new") and user_errors(w) == []
