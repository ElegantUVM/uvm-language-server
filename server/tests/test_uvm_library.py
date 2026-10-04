"""The bundled Accellera UVM 1.2 (the default) next to the built-in model."""

import json
import os
import shutil

from uvmls.cli import main as cli
from uvmls.lints import lint
from uvmls.model import Diag, FileIndex, Range
from uvmls.navigation import Navigator
from uvmls.project import BUNDLED_UVM, ProjectConfig
from uvmls.views import class_graph, topology
from uvmls.workspace import Workspace

from conftest import EXAMPLE, FILELIST, position

UVM_DRIVER = os.path.join(BUNDLED_UVM, "comps", "uvm_driver.svh")


def workspace(**kw) -> Workspace:
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[EXAMPLE], filelists=[FILELIST], **kw))
    return w


def test_stats_name_the_uvm_library(ws, bundled_ws):
    s = bundled_ws.stats()
    assert s["uvm"] == {"source": "bundled", "version": "1.2", "dir": BUNDLED_UVM}
    assert s["realUvm"] and s["classes"] == 19 and s["libraryClasses"] > 300
    # UVM's own files are counted apart from the project's.
    assert s["files"] == ws.stats()["files"] == 20 and s["libraryFiles"] > 100
    b = ws.stats()
    assert b["uvm"] == {"source": "builtin", "version": None, "dir": None}
    assert not b["realUvm"] and b["libraryClasses"] == 0


def test_cli_picks_the_library(capsys, monkeypatch):
    monkeypatch.delenv("UVM_HOME", raising=False)
    for flags, source in (([], "bundled"), (["--no-bundled-uvm"], "builtin")):
        cli(["stats", EXAMPLE, "-f", FILELIST, *flags])
        assert json.loads(capsys.readouterr().out)["uvm"]["source"] == source
    # A --uvm-home without a UVM in it falls back to $UVM_HOME.
    monkeypatch.setenv("UVM_HOME", os.path.dirname(BUNDLED_UVM))
    cli(["stats", EXAMPLE, "-f", FILELIST, "--uvm-home", "/no/such/uvm"])
    out = capsys.readouterr()
    assert json.loads(out.out)["uvm"] == {"source": "configured", "version": "1.2", "dir": BUNDLED_UVM}
    assert "warning: uvmHome /no/such/uvm does not contain uvm_pkg.sv" in out.err


def test_class_graph_inheritance_reaches_uvm_classes(bundled_ws):
    g = class_graph(bundled_ws)
    edges = {(e["source"], e["target"], e["kind"]) for e in g["edges"]}
    chain = ["alu_pkg::alu_driver", "uvm_pkg::uvm_driver", "uvm_pkg::uvm_component",
             "uvm_pkg::uvm_report_object", "uvm_pkg::uvm_object", "uvm_pkg::uvm_void"]
    assert {(a, b, "extends") for a, b in zip(chain, chain[1:])} <= edges
    assert ("alu_pkg::alu_sequencer", "uvm_pkg::uvm_sequencer", "typedef") in edges
    drv = next(n for n in g["nodes"] if n["id"] == "uvm_pkg::uvm_driver")
    assert drv["library"] and not drv["external"] and drv["package"] == "uvm_pkg"
    assert drv["libDistance"] == 1 and drv["file"] == UVM_DRIVER


def test_user_classes_look_the_same_on_either_model(ws, bundled_ws):
    # The real macros generate more members (get_type, create, ...); none of them are sent.
    def user_nodes(w):
        return {n["id"]: n for n in class_graph(w)["nodes"] if not n["library"]}
    assert user_nodes(ws) == user_nodes(bundled_ws)


def test_definition_goes_into_the_bundled_sources(bundled_ws):
    nav = Navigator(bundled_ws)
    for needle, offset, line in (("uvm_driver #(", 1, 44), ("get_next_item(req)", 14, 65)):
        path, ln, col, text = position("agent/alu_driver.svh", needle, offset)
        assert [(r.path, r.line + 1) for r in nav.definition(path, ln, col, text)] == [(UVM_DRIVER, line)]


def test_uvm_macros_are_predefined_when_a_file_does_not_include_them(tmp_path):
    # Simulators usually have them in scope already, as the macro shim always did.
    (tmp_path / "t.sv").write_text("import uvm_pkg::*;\nclass c extends uvm_object;\n"
                                   "  `uvm_object_utils(c)\nendclass\n")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    assert w.find_class("c").registration == "object"
    assert w.user_diagnostics() == []


def test_parse_warnings_follow_slangs_default_set(tmp_path):
    # UVM 1.2's `uvm_pack_sarrayN has `foreach (VAR `` [index])`: an IgnoredMacroPaste that
    # slang's driver, and so elaboration, ignores, at every call of the macros built on it.
    (tmp_path / "t.sv").write_text(
        '`include "uvm_macros.svh"\nimport uvm_pkg::*;\nclass c extends uvm_object;\n'
        "  string s;\n  bit [7:0] a[4];\n"
        "  function void do_pack(uvm_packer packer);\n"
        "    `uvm_pack_string(s)\n    `uvm_pack_sarray(a)\n  endfunction\n"
        "  function void do_unpack(uvm_packer packer);\n    `uvm_unpack_sarray(a)\n  endfunction\n"
        "endclass\n")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    assert w.user_diagnostics() == []


def test_diagnostics_inside_library_files_are_not_reported():
    def diag(path):
        return Diag(Range(path, 0, 0, 0, 1), "header guard", "warning", "HeaderGuardMismatch")
    lib, macros, user = "/uvm/uvm_pkg.sv", "/uvm/uvm_macros.svh", "/tb/tb.sv"
    w = Workspace()
    w.indices = {lib: FileIndex(lib, {lib, macros}, diagnostics=[diag(lib)], library=True),
                 user: FileIndex(user, {user, macros}, diagnostics=[diag(macros), diag(user), diag(user)])}
    w._rebuild()
    assert [d.range.path for d in w.user_diagnostics()] == [user]


def test_open_uvm_source_is_not_reparsed_or_taken_for_user_code():
    with open(UVM_DRIVER) as f:
        text = f.read()
    w = workspace()
    version = w.version
    w.update_document(UVM_DRIVER, text)  # opened in the editor, unchanged
    w.update_document(UVM_DRIVER, None)  # and closed
    assert w.version == version
    w.update_document(UVM_DRIVER, text + "\n")  # an edit does re-parse the library
    assert w.version > version
    w = workspace(bundled_uvm=False)
    w.update_document(UVM_DRIVER, text)
    assert w.stats()["classes"] == 19 and w.find_class("uvm_driver") is None


def test_open_uvm_source_under_another_name_is_not_taken_for_user_code(tmp_path):
    # The bundled file reached through a symlink: the editor's name and slang's resolved name differ.
    (tmp_path / "uvm").symlink_to(os.path.dirname(BUNDLED_UVM))
    alias = str(tmp_path / "uvm" / "src" / "comps" / "uvm_driver.svh")
    with open(UVM_DRIVER) as f:
        text = f.read()
    for name in (alias, os.path.realpath(UVM_DRIVER)):
        w = workspace()
        w.update_document(name, text)  # opened while the bundled copy is in use
        assert w.stats()["classes"] == 19 and not w.ephemeral
        w.configure(ProjectConfig(workspace_roots=[EXAMPLE], filelists=[FILELIST], bundled_uvm=False))
        assert w.stats()["classes"] == 19 and not w.ephemeral  # still open after switching


def test_type_parameter_bases_are_followed(tmp_path, monkeypatch):
    # uvm_reg_sequence #(type BASE=...) extends BASE; uvm_port_base #(type IF=...) extends IF
    monkeypatch.delenv("UVM_HOME", raising=False)
    src = ("package p;\n  import uvm_pkg::*;\n  `include \"uvm_macros.svh\"\n"
           "  class item extends uvm_sequence_item;\n  endclass\n"
           "  class rs extends uvm_reg_sequence;\n    task body();\n      req = null;\n    endtask\n  endclass\n"
           "  class rs2 extends uvm_reg_sequence #(uvm_sequence #(item));\n  endclass\nendpackage\n")
    (tmp_path / "t.sv").write_text(src)
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))

    def names(c):
        return [e.spec.name for e in w.chain(w.find_class(c))]
    assert names("rs") == ["rs", "uvm_reg_sequence", "uvm_sequence", "uvm_sequence_base",
                           "uvm_sequence_item", "uvm_transaction", "uvm_object", "uvm_void"]
    assert names("uvm_reg_frontdoor")[2] == "uvm_sequence"
    assert names("uvm_seq_item_pull_port")[-1] == "uvm_sqr_if_base"
    assert not {"BASE", "IF"} & {n["id"] for n in class_graph(w)["nodes"]}
    owner, member, bindings = w.find_member(w.find_class("rs2"), "req")
    assert owner.name == "uvm_sequence" and w.member_type_spec(owner, member, bindings).cls.name == "item"
    assert w.find_member(w.find_class("rs"), "m_sequencer")[0].name == "uvm_sequence_item"
    line = src.split("\n").index("      req = null;")
    assert "property of `uvm_sequence`" in Navigator(w).hover(str(tmp_path / "t.sv"), line, 7, src)
    # The built-in model follows the default too, and nothing calls the base unknown.
    (tmp_path / "t.sv").write_text("class m #(type B = uvm_sequence_item) extends B;\nendclass\n")
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)], bundled_uvm=False))
    assert w.role(w.find_class("m")) == "item" and not lint(w)


def test_user_roots_do_not_repeat_the_library_macros(bundled_ws):
    # The library's root records the UVM macros once; user roots that include them do not again.
    assert not [m for idx in bundled_ws.indices.values() if not idx.library
                for m in idx.macros if m.range.path in bundled_ws.library_files]
    assert len(bundled_ws.macros_by_name["uvm_info"]) == 1


def test_a_configured_uvm_is_not_rescanned_either(tmp_path):
    src = tmp_path / "uvm" / "src"
    (src / "macros").mkdir(parents=True)
    (src / "uvm_pkg.sv").write_text('`include "uvm_macros.svh"\npackage uvm_pkg;\nendpackage\n')
    (src / "uvm_macros.svh").write_text('`include "macros/m.svh"\n')
    (src / "macros" / "m.svh").write_text("`define uvm_info(ID, MSG, VERB) $display(MSG);\n")
    tb = tmp_path / "tb"
    tb.mkdir()
    for i in range(3):
        (tb / f"p{i}.sv").write_text(f'`include "uvm_macros.svh"\npackage p{i};\nendpackage\n')
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tb)], uvm_home=str(tmp_path / "uvm")))
    assert w.sources.uvm_source == "configured"
    macros = [m for idx in w.indices.values() for m in idx.macros]
    assert [(m.name, m.range.line) for m in macros] == [("uvm_info", 0)]


def test_macro_definitions_point_at_their_define(bundled_ws):
    lines = {}
    for name, defs in bundled_ws.macros_by_name.items():
        for m in defs:
            if m.range.path not in lines:
                with open(m.range.path, encoding="utf-8", errors="replace") as f:
                    lines[m.range.path] = f.read().split("\n")
            text = lines[m.range.path][m.range.line]
            assert text[m.range.col:].startswith(name) and "`define" in text[:m.range.col], (m, text)


def test_a_file_that_includes_uvm_pkg_does_not_parse_the_library_again(tmp_path):
    # The library's own root indexes it; as part of a user root it would be user code.
    tb = tmp_path / "tb.sv"
    tb.write_text('`include "uvm_macros.svh"\n`include "uvm_pkg.sv"\nmodule tb;\n  import uvm_pkg::*;\n'
                  "  class t extends uvm_test;\n    task run_phase(uvm_phase phase);\n    endtask\n"
                  "  endclass\nendmodule\n")
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)]))
    idx = w.indices[str(tb)]
    assert len(idx.files) < 20 and os.path.join(BUNDLED_UVM, "uvm_pkg.sv") in idx.files  # still a link
    assert not [r for rs in idx.identifiers.values() for r in rs if r.path in w.library_files]
    text = tb.read_text()
    line = text.split("\n").index("    task run_phase(uvm_phase phase);")
    refs = Navigator(w).references(str(tb), line, 20, text)
    assert refs and {r.path for r in refs} == {str(tb)}
    assert not any(cs.creates or cs.config_db or cs.connects for q, cs in w.calls.items()
                   if w.class_by_qual[q].library)
    assert w.find_class("t").base.name == "uvm_test" and w.user_diagnostics() == []


def test_topology_is_the_same_when_a_package_includes_uvm_pkg(tmp_path):
    shutil.copytree(EXAMPLE, tmp_path / "alu")
    pkg = tmp_path / "alu" / "agent" / "alu_pkg.sv"
    pkg.write_text('`include "uvm_macros.svh"\n`include "uvm_pkg.sv"\n' + pkg.read_text())
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path / "alu")], filelists=[str(tmp_path / "alu" / "alu_tb.f")]))

    def shape(t):
        def node(n):
            return (n["name"], n["type"], [node(c) for c in n["children"]])
        return node(t["root"]), sorted((c["source"], c["sourcePort"], c["target"], c["targetPort"])
                                       for c in t["connections"])
    assert shape(topology(w)) == shape(topology(workspace()))


def test_uvm_macros_go_to_the_definition_in_effect(bundled_ws):
    # uvm_object_defines.svh first defines empty stubs under `ifdef UVM_EMPTY_MACROS.
    nav = Navigator(bundled_ws)
    objdefs = os.path.join(BUNDLED_UVM, "macros", "uvm_object_defines.svh")
    path, ln, col, text = position("agent/alu_driver.svh", "`uvm_component_utils", 1)
    assert [(r.path, r.line + 1) for r in nav.definition(path, ln, col, text)] == [(objdefs, 330)]
    assert nav.hover(path, ln, col, text).startswith("```systemverilog\n`define uvm_component_utils(T) \\")
    # The stub of uvm_field_array_enum takes two arguments; the real macro takes three.
    items = {c["label"]: c for c in nav.completions(path, ln, col + len("uvm_"), text)}
    assert items["uvm_field_array_enum"]["snippet"] == "uvm_field_array_enum(${1:T}, ${2:ARG}, ${3:FLAG})"
    w = workspace(defines={"UVM_EMPTY_MACROS": ""})
    assert [(r.path, r.line + 1) for r in Navigator(w).definition(path, ln, col, text)] == [(objdefs, 36)]


def test_macro_definitions_know_their_ifdef_branches(tmp_path):
    (tmp_path / "defs.svh").write_text(
        "`define USE_FAST\n"
        "`ifdef USE_FAST\n`define DELAY 1\n`else\n`define DELAY 10\n`endif\n"
        "`ifdef NOWHERE\n`define MODE 0\n`elsif ALSO_NOWHERE\n`define MODE 1\n`else\n"
        "`define MODE 2 \\\n  `ifdef X \\\n  1\n`endif\n")
    (tmp_path / "top.sv").write_text('`include "defs.svh"\nmodule top; endmodule\n')
    w = Workspace()
    w.configure(ProjectConfig(workspace_roots=[str(tmp_path)], bundled_uvm=False))
    mode = [m for idx in w.indices.values() for m in idx.macros if m.name == "MODE"]
    assert [m.requires for m in mode] == [
        (("ifdef", "NOWHERE"),), (("ifndef", "NOWHERE"), ("ifdef", "ALSO_NOWHERE")),
        (("ifndef", "NOWHERE"), ("ifndef", "ALSO_NOWHERE"))]  # the `ifdef in its body is not a branch
    assert [m.range.line for m in w.macros_by_name["MODE"]] == [11]
    # USE_FAST is defined, so either DELAY may be in effect: both stay.
    assert [m.range.line for m in w.macros_by_name["DELAY"]] == [2, 4]
