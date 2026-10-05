"""Similar config_db field names can still denote distinct knobs."""

import pytest

from test_fixes import findings, index, offered


@pytest.mark.parametrize("set_name,get_name", [
    ("enable_tx", "enable_rx"), ("m_cfg", "s_cfg"), ("has_cov0", "has_cov1"),
    ("io_div2_clk_rst_vif", "io_div4_clk_rst_vif"), ("aw_bp_vif", "ar_bp_vif"),
    ("aon_clk_rst_vif", "edn_clk_rst_vif"),
])
@pytest.mark.parametrize("required", [False, True])
def test_config_names_already_read_are_separate_knobs(tmp_path, set_name, get_name, required):
    get = 'uvm_config_db #(bit)::get(this, "", "FIELD", v)'
    read = 'if (!' + get + ') `uvm_fatal("CFG", "missing")' if required else "void'(" + get + ");"
    text = '''import uvm_pkg::*;
class env extends uvm_env;
  bit v;
  function void build_phase(uvm_phase phase);
    uvm_config_db #(bit)::set(this, "*", "SET", 1);
    READ_SET
    READ_GET
  endfunction
endclass
'''.replace('"SET"', '"' + set_name + '"') \
        .replace("READ_SET", read.replace("FIELD", set_name)).replace("READ_GET", read.replace("FIELD", get_name))
    w = index(tmp_path, {"t.sv": text})
    [d] = findings(w, "config-db-no-set")
    assert d.severity == ("warning" if required else "info")
    assert "did you mean" not in d.message
    assert offered(w, d) == []


@pytest.mark.parametrize("set_name,get_name,preferred", [
    ("Checks_Enable", "checks_enable", True),
    ("checks_enabled", "checks_enable", False),
    ("enable_tx", "enable_rx", False),
])
def test_config_name_guesses_need_a_deliberate_choice(tmp_path, set_name, get_name, preferred):
    text = '''import uvm_pkg::*;
class env extends uvm_env;
  bit v;
  function void build_phase(uvm_phase phase);
    uvm_config_db #(bit)::set(this, "*", "SET", 1);
    void'(uvm_config_db #(bit)::get(this, "", "GET", v));
  endfunction
endclass
'''.replace('"SET"', '"' + set_name + '"').replace('"GET"', '"' + get_name + '"')
    w = index(tmp_path, {"t.sv": text})
    [d] = findings(w, "config-db-no-set")
    assert d.severity == "info"
    [fix] = offered(w, d)
    assert fix.preferred == preferred and not fix.safe
