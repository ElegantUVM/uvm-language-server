import os
import re

from uvmls.navigation import Navigator, expr_at

from conftest import EXAMPLE, position


def rel(r):
    return f"{os.path.relpath(r.path, EXAMPLE)}:{r.line + 1}"


def defs(ws, *args, **kw):
    path, line, col, text = position(*args, **kw)
    return [rel(r) for r in Navigator(ws).definition(path, line, col, text)]


def hover(ws, *args, **kw):
    path, line, col, text = position(*args, **kw)
    return Navigator(ws).hover(path, line, col, text) or ""


def test_expr_at():
    e = expr_at("    seq.start(m_env.m_agent.m_sequencer);", 34)
    assert e.segments == ["m_env", "m_agent", "m_sequencer"]
    e = expr_at("x = my_env #(8)::type_id::create(", 26)
    assert e.segments == ["my_env", "type_id", "create"] and e.seps == ["::", "::"]


def test_strict_member_typedef_preserves_navigation_fallback(tmp_path):
    from test_fixes import index

    text = '''package first;
class box; static function void go(int a); endfunction endclass
endpackage
package second;
class box; static function void go(string b); endfunction endclass
endpackage
package calls;
import first::*;
import second::*;
class caller;
 typedef box alias_t;
 function void test(); alias_t::go(1); endfunction
endclass
endpackage
'''
    ws = index(tmp_path, {'t.sv': text})
    nav = Navigator(ws)
    path = str(tmp_path / 't.sv')
    line = next(i for i, s in enumerate(text.splitlines()) if 'alias_t::go' in s)
    col = text.splitlines()[line].index('go(')
    ce = expr_at(text.splitlines()[line], col)
    assert nav.resolve_expr(path, line, col, ce, strict=True) == []
    [target] = nav.resolve_expr(path, line, col, ce)
    assert target.owner.package == 'first'
    assert 'go' in nav.hover(path, line, col, text)


def test_types_scoped_by_a_specialization_resolve(tmp_path):
    from test_fixes import findings, index
    from uvmls import signatures
    from uvmls.views import topology

    text = '''package agent_pkg;
import uvm_pkg::*;
`include "uvm_macros.svh"
class cfg_a; function void apply(int level); endfunction endclass
class my_drv extends uvm_component;
  `uvm_component_utils(my_drv)
  cfg_a cfg;
  int field;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
endclass
class my_agent #(int W = 8) extends uvm_agent;
  `uvm_component_param_utils(my_agent#(W))
  typedef my_drv drv_t;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
endclass
class holder #(type T = my_agent#(2));
  T::drv_t inner;
endclass
class my_env extends uvm_env;
  `uvm_component_utils(my_env)
  my_agent #(4) :: drv_t d1;
  holder#() h;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    d1 = new("d1", this);
    d1.field = 1;
    d1.cfg.apply(1);
    h.inner.field = 2;
  endfunction
endclass
class my_test extends uvm_test;
  `uvm_component_utils(my_test)
  my_env env;
  function new(string name, uvm_component parent); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    env = my_env::type_id::create("env", this);
  endfunction
endclass
endpackage
'''
    ws = index(tmp_path, {'pkg.sv': text}, bundled=True)
    nav = Navigator(ws)
    path = str(tmp_path / 'pkg.sv')
    lines = text.splitlines()
    drv = lines.index('class my_drv extends uvm_component;')

    def at(needle, offset=0):
        line = next(i for i, s in enumerate(lines) if needle in s)
        return line, lines[line].index(needle) + offset

    for receiver in ('d1.field', 'h.inner.field'):
        line, col = at(receiver, receiver.rindex('.') - 1)
        assert [(r.line, r.col) for r in nav.type_definition(path, line, col, text)] == [(drv, 6)]
        line, col = at(receiver, receiver.rindex('.') + 1)
        assert 'field' in {c['label'] for c in nav.completions(path, line, col, text)}
    assert 'type `drv_t` = `my_drv` (component)' in nav.hover(path, *at('d1.field'), text)
    help = signatures.signature(nav, path, *at('d1.cfg.apply(', len('d1.cfg.apply(')))
    assert help is not None and help[2] == 'function void apply(int level)'
    [d] = findings(ws, 'create-new')
    assert d.message.startswith('d1 is created with new()')
    env = topology(ws, 'my_test')['root']['children'][0]
    assert [c['name'] for c in env['children']] == ['d1']


def test_member_through_config_object(ws):
    assert defs(ws, "agent/alu_driver.svh", "m_cfg.idle_cycles", 6) == ["agent/alu_agent_config.svh:8"]


def test_chain_through_components(ws):
    assert defs(ws, "tests/alu_base_test.svh", "m_env.m_agent.m_sequencer", 14) == ["agent/alu_agent.svh:6"]


def test_virtual_interface_clocking_signal(ws):
    assert defs(ws, "agent/alu_driver.svh", "vif.drv_cb.valid", 11) == ["agent/alu_if.sv:2"]
    assert defs(ws, "agent/alu_driver.svh", "vif.drv_cb.valid", 4) == ["agent/alu_if.sv:9"]


def test_type_parameter_member(any_ws):
    # req is declared in uvm_driver as REQ; alu_driver binds REQ = alu_seq_item.
    assert "alu_seq_item" in hover(any_ws, "agent/alu_driver.svh", "get_next_item(req)", 14)
    assert defs(any_ws, "agent/alu_driver.svh", "item.op", 5) == ["agent/alu_seq_item.svh:3"]


def test_extern_method_goes_to_both(ws):
    assert defs(ws, "agent/alu_driver.svh", "drive_item(req)") == [
        "agent/alu_driver.svh:32", "agent/alu_driver.svh:20"]


def test_typedef_and_enum_and_package(ws):
    assert defs(ws, "agent/alu_agent.svh", "alu_sequencer    m_sequencer") == ["agent/alu_sequencer.svh:1"]
    assert defs(ws, "agent/alu_seq_item.svh", "ALU_SUB -> a", 1) == ["agent/alu_pkg.sv:5"]
    assert defs(ws, "tb/tb_top.sv", "alu_test_pkg::*", 2) == ["tests/alu_test_pkg.sv:1"]


def test_class_hover_shows_role_and_chain(ws):
    h = hover(ws, "tests/alu_base_test.svh", "class alu_base_test", 6)
    assert "**test**" in h and "4 subclasses" in h and "alu_base_test → uvm_test" in h


def test_uvm_macro_hover_without_library(ws):
    assert "uvm_component_utils(T)" in hover(ws, "agent/alu_monitor.svh", "`uvm_component_utils", 1)


def test_completion_members(ws):
    path, line, col, text = position("agent/alu_driver.svh", "drive_item(req);", 14)
    text = text.replace("drive_item(req);", "drive_item(req.);", 1)
    labels = [c["label"] for c in Navigator(ws).completions(path, line, col + 1, text)]
    assert labels[:4] == ["op", "a", "b", "result"]


def test_document_symbols(ws):
    syms = Navigator(ws).document_symbols(os.path.join(EXAMPLE, "agent/alu_driver.svh"))
    names = [s["name"] for s in syms]
    assert names == ["alu_driver", "alu_driver::run_phase", "alu_driver::drive_item", "alu_error_driver"]


def test_with_real_uvm(uvm_ws):
    # req is declared in the library's uvm_driver.svh, at a line that depends on the UVM version.
    target = defs(uvm_ws, "agent/alu_driver.svh", "get_next_item(req)", 14)[0]
    path, line = target.rsplit(":", 1)
    assert path.endswith("uvm_driver.svh")
    with open(os.path.join(EXAMPLE, path)) as f:
        assert re.search(r"\bREQ\s+req\b", f.read().splitlines()[int(line) - 1])


def _labels(ws, probe):
    """Completion labels for `probe` in place of the drive_item(req) line of alu_driver's run_phase."""
    path, line, _, text = position("agent/alu_driver.svh", "drive_item(req);")
    lines = text.split("\n")
    lines[line] = probe
    return {c["label"] for c in Navigator(ws).completions(path, line, len(probe), "\n".join(lines))}


def test_completion_offers_what_the_class_can_use(bundled_ws):
    labels = _labels(bundled_ws, "    seq_item_port.")
    assert {"get_next_item", "try_next_item", "item_done", "put_response", "connect"} <= labels  # macro-made API
    assert not labels & {"m_if", "m_if_mask", "m_def_index", "m_provided_by", "m_imp_list"}  # protected, local
    labels = _labels(bundled_ws, "    this.")
    assert {"m_cfg", "drive_item", "get_full_name", "get_type_name", "m_children"} <= labels  # m_children: protected
    assert not labels & {"m_leaf_name", "m_inst_id", "m_main_stream", "m_set_full_name"}  # local to UVM classes
