"""Phase forwarding wrappers do no work of their own."""

import pytest

from test_fixes import PHASES, findings, index


@pytest.mark.parametrize("phase", ["build_phase", "connect_phase"])
@pytest.mark.parametrize("target", ["super.", "uvm_test::"])
def test_phase_no_super_ignores_forwarding_to_uvm(tmp_path, phase, target):
    text = PHASES.replace('    m_env = uvm_env::type_id::create("m_env", this);\n', '') \
        .replace("    int n;\n    super.build_phase(phase);\n", "    int n;\n") \
        .replace("super.build_phase(phase);", target + "build_phase(phase);") \
        .replace("build_phase", phase)
    assert findings(index(tmp_path, {"t.sv": text}), "phase-no-super") == []


@pytest.mark.parametrize("target", ["super.", "base_test::", "uvm_test::"])
def test_phase_no_super_follows_the_forwarded_base(tmp_path, target):
    text = PHASES.replace("class plain_test extends base_test;", "class mid_test extends base_test;\n"
                         "  function void build_phase(uvm_phase phase); " + target + "build_phase(phase); endfunction\n"
                         "endclass\nclass plain_test extends mid_test;") \
        .replace("    int n;\n    super.build_phase(phase);\n", "    int n;\n")
    ds = findings(index(tmp_path, {"t.sv": text}), "phase-no-super")
    if target == "uvm_test::":
        assert ds == []
    else:
        [d] = ds
        assert d.data == {"cls": "plain_test", "method": "build_phase", "base": "base_test"}
        assert "which creates m_env" in d.message


@pytest.mark.parametrize("body", [
    'super.build_phase(phase); `uvm_info("T", "build", UVM_LOW)',
    'super.build_phase(make_phase());',
    'if (enabled) super.build_phase(phase);',
])
def test_phase_no_super_keeps_wrappers_with_work(tmp_path, body):
    text = PHASES.replace('super.build_phase(phase);\n    m_env = uvm_env::type_id::create("m_env", this);', body) \
        .replace("    int n;\n    super.build_phase(phase);\n", "    int n;\n")
    [d] = findings(index(tmp_path, {"t.sv": text}), "phase-no-super")
    assert d.data["base"] == "base_test"
