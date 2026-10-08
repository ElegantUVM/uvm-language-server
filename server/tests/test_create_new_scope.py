"""Factory rewrites need a type scope valid at the constructor call."""

import pytest

from test_fixes import NEWS, findings, fix_once, index, offered, user_errors


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("ports", ["type T = int", "localparam int W = 8", "localparam type T = int"])
def test_create_new_default_specialization(tmp_path, local, ports):
    text = NEWS.replace("pdrv #(item) d;", "pdrv d;").replace(
        'd = pdrv#(item)::type_id::create("d", this)', 'd = new("d", this)')
    text = text.replace("class pdrv #(type T = int)", f"class pdrv #({ports})")
    if ports.startswith("localparam"):
        text = text.replace("`uvm_component_param_utils(pdrv #(T))", "`uvm_component_utils(pdrv)")
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


@pytest.mark.parametrize("param", ["type", "localparam type"])
@pytest.mark.parametrize("default", ["drv", "drv#(bit)"])
def test_create_new_keeps_a_bound_type_parameter(tmp_path, param, default):
    text = '''`include "uvm_macros.svh"
import uvm_pkg::*;
class drv #(type T = int) extends uvm_driver;
  `uvm_component_param_utils(drv#(T))
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
endclass
class env #(PARAM D = DEFAULT) extends uvm_env;
  `uvm_component_param_utils(env#(D))
  D d;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    d = new("d", this);
  endfunction
endclass
module top; env#() e; endmodule
'''.replace("PARAM", param).replace("DEFAULT", default)
    if param == "localparam type":
        text = text.replace("`uvm_component_param_utils(env#(D))", "`uvm_component_utils(env)")
    else:
        text = text.replace("env#() e;", "env#() e; env#(drv#(byte)) e2;")
    w = index(tmp_path, {"t.sv": text}, bundled=True)
    assert user_errors(w) == []
    [d] = findings(w, "create-new")
    assert d.message.endswith("use D::type_id::create")
    fix = fix_once(w, "create-new")
    assert fix.preferred and fix.title == "Create 'd' with D::type_id::create"
    assert (tmp_path / "t.sv").read_text() == text.replace('d = new(', 'd = D::type_id::create(')
    assert not findings(w, "create-new") and user_errors(w) == []


COMP = '''class NAME extends uvm_component;
  `uvm_component_utils(NAME)
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  function void foo(int a, int b); endfunction
endclass
'''
USER = '''class USER extends uvm_env;
  `uvm_component_utils(USER)
  my_comp c;
  function new(string name, uvm_component parent);
    super.new(name, parent);
    c = new("c", this);
    c.foo(1, 2);
  endfunction
endclass
'''
PKG = 'package agent_pkg;\nimport uvm_pkg::*;\n`include "uvm_macros.svh"\n' + COMP.replace('NAME', 'my_comp') + 'endpackage\n'
SCOPES = {
    # test_pkg sees my_comp through env_pkg's export
    'export': {'pkgs.sv': PKG + 'package env_pkg;\nimport agent_pkg::*;\nexport agent_pkg::*;\n'
               'typedef my_comp my_comp_t;\nendpackage\npackage test_pkg;\nimport uvm_pkg::*;\nimport env_pkg::*;\n'
               '`include "uvm_macros.svh"\n' + USER.replace('USER', 'my_env') + 'endpackage\n'},
    # each testbench declares its own my_comp in its module
    'modules': {f'{tb}_tb/top.sv': f'module {tb}_top;\nimport uvm_pkg::*;\n`include "uvm_macros.svh"\n' +
                COMP.replace('NAME', 'my_comp') + USER.replace('USER', f'{tb}_env') + 'endmodule\n'
                for tb in ('block', 'chip')},
    # each testbench has its own agent_pkg
    'packages': {f'{tb}_tb/pkgs.sv': PKG + f'package {tb}_pkg;\nimport uvm_pkg::*;\nimport agent_pkg::*;\n'
                 '`include "uvm_macros.svh"\n' + USER.replace('USER', f'{tb}_env') + 'endpackage\n'
                 for tb in ('block', 'chip')},
    # a $unit import is visible in a module; another testbench's $unit my_comp does not hide it
    'unit': {'a_tb/top.sv': PKG + 'import uvm_pkg::*;\nimport agent_pkg::*;\n`include "uvm_macros.svh"\n'
             'module top;\n' + USER.replace('USER', 'my_env') + 'endmodule\n',
             'b_tb/other.sv': 'class my_comp;\n  function new(int x, int y); endfunction\nendclass\n'},
    # a class or typedef of a module seen from a header the module includes
    'included': {'tb/top.sv': 'module top;\nimport uvm_pkg::*;\n`include "uvm_macros.svh"\n' +
                 COMP.replace('NAME', 'my_comp') + '`include "env.svh"\nendmodule\n',
                 'tb/env.svh': USER.replace('USER', 'my_env')},
    'included_typedef': {'tb/top.sv': 'module top;\nimport uvm_pkg::*;\n`include "uvm_macros.svh"\n' +
                         COMP.replace('NAME', 'my_comp') + 'typedef my_comp comp_t;\n`include "env.svh"\nendmodule\n',
                         'tb/env.svh': USER.replace('USER', 'my_env').replace('my_comp c;', 'comp_t c;')},
}
OWNERS = {'included': 'tb/top.sv', 'included_typedef': 'tb/top.sv'}


@pytest.mark.parametrize('case', SCOPES)
def test_create_new_and_signatures_see_the_visible_class(tmp_path, case):
    from uvmls.navigation import Navigator
    from uvmls.signatures import signature

    w = index(tmp_path, SCOPES[case], bundled=True)
    nav = Navigator(w)
    users = [name for name, text in SCOPES[case].items() if 'c = new(' in text]
    assert [str(tmp_path / f) for f in users] == [d.range.path for d in findings(w, 'create-new')]
    for name in users:
        path, text = str(tmp_path / name), SCOPES[case][name]
        for call, label in (('c = new(', 'function new(string name, uvm_component parent)'),
                            ('c.foo(', 'function void foo(int a, int b)')):
            i = text.index(call) + len(call)
            line = text.count('\n', 0, i)
            target, _, got, _, _ = signature(nav, path, line, i - text.rfind('\n', 0, i) - 1)
            assert got == label and target.owner.path == str(tmp_path / OWNERS.get(case, name))
