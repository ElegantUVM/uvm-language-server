"""A missing super call is editable only when all of the base's work is visible."""

import pytest

from test_code_actions import Doc, own_fixes, start
from test_fixes import findings, fix_once, index, offered, user_errors
from uvmls.fixes import Client, Context, FileTexts, fix_all


SOURCE = """import uvm_pkg::*;
class base_test extends uvm_test;
  int depth;
  function new(string name = "base", uvm_component parent = null); super.new(name, parent); endfunction
  function void build_phase(uvm_phase phase);
    prepare();
  endfunction
  function void prepare();
    read_depth();
  endfunction
  function void read_depth();
    depth = 1;
  endfunction
endclass
class derived_test extends base_test;
  function void build_phase(uvm_phase phase);
    uvm_config_db #(int)::set(this, "", "depth", 42);
  endfunction
endclass
"""

READ = 'void\'(uvm_config_db #(int)::get(this, "", "depth", depth));'


@pytest.mark.parametrize('phase', ['build_phase', 'connect_phase'])
@pytest.mark.parametrize('body', [
    READ,
    'void\'(uvm_config_db #(int)::exists(this, "", "depth"));',
    'unknown_helper();',
    'unknown_helper;',
    'uvm_top.print_topology();',
    'begin uvm_object obj = new(); end',
])
@pytest.mark.parametrize('in_helper', [False, True])
def test_phase_fix_withheld_for_config_or_unknown_work(tmp_path, phase, body, in_helper):
    text = SOURCE.replace('depth = 1;' if in_helper else 'prepare();', body, 1)
    # Withholding must not depend on recognizing any setup in the derived phase.
    text = text.replace('uvm_config_db #(int)::set(this, "", "depth", 42);', 'depth = 42;')
    text = text.replace('build_phase', phase)
    w = index(tmp_path, {'t.sv': text})
    [d] = findings(w, 'phase-no-super')
    assert d.data['cls'] == 'derived_test'
    assert offered(w, d) == []
    assert fix_all(Context(w, FileTexts(), Client(), d.range.path), [d]) == ([], [])


@pytest.mark.parametrize('phase', ['build_phase', 'connect_phase'])
@pytest.mark.parametrize('shape', ['direct', 'helpers', 'this', 'inherited', 'extern'])
def test_phase_fix_for_visible_config_free_work(tmp_path, phase, shape):
    text = SOURCE
    if shape == 'direct':
        text = text.replace('prepare();', 'depth = 1;', 1)
    elif shape == 'this':
        text = text.replace('    read_depth();', '    this.read_depth();')
    elif shape == 'inherited':
        text = text.replace('  function void build_phase(uvm_phase phase);\n    prepare();\n  endfunction\n', '', 1)
        text = text.replace('class derived_test extends base_test;',
                            'class middle_test extends base_test;\n'
                            '  function void build_phase(uvm_phase phase); prepare(); endfunction\n'
                            'endclass\nclass derived_test extends middle_test;')
    elif shape == 'extern':
        text = text.replace('  function void read_depth();\n    depth = 1;\n  endfunction',
                            '  extern function void read_depth();')
        text += 'function void base_test::read_depth(); depth = 1; endfunction\n'
    text = text.replace('build_phase', phase)
    w = index(tmp_path, {'t.sv': text}, bundled=True)
    assert user_errors(w) == []
    [d] = findings(w, 'phase-no-super')
    [fix] = offered(w, d)
    assert fix.title == f'Call super.{phase}(phase) first'
    assert not fix.preferred and not fix.safe
    fix_once(w, 'phase-no-super')
    assert not findings(w, 'phase-no-super')
    assert user_errors(w) == []


@pytest.mark.parametrize('shape', ['extern', 'virtual', 'macro', 'super', 'default', 'bare'])
def test_phase_fix_requires_complete_helper_bodies(tmp_path, shape):
    text = SOURCE
    if shape == 'extern':
        text = text.replace('  function void read_depth();\n    depth = 1;\n  endfunction',
                            '  extern function void read_depth();')
    elif shape == 'virtual':
        text = text.replace('function void read_depth()', 'virtual function void read_depth()')
        text = text.replace('class derived_test extends base_test;',
                            'class derived_test extends base_test;\n'
                            f'  function void read_depth(); {READ} endfunction')
    elif shape == 'macro':
        text = text.replace('    depth = 1;', '    `READ_DEPTH')
    elif shape == 'super':
        text = text.replace('    depth = 1;', '    super.build_phase(null);')
    elif shape == 'default':
        text = text.replace('  function void read_depth();\n    depth = 1;\n  endfunction',
                            '  extern function void read_depth(int value = get_depth());\n'
                            '  extern function int get_depth();')
        text += 'function void base_test::read_depth(int value); depth = value; endfunction\n'
    elif shape == 'bare':
        text = text.replace('    read_depth();', '    depth = read_depth;')
        text = text.replace('  function void read_depth();\n    depth = 1;\n  endfunction\n', '')
        text = text.replace('  function void prepare();',
                            '  function int read_depth();\n'
                            '    return uvm_config_db #(int)::get(this, "", "depth", depth);\n'
                            '  endfunction\n  function void prepare();')
    w = index(tmp_path, {'t.sv': text}, bundled=True)
    if shape in ('virtual', 'super', 'bare'):
        assert user_errors(w) == []
    [d] = findings(w, 'phase-no-super')
    assert offered(w, d) == []


@pytest.mark.parametrize('phase', ['build_phase', 'connect_phase'])
def test_phase_actions_follow_helper_visibility(tmp_path, phase):
    text = SOURCE.replace('build_phase', phase)
    path = tmp_path / 't.sv'
    path.write_text(text, encoding="utf-8", newline="")
    (tmp_path / 'alu_tb.f').write_text('t.sv\n', encoding="utf-8", newline="")
    c = start(str(tmp_path))
    try:
        doc = Doc(c, str(path))
        for body in [READ, 'unknown_helper();', 'depth = 1;']:
            doc.change(text.replace('depth = 1;', body))
            ds = doc.diagnostics(lambda ds: any(d['code'] == 'phase-no-super' for d in ds))
            [d] = [d for d in ds if d['code'] == 'phase-no-super']
            for trigger in (1, 2):
                actions = own_fixes(doc.actions([d], only=['quickfix'], trigger=trigger))
                if body == 'depth = 1;':
                    assert len(actions) == 1 and actions[0]['title'] == f'Call super.{phase}(phase) first'
                else:
                    assert actions == []
            assert doc.actions([d], only=['source.fixAll.uvm']) == []
    finally:
        c.close()
