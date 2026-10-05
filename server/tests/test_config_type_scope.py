"""A config_db type edit must use a spelling visible at the get."""

import pytest

from test_fixes import CONFIG, example, findings, fix_once, index, offered, user_errors


@pytest.mark.parametrize("setter", ["module", "class"])
def test_config_type_uses_the_get_variable_type(tmp_path, setter):
    plants = {"agent/alu_monitor.svh": ('#(bit)::get', '#(int)::get')}
    if setter == "module":
        plants["tb/tb_top.sv"] = ("  initial begin", "  typedef bit flag_t;\n  initial begin\n"
                                 '    uvm_config_db #(flag_t)::set(null, "*", "checks_enable", 1);')
    else:
        plants["env/alu_env.svh"] = (
            "  function void build_phase(uvm_phase phase);",
            "  typedef bit flag_t;\n  function void build_phase(uvm_phase phase);\n"
            '    uvm_config_db #(flag_t)::set(this, "m_agent.m_monitor", "checks_enable", 1);')
    w = example(tmp_path, plants)
    assert user_errors(w) == []
    fix = fix_once(w, "config-db-type-mismatch")
    assert fix.preferred and "#(bit)" in fix.title
    assert not findings(w, "config-db-type-mismatch") and user_errors(w) == []


def aliased_vif(tmp_path):
    return example(tmp_path, {
        "tb/tb_top.sv": ('  initial begin\n    uvm_config_db #(virtual alu_if)::set',
                         '  typedef virtual alu_if alu_vif_t;\n  initial begin\n    uvm_config_db #(alu_vif_t)::set'),
        "tests/alu_base_test.svh": ('#(virtual alu_if)::get', '#(virtual alu_if.mon)::get'),
    })


def test_config_type_uses_a_direct_interface_spelling(tmp_path):
    w = aliased_vif(tmp_path)
    fix = fix_once(w, "config-db-type-mismatch")
    assert fix.preferred and "#(virtual alu_if)" in fix.title
    assert not findings(w, "config-db-type-mismatch") and user_errors(w) == []


def test_config_type_withholds_a_member_private_typedef(tmp_path):
    w = aliased_vif(tmp_path)
    path = tmp_path / "alu_tb" / "agent" / "alu_agent_config.svh"
    path.write_text(path.read_text().replace("virtual alu_if        vif;", "typedef virtual alu_if vif_t;\n  vif_t vif;"))
    w.reindex_all()
    [d] = findings(w, "config-db-type-mismatch")
    assert offered(w, d) == []
    [show] = offered(w, d, commands=frozenset({"uvm.showConfigDb"}))
    assert show.command and not show.changes and not show.preferred


@pytest.mark.parametrize("scope", ["class", "package"])
def test_config_type_replaces_a_setter_private_class_alias(tmp_path, scope):
    w = example(tmp_path, {"agent/alu_driver.svh": ('#(alu_agent_config)::get', '#(uvm_object)::get')})
    root = tmp_path / "alu_tb"
    path = root / "env" / "alu_env.svh"
    text = path.read_text().replace('#(alu_agent_config)::set', '#(agent_cfg_t)::set')
    if scope == "class":
        text = text.replace('  `uvm_component_utils(alu_env)',
                            '  `uvm_component_utils(alu_env)\n  typedef alu_agent_config agent_cfg_t;')
    else:
        pkg = root / "env" / "alu_env_pkg.sv"
        pkg.write_text(pkg.read_text().replace('  `include "alu_env.svh"',
                                               '  typedef alu_agent_config agent_cfg_t;\n  `include "alu_env.svh"'))
    path.write_text(text)
    w.reindex_all()
    fix = fix_once(w, "config-db-type-mismatch")
    assert fix.preferred and "#(alu_agent_config)" in fix.title
    assert not findings(w, "config-db-type-mismatch") and user_errors(w) == []


def test_config_type_does_not_copy_a_setter_local_dimension(tmp_path):
    text = CONFIG.replace('class test extends uvm_test;', 'class test extends uvm_test;\n  localparam int WIDTH = 8;') \
        .replace('uvm_config_db #(int)::set', 'uvm_config_db #(bit [WIDTH-1:0])::set')
    w = index(tmp_path, {"t.sv": '`include "uvm_macros.svh"\n' + text}, bundled=True)
    assert user_errors(w) == []
    ds = findings(w, "config-db-type-mismatch")
    assert len(ds) == 2 and all(offered(w, d) == [] for d in ds)
