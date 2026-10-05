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


@pytest.mark.parametrize("get_name,suggested", [
    ("alu_agent_confg", True),   # a character missing
    ("alu_agent_cofnig", True),  # two characters swapped
    ("alu_agent_configs", True),  # a character extra
    ("alu_agent_confog", False),  # a character changed: how separate knobs differ (rx/tx, cov0/cov1)
])
def test_a_slip_of_a_name_others_read_is_still_a_typo(tmp_path, get_name, suggested):
    """The common typo: one class misspells a field that the other classes read correctly."""
    get = 'if (!uvm_config_db #(bit)::get(this, "", "FIELD", v)) `uvm_fatal("CFG", "missing")'
    text = '''import uvm_pkg::*;
class env extends uvm_env;
  bit v;
  function void build_phase(uvm_phase phase);
    uvm_config_db #(bit)::set(this, "*", "alu_agent_config", 1);
    READ_OK
    READ_OK
    READ_GET
  endfunction
endclass
'''.replace("READ_OK", get.replace("FIELD", "alu_agent_config")).replace("READ_GET", get.replace("FIELD", get_name))
    w = index(tmp_path, {"t.sv": text})
    [d] = findings(w, "config-db-no-set")
    assert d.severity == "warning"
    assert ("did you mean 'alu_agent_config'" in d.message) == suggested
    fixes = offered(w, d)
    assert [f.title.startswith("Change") for f in fixes] == ([True] if suggested else [])
    assert not any(f.preferred for f in fixes)
