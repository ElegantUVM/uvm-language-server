"""The bundled Accellera UVM 1.2 (the default) next to the built-in model."""

import json
import os

from uvmls.cli import main as cli
from uvmls.lints import lint
from uvmls.model import Diag, FileIndex, Range
from uvmls.navigation import Navigator
from uvmls.project import BUNDLED_UVM, ProjectConfig
from uvmls.views import class_graph
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
