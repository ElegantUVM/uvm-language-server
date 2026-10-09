"""Boundary cases that distinguish valid fixes from the review's surviving mutations."""

import pytest

from test_fixes import (
    COMPONENT, CONFIG, DRV, NEWS, PARAMS, apply, findings, index, offered, round_trip, slang_findings,
)
from uvmls import fixes
from uvmls.fixes import Client, Context, FileTexts, Fix, TextChange
from uvmls.model import Diag, Range


def test_constructor_stays_inside_a_one_line_class_before_another_class(tmp_path):
    text = """import uvm_pkg::*;
class c extends uvm_object; `uvm_object_utils(c) endclass
class after_c;
  int x;
endclass
"""
    w = index(tmp_path, {"t.sv": text})
    [d] = findings(w, "factory-ctor")
    assert offered(w, d) == []
    ctx = Context(w, FileTexts(), Client(roots=[str(tmp_path)]), d.range.path)
    assert fixes.fix_all(ctx, [d]) == ([], [])


def test_utils_param_preserves_space_around_the_macro_argument(tmp_path):
    macro = "`uvm_component_param_utils(drv #(T, N))"
    round_trip(tmp_path,
               {"t.sv": PARAMS.replace(macro, "`uvm_component_param_utils( drv #(T, N) )")},
               {"t.sv": PARAMS.replace(macro, "`uvm_component_utils( drv )")}, "utils-param")


def test_include_order_is_nearest_first_and_excludes_configured_directories(tmp_path):
    files = {f"{d}/inc/sub/hdr.svh": "// header\n" for d in ("a", "b", "z")}
    files["z/tb/t.sv"] = '`include "sub/hdr.svh"\n'
    w = index(tmp_path, files)
    [d] = slang_findings(w, "CouldNotOpenIncludeFile")
    client = {"commands": frozenset({"uvm.addIncludeDir"})}
    actions = offered(w, d, **client)
    assert [f.command[1][0]["value"] for f in actions] == ["z/inc", "a/inc", "b/inc"]
    assert not any(f.preferred for f in actions)
    # A stale action can arrive after settings changed but before the diagnostic clears.
    w.sources.include_dirs.append(str(tmp_path / "z/inc"))
    assert [f.command[1][0]["value"] for f in offered(w, d, **client)] == ["a/inc", "b/inc"]


def test_config_failure_calls_and_bare_get_have_distinct_severities(tmp_path):
    calls = {
        "alpha": '$fatal(1, "missing")', "bravo": '$error("missing")',
        "charlie": 'uvm_report_fatal("CFG", "missing")', "delta": 'uvm_report_error("CFG", "missing")',
    }
    body = "\n".join(f'    if (!uvm_config_db #(int)::get(this, "", "{field}", value)) {call};'
                     for field, call in calls.items())
    text = '''import uvm_pkg::*;
class env extends uvm_env;
  int value;
  function void build_phase(uvm_phase phase);
BODY
    uvm_config_db #(int)::get(this, "", "unchecked", value);
  endfunction
endclass
'''.replace("BODY", body)
    w = index(tmp_path, {"t.sv": text})
    assert {d.data["field"]: d.severity for d in findings(w, "config-db-no-set")} == {
        **{field: "warning" for field in calls}, "unchecked": "info"}


@pytest.mark.parametrize("bundled,severity", [(False, "warning"), (True, "error")])
def test_missing_parent_severity_depends_on_the_registry(tmp_path, bundled, severity):
    text = 'import uvm_pkg::*;\n`include "uvm_macros.svh"\n' + COMPONENT.replace(
        'create("m_drv", this)', 'create("m_drv")')
    w = index(tmp_path, {"t.sv": text}, bundled=bundled)
    [d] = findings(w, "create-no-parent")
    assert d.severity == severity


@pytest.mark.parametrize("count", [3, 4])
def test_config_field_typo_limit(tmp_path, count):
    names = ["checks_enabl", "checks_enablee", "checks_enablex", "checks_enabley"][:count]
    sets = "\n".join(f'    uvm_config_db #(bit)::set(this, "*", "{name}", 0);' for name in names)
    text = CONFIG.replace('    uvm_config_db #(bit)::set(this, "*", "checks_enable", 0);', sets)
    w = index(tmp_path, {"t.sv": text})
    [d] = findings(w, "config-db-no-set")
    actions = offered(w, d, commands=frozenset({"uvm.showConfigDb"}))
    assert len([f for f in actions if f.changes]) == (3 if count == 3 else 0)
    assert actions[-1].command == ("uvm.showConfigDb", [{"field": "checks_enable"}])
    assert not any(f.preferred for f in actions)


def test_fix_all_rejects_a_safe_edit_that_also_touches_another_file(tmp_path, monkeypatch):
    w = index(tmp_path, {"t.sv": "int a;\nint b;\n", "other.sv": "int c;\n"})
    path, other = str(tmp_path / "t.sv"), str(tmp_path / "other.sv")
    ds = [Diag(Range(path, line, 0, line, 3), str(line), "warning", "create-no-parent", "uvm") for line in (0, 1)]
    local = TextChange(path, 11, 12, "d")
    edits = [[TextChange(path, 4, 5, "e"), TextChange(other, 4, 5, "f")], [local]]
    monkeypatch.setattr(fixes, "fixes_for", lambda ctx, d: [Fix("fix", edits[d.range.line], safe=True)])
    ctx = Context(w, FileTexts(), Client(roots=[str(tmp_path)]), path)
    assert fixes.fix_all(ctx, ds) == ([local], [ds[1]])


def test_fix_all_does_not_combine_different_insertions_at_the_same_offset(tmp_path, monkeypatch):
    w = index(tmp_path, {"t.sv": "int a;\nint b;\n"})
    path = str(tmp_path / "t.sv")
    ds = [Diag(Range(path, line, 0, line, 3), str(line), "warning", "create-no-parent", "uvm") for line in (0, 1)]
    edits = [TextChange(path, 0, 0, "// first\n"), TextChange(path, 0, 0, "// second\n")]
    monkeypatch.setattr(fixes, "fixes_for", lambda ctx, d: [Fix("fix", [edits[d.range.line]], safe=True)])
    ctx = Context(w, FileTexts(), Client(roots=[str(tmp_path)]), path)
    assert fixes.fix_all(ctx, ds[::-1]) == ([edits[0]], [ds[0]])


def test_utils_mismatch_ignores_a_nested_class_end_macro(tmp_path):
    inner = """  class inner extends uvm_object;
    `uvm_object_utils_begin(inner)
    `uvm_object_utils_end
  endclass
"""
    text = DRV.replace("`uvm_component_utils_begin(drv)", "`uvm_object_utils_begin(drv)").replace(
        "  `uvm_component_utils_end", inner + "  `uvm_object_utils_end", 1)
    w = index(tmp_path, {"t.sv": text})
    [d] = findings(w, "utils-mismatch")
    [fix] = offered(w, d)
    expected = text.replace("`uvm_object_utils_begin(drv)", "`uvm_component_utils_begin(drv)").replace(
        inner + "  `uvm_object_utils_end", inner + "  `uvm_component_utils_end", 1)
    assert apply(text, fix.changes) == expected


def test_utils_mismatch_requires_the_replacement_end_macro(tmp_path, monkeypatch):
    w = index(tmp_path, {"t.sv": DRV.replace("`uvm_component_utils_begin(drv)",
                                            "`uvm_object_utils_begin(drv)")})
    [d] = findings(w, "utils-mismatch")
    original = fixes.macro_defined
    monkeypatch.setattr(fixes, "macro_defined", lambda ws, name: name != "uvm_component_utils_end" and original(ws, name))
    assert offered(w, d) == []


def test_utils_mismatch_sees_the_base_declared_before_a_nested_one(tmp_path):
    """`child extends base` before outer's own nested `base` extends the global component."""
    text = """`include "uvm_macros.svh"
import uvm_pkg::*;
class base extends uvm_component;
 function new(string name, uvm_component parent); super.new(name,parent); endfunction
endclass
class outer;
 class child extends base;
  `uvm_component_utils(child)
  function new(string name, uvm_component parent); super.new(name,parent); endfunction
 endclass
 class base extends uvm_object;
  function new(string name=""); super.new(name); endfunction
 endclass
endclass
module tb; outer::child c; endmodule
"""
    w = index(tmp_path, {"t.sv": text}, bundled=True)
    assert w.role(w.class_by_qual["outer::child"]) == "component"
    assert findings(w, "utils-mismatch") == []


def test_parent_fix_rechecks_argument_count_in_same_length_text(tmp_path):
    text = COMPONENT.replace('create("m_drv", this)', 'create("m_drv")')
    w = index(tmp_path, {"t.sv": text})
    [d] = findings(w, "create-no-parent")
    assert offered(w, d)
    (tmp_path / "t.sv").write_text(text.replace('create("m_drv")', 'create(       )'))
    assert offered(w, d) == []


def test_factory_rewrite_rechecks_constructor_scope_in_same_length_text(tmp_path):
    text = NEWS.replace('m = mon::type_id::create("m", this)', 'm =       new("m", this)')
    w = index(tmp_path, {"t.sv": text})
    [d] = findings(w, "create-new")
    assert offered(w, d)
    (tmp_path / "t.sv").write_text(text.replace('      new("m", this)', 'item::new("m", this)'))
    assert offered(w, d) == []


@pytest.mark.parametrize("data", [False, True])
@pytest.mark.parametrize("republished", [False, True])
@pytest.mark.parametrize("lines", [1, 3])
def test_two_identical_findings_keep_their_identity_after_an_insertion(tmp_path, data, republished, lines):
    from test_code_actions import Doc, applied, edits_for, start

    path = tmp_path / "t.sv"
    path.write_text(COMPONENT)
    (tmp_path / "alu_tb.f").write_text("t.sv\n")
    c = start(str(tmp_path), settings={"filelists": [str(tmp_path / "alu_tb.f")],
                                       "useBundledUvm": False, "elaboration": "off"})
    try:
        doc = Doc(c, str(path))
        call = '    m_drv = drv::type_id::create("m_drv", this);'
        first = call.replace('"m_drv", this', '"first"')
        second = call.replace('"m_drv", this', '"other"')
        doc.change(COMPONENT.replace(call, first + "\n" + second))
        ds = doc.diagnostics(lambda ds: len(ds) == 2)
        old = dict(ds[1])
        assert ds[0]["code"] == old["code"] and ds[0]["message"] == old["message"]
        if not data:
            old.pop("data", None)
        # One line reuses the old range; three lines put the wrong finding nearer it.
        doc.change("// inserted\n" * lines + doc.text)
        if republished:
            doc.diagnostics(lambda ds: len(ds) == 2 and ds[1]["range"] != old["range"])
        actions = doc.actions([old])
        if not data and lines == 1:
            assert actions == []  # an untagged old diagnostic is indistinguishable from the new one
            return
        fix = next(a for a in actions if a["title"] == "Pass 'this' as the parent")
        assert applied(doc.text, edits_for(fix, doc.uri, doc.version)) == doc.text.replace(
            'create("other")', 'create("other", this)')
    finally:
        c.close()


@pytest.mark.parametrize("data", [False, True])
@pytest.mark.parametrize("republished", [False, True])
@pytest.mark.parametrize("ranged", [False, True])
@pytest.mark.parametrize("removed", [0, 1])
def test_deleted_identical_call_does_not_transfer_its_diagnostic(tmp_path, data, republished, ranged, removed):
    from test_code_actions import Doc, applied, edits_for, start

    path = tmp_path / "t.sv"
    path.write_text(COMPONENT)
    (tmp_path / "alu_tb.f").write_text("t.sv\n")
    c = start(str(tmp_path), settings={"filelists": [str(tmp_path / "alu_tb.f")],
                                       "useBundledUvm": False, "elaboration": "off"})
    try:
        doc = Doc(c, str(path))
        call = '    m_drv = drv::type_id::create("m_drv", this);\n'
        broken = call.replace(', this', '')
        doc.change(COMPONENT.replace(call, broken * 2))
        ds = doc.diagnostics(lambda ds: len(ds) == 2)
        old = dict(ds[removed])
        if not data:
            old.pop("data")
        line = old["range"]["start"]["line"]
        rng = {"start": {"line": line, "character": 0}, "end": {"line": line + 1, "character": 0}}
        changed = applied(doc.text, [{"range": rng, "newText": ""}])
        if ranged:
            doc.version += 1
            doc.text = changed
            c.notify("textDocument/didChange", {"textDocument": {"uri": doc.uri, "version": doc.version},
                                                 "contentChanges": [{"range": rng, "text": ""}]})
        else:
            doc.change(changed)
        if republished:
            current = doc.diagnostics(lambda ds: len(ds) == 1)
        assert doc.actions([old]) == []
        if not republished:
            current = doc.diagnostics(lambda ds: len(ds) == 1)
        assert doc.actions([old]) == []
        assert current[0]["data"]["_uvmls_id"] not in {d["data"]["_uvmls_id"] for d in ds}
        fix = next(a for a in doc.actions(current) if a["title"] == "Pass 'this' as the parent")
        assert applied(doc.text, edits_for(fix, doc.uri, doc.version)) == COMPONENT
    finally:
        c.close()


@pytest.mark.parametrize("disabled", [False, True])
def test_outside_workspace_fix_respects_lsp_disabled_support(tmp_path, disabled):
    import copy
    from test_code_actions import CAPABILITIES, Doc, start

    root = tmp_path / "ws"
    root.mkdir()
    (tmp_path / "item.svh").write_text("class item extends uvm_object; endclass\n")
    text = 'import uvm_pkg::*;\n`include "../item.svh"\n' + COMPONENT
    path = root / "t.sv"
    path.write_text(text)
    (root / "alu_tb.f").write_text("t.sv\n")
    caps = copy.deepcopy(CAPABILITIES)
    caps["textDocument"]["codeAction"]["disabledSupport"] = disabled
    c = start(str(root), capabilities=caps, settings={"filelists": [str(root / "alu_tb.f")],
                                                      "useBundledUvm": False, "elaboration": "off"})
    try:
        doc = Doc(c, str(path))
        doc.change(text.replace("    m_drv =", '    item i = item::type_id::create("i");\n    m_drv ='))
        [d] = doc.diagnostics(lambda ds: len(ds) == 1 and ds[0]["code"] == "create-unregistered")
        actions = doc.actions([d])
        registrations = [a for a in actions if a["title"].startswith("Add `uvm_object_utils")]
        if disabled:
            [action] = registrations
            assert action["disabled"]["reason"] == f"'item' is declared outside the workspace ({tmp_path / 'item.svh'})"
            assert "edit" not in action and "isPreferred" not in action
        else:
            assert registrations == []
        assert actions[-1]["title"] == "Ignore 'create-unregistered' on this line"
    finally:
        c.close()


def test_slang_diagnostic_without_data_still_gets_its_fix(tmp_path):
    from test_code_actions import Doc, applied, edits_for, start

    text = "module m;\n  logic a;\n  always @(a) a = 0;\nendmodule\n"
    path = tmp_path / "t.sv"
    path.write_text(text)
    (tmp_path / "alu_tb.f").write_text("t.sv\n")
    c = start(str(tmp_path), settings={"filelists": [str(tmp_path / "alu_tb.f")],
                                       "useBundledUvm": False, "elaboration": "off"})
    try:
        doc = Doc(c, str(path))
        doc.change(text.replace("always", "alwasy"))
        [d] = doc.diagnostics(lambda ds: ds and ds[0]["code"] == "TypoKeyword")
        d.pop("data")
        [action] = doc.actions([d])
        assert applied(doc.text, edits_for(action, doc.uri, doc.version)) == text
    finally:
        c.close()


@pytest.mark.parametrize("broken", [False, True])
def test_actions_order_by_severity_and_survive_a_raising_fixer(tmp_path, monkeypatch, caplog, broken):
    from lsprotocol import types as T
    from uvmls.documents import Workspace as LspWorkspace
    from uvmls.lints import lint
    from uvmls.server import UvmLanguageServer, code_actions, lsp_diagnostic

    text = COMPONENT.replace('create("m_drv", this)', 'create("m_drv")') + '''
class item extends uvm_object;
  `uvm_object_utils(item)
endclass
'''
    w = index(tmp_path, {"t.sv": text})
    ls = UvmLanguageServer()
    ls.ws = w
    ls.workspace_roots = [str(tmp_path)]
    ls.protocol._workspace = LspWorkspace(tmp_path.as_uri())
    ls.protocol.client_capabilities = T.ClientCapabilities()
    ls.published = lint(w)
    path = tmp_path / "t.sv"
    ds = [lsp_diagnostic(d) for d in ls.published[str(path)]]
    assert {d.code for d in ds} == {"factory-ctor", "create-no-parent"}
    if broken:
        def fail(ctx, d):
            raise ValueError("broken constructor fixer")
        monkeypatch.setitem(fixes.FIXERS, ("uvm", "factory-ctor"), fail)
    actions = code_actions(ls, path.as_uri(), sorted(ds, key=lambda d: d.range.start.line), text)
    assert actions[0].diagnostics[0].code == "factory-ctor"  # error, although later in the file
    assert any(a.title == "Pass 'this' as the parent" for a in actions)
    assert any(a.title == "Ignore 'factory-ctor' on this line" for a in actions)
    if broken:
        assert "broken constructor fixer" in caplog.text
        assert not any(a.title.startswith("Add a constructor") for a in actions)
