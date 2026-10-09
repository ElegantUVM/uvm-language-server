"""Structural folds come from matched syntax tokens, including unsaved buffers."""

import os
import shutil

import pytest

from conftest import EXAMPLE
from test_code_actions import Doc, start
from test_lsp import Client

CAPS = {"textDocument": {"foldingRange": {}}}


@pytest.fixture(scope="module")
def tb(tmp_path_factory):
    root = tmp_path_factory.mktemp("folding") / "alu_tb"
    shutil.copytree(EXAMPLE, root)
    return str(root)


@pytest.fixture(scope="module")
def client(tb):
    c = start(tb, capabilities=CAPS)
    yield c
    c.close()


@pytest.fixture
def doc(client, tb):
    d = Doc(client, os.path.join(tb, "agent", "alu_agent.svh"))
    yield d
    d.close()


def folds(doc):
    return doc.client.request("textDocument/foldingRange", {"textDocument": {"uri": doc.uri}})


def fold(text, opening, closing, after=""):
    start = text.index(opening, text.index(after))
    end = text.index(closing, start + len(opening))
    return {"startLine": text.count("\n", 0, start), "endLine": text.count("\n", 0, end) - 1}


def ordered(ranges):
    return sorted(ranges, key=lambda r: (r["startLine"], -r["endLine"]))


def agent_folds(text):
    return ordered([fold(text, "class alu_agent", "endclass"),
                    fold(text, "function new", "endfunction"),
                    fold(text, "function void build_phase", "endfunction"),
                    fold(text, "function void connect_phase", "endfunction"),
                    fold(text, "UVM_ACTIVE) begin", "    end\n")])


@pytest.mark.parametrize("caps,present", [({}, False), (CAPS, True),
    ({"textDocument": {"foldingRange": {"dynamicRegistration": False}}}, True),
    ({"textDocument": {"formatting": {}, "rangeFormatting": {}, "signatureHelp": {},
                       "documentHighlight": {}, "foldingRange": {}}}, True)])
def test_registration(tb, caps, present):
    c = Client()
    try:
        result = c.request("initialize", {"processId": None, "rootUri": f"file://{tb}", "capabilities": caps})
        assert result["capabilities"].get("foldingRangeProvider", False) == present
    finally:
        c.close()


def test_agent_current_buffer(doc):
    original = doc.text
    expected = agent_folds(original)
    assert folds(doc) == expected
    doc.change("\n" + original)
    assert folds(doc) == agent_folds(doc.text)
    doc.change("// no structures\n")
    assert folds(doc) == []
    doc.close()
    assert folds(doc) == expected


def test_driver_extern_bodies(client, tb):
    d = Doc(client, os.path.join(tb, "agent", "alu_driver.svh"))
    try:
        text = d.text
        expected = [fold(text, "class alu_driver", "endclass"),
                    fold(text, "function new", "endfunction"),
                    fold(text, "function void build_phase", "endfunction"),
                    fold(text, "task alu_driver::run_phase", "endtask"),
                    fold(text, "forever begin", "  end\n"),
                    fold(text, "task alu_driver::drive_item", "endtask"),
                    fold(text, "class alu_error_driver", "endclass"),
                    fold(text, "function new", "endfunction", after="class alu_error_driver"),
                    fold(text, "task drive_item", "endtask", after="class alu_error_driver")]
        assert folds(d) == ordered(expected)
    finally:
        d.close()


SOURCE = '''`define BLOCK begin \\
 int generated; \\
 end
virtual class c;
  extern function void prototype(
    int value
  );
  pure virtual task abstract_method(
    int value
  );
  function new(); endfunction
  function void f();
    begin : outer
      begin : inner
        string s = "class function task begin end endclass";
        // begin function class endfunction end
      end : inner
      begin end
      `BLOCK
    end : outer
  endfunction
endclass
module m;
  initial begin : proc
    int x;
    x = 1;
  end : proc
  if (1) begin : gen
    wire x;
  end : gen
endmodule
`ifdef NEVER_DEFINED
class hidden;
  int x;
endclass
`endif
'''


SOURCE_FOLDS = [("`define BLOCK", "virtual class"), ("class c", "endclass\n"), ("prototype(", "  );"),
                ("abstract_method(", "  );"), ("function void f", "  endfunction"), ("begin : outer", "end : outer"),
                ("begin : inner", "end : inner"), ("module m", "endmodule"), ("begin : proc", "end : proc"),
                ("begin : gen", "end : gen"), ("`ifdef NEVER_DEFINED", "`endif")]


def source_folds(text, without=()):
    return ordered([fold(text, opening, closing) for opening, closing in SOURCE_FOLDS if opening not in without])


def test_line_directive_fold(doc):
    doc.change('`line 100 "virtual.sv" 0\nclass c;\n int value;\nendclass\n')
    assert folds(doc) == [{"startLine": 1, "endLine": 2}]


def test_line_directives_inside_folds(doc):
    original = doc.text
    text = '`line 100 "virtual.sv" 0\n' + SOURCE.replace(
        "      end : inner", '`line 1 "nested.sv" 1\n      end : inner\n`line 200 "virtual.sv" 2')
    doc.change(text)
    assert folds(doc) == source_folds(text)
    doc.change("\n" + text)
    assert folds(doc) == source_folds(doc.text)
    doc.close()
    assert folds(doc) == agent_folds(original)


def test_nested_named_procedural_and_generated_blocks(doc):
    doc.change(SOURCE)
    assert folds(doc) == source_folds(SOURCE)
    # The inner begin is given `end : outer` and the outer begin no end: neither block folds.
    text = SOURCE.replace("      end : inner\n", "")
    doc.change(text)
    assert folds(doc) == source_folds(text, without=("begin : outer", "begin : inner"))


NESTED = "    begin : outer\n      begin : inner\n        int q;\n        q = 1;\n      end : inner\n    end : outer\n"


def test_removed_inner_end_in_build_phase(doc):
    original = doc.text
    text = original.replace("    super.build_phase(phase);\n", "    super.build_phase(phase);\n" + NESTED)
    doc.change(text)
    assert folds(doc) == ordered(agent_folds(text) + [fold(text, "begin : outer", "end : outer"),
                                                      fold(text, "begin : inner", "end : inner")])
    # slang pairs `begin : inner` with `end : outer` and the outer begin with no end.
    text = original.replace("    super.build_phase(phase);\n",
                            "    super.build_phase(phase);\n" + NESTED.replace("      end : inner\n", ""))
    doc.change(text)
    assert folds(doc) == agent_folds(text)
    # The outer begin has no end, and what it holds keeps its own.
    text = original.replace("    super.build_phase(phase);\n",
                            "    super.build_phase(phase);\n" + NESTED.replace("    end : outer\n", ""))
    doc.change(text)
    assert folds(doc) == ordered(agent_folds(text) + [fold(text, "begin : inner", "end : inner")])


UNMATCHED = '''class c;
  function void f();
    if (a) begin
      if (b) begin
        x = 1;
      // inner end deleted
    end else begin
      y = 1;
    end
  endfunction
endclass
module m;
  initial begin : a
    x = 1;
  end : b
  initial begin
    x = 1;
  end : orphan
endmodule
class d;
  function void g();
    x = 1;
  endfunction : h
endclass : e
'''


def test_unlabelled_missing_end_and_mismatched_labels(doc):
    doc.change(UNMATCHED)
    # `if (b) begin` is given the end before `else`; the else block keeps the end below it.
    assert folds(doc) == ordered([fold(UNMATCHED, "class c", "endclass"),
                                  fold(UNMATCHED, "function void f", "endfunction"),
                                  fold(UNMATCHED, "end else begin", "    end\n  endfunction"),
                                  fold(UNMATCHED, "module m", "endmodule")])


def test_unclosed_begin_keeps_blocks_it_holds(doc):
    # While a new begin waits for its end, the blocks after it keep their folds.
    text = ("class c;\n  function void f();\n    begin\n    x = 1;\n    if (x) begin\n      y = 1;\n"
            "    end\n  endfunction\nendclass\n")
    doc.change(text)
    assert folds(doc) == ordered([fold(text, "class c", "endclass"), fold(text, "function", "endfunction"),
                                  fold(text, "if (x) begin", "    end")])


@pytest.mark.parametrize("source,expected", [
    ("class missing;\n  int value;\n", []),
    ("module m; initial begin\n  int value;\nendmodule\n", [{"startLine": 0, "endLine": 1}]),
    ("function void f();\n  int value;\n", []),
    ("class c;\nendclass\n", []),
    ("class c; endclass\n", []),
])
def test_unmatched_and_short_bodies(doc, source, expected):
    doc.change(source)
    assert folds(doc) == expected


@pytest.mark.parametrize("limit", [None, 0, 2])
@pytest.mark.parametrize("line_only", [False, True])
def test_capabilities_and_crlf(tb, limit, line_only):
    caps = {"lineFoldingOnly": line_only}
    if limit is not None:
        caps["rangeLimit"] = limit
    c = start(tb, capabilities={"textDocument": {"foldingRange": caps}})
    try:
        d = Doc(c, os.path.join(tb, "agent", "alu_agent.svh"))
        text = SOURCE.replace("begin : inner", "/* 😀 */ begin : inner").replace("\n", "\r\n").rstrip("\r\n")
        d.change(text)
        assert folds(d) == source_folds(SOURCE)[:limit]
    finally:
        c.close()


def test_duplicate_roots_and_include_endpoints(tmp_path):
    header = tmp_path / "shared.svh"
    header.write_text('class c;\n`include "member.svh"\nendclass\n')
    (tmp_path / "member.svh").write_text("int value;\n")
    (tmp_path / "close.svh").write_text("int x;\nendclass\n")
    (tmp_path / "first.sv").write_text('`include "shared.svh"\nclass cross_file;\n`include "close.svh"\n')
    (tmp_path / "second.sv").write_text('`include "shared.svh"\n')
    c = start(str(tmp_path), capabilities=CAPS, settings={"useBundledUvm": False, "elaboration": "off"})
    try:
        d = Doc(c, str(header))
        assert folds(d) == [{"startLine": 0, "endLine": 1}]
        for name in ("first.sv", "close.svh"):
            other = Doc(c, str(tmp_path / name))
            assert folds(other) == []
    finally:
        c.close()


def span(text, first, last, kind=None, after=""):
    """A fold that hides everything after its first line, through the line of `last`."""
    start = text.index(first, text.index(after))
    found = {"startLine": text.count("\n", 0, start), "endLine": text.count("\n", 0, text.index(last, start))}
    return {**found, "kind": kind} if kind else found


@pytest.mark.parametrize("name,expected", [
    ("tb/tb_top.sv", lambda t: [fold(t, "module tb_top", "endmodule"), fold(t, "u_dut (", "  );"),
                                fold(t, "initial begin", "  end")]),
    ("agent/alu_if.sv", lambda t: [fold(t, "interface alu_if", "endinterface"), fold(t, "clocking drv_cb", "endclocking"),
                                   fold(t, "clocking mon_cb", "endclocking")]),
    ("agent/alu_pkg.sv", lambda t: [fold(t, "package alu_pkg", "endpackage")]),
    ("rtl/alu.sv", lambda t: [fold(t, "module alu", ");"), fold(t, ");", "endmodule"),
                              fold(t, "always_ff", "  end\nendmodule"), fold(t, "if (!rst_n) begin", "    end else"),
                              fold(t, "end else begin", "    end\n  end"), fold(t, "if (valid) begin", "      end\n"),
                              fold(t, "unique case", "endcase")]),
    ("env/alu_coverage.svh", lambda t: [fold(t, "class alu_coverage", "endclass"), fold(t, "covergroup", "endgroup"),
                                        fold(t, "function new", "endfunction"),
                                        fold(t, "function void write", "endfunction")]),
])
def test_units_ports_clocking_case_and_covergroups(client, tb, name, expected):
    # Without a folding provider an editor folds by indentation: these are the folds it gave here.
    d = Doc(client, os.path.join(tb, name))
    try:
        assert folds(d) == ordered(expected(d.text))
    finally:
        d.close()


CONSTRUCTS = '''/*
 * A block comment.
 */
// Function: helper
//
// A NaturalDocs topic.
`define SHOW(a, b) \\
  $display(a, \\
           b);
program prog;
  initial
    fork
      #1;
    join_any
endprogram
package pkg;
  typedef enum logic [1:0] {
    ADD,
    SUB
  } op_e;
  typedef struct packed {
    logic a;
  } pair_t;
  class item #(
    type T = int
  ) extends base;
    rand int x;
    constraint c_x {
      x > 0;
    }
    function void f(
      int a
    );
      x = a; /* trailing
      block */
      /* leading
      */ x++;
    endfunction
    extern function void g(int a,
                           int b);
  endclass
endpackage
interface bus (input logic clk);
  clocking cb @(posedge clk);
    input clk;
  endclocking
  modport mp (
    input clk
  );
  property p_clk;
    @(posedge clk)
      clk;
  endproperty
  sequence s_clk;
    clk ##1
      clk;
  endsequence
endinterface
module top #(parameter int N = 2) (
  input logic clk,
  input logic [1:0] op
);
  covergroup cg @(posedge clk);
    cp_op: coverpoint op {
      bins add = {0};
    }
  endgroup
  sub #(
    .N(N)
  ) u_sub (
    .clk(clk)
  );
  generate
    for (genvar i = 0; i < N; i++) begin : g
      wire w;
    end
  endgenerate
  // #region checks
  always_comb
    case (op)
      2'd0: ;
    endcase
  // #endregion
  initial begin
    `SHOW("%0d",
      N
    )
    $display(
      "%0d", N
    );
    fork
      #1;
    join_none
  end
`ifdef SIM_ONLY
  initial
    $display("sim");
`elsif FAST
  initial
    $display("fast");
`else
  initial
    $display("other");
`endif
endmodule
'''


def constructs_folds(t):
    return ordered([
        span(t, "/*", " */", "comment"), span(t, "// Function", "// A NaturalDocs", "comment"),
        span(t, "`define SHOW", "b);"), fold(t, "program", "endprogram"), fold(t, "fork", "join_any"),
        fold(t, "package", "endpackage"), fold(t, "typedef enum", "} op_e"), fold(t, "typedef struct", "} pair_t"),
        fold(t, "class item #(", ") extends"), fold(t, ") extends", "  endclass"),
        fold(t, "constraint", "    }"), fold(t, "function void f(", "    );"), fold(t, "    );", "endfunction"),
        span(t, "x = a; /* trailing", "block */", "comment"), fold(t, "interface", "endinterface"),
        fold(t, "clocking", "endclocking"), fold(t, "modport mp (", "  );"), fold(t, "property", "endproperty"),
        fold(t, "sequence", "endsequence"), fold(t, "module top", ");\n  covergroup"),
        fold(t, ");\n  covergroup", "endmodule"), fold(t, "covergroup", "endgroup"), fold(t, "coverpoint", "    }"),
        fold(t, "sub #(", ") u_sub"), fold(t, ") u_sub (", "  );"), fold(t, "  generate", "endgenerate"),
        fold(t, "begin : g", "    end\n"), span(t, "// #region", "// #endregion", "region"),
        fold(t, "case (op)", "endcase"), fold(t, "initial begin", "  end\n`ifdef"), fold(t, "`SHOW(", "    )\n"),
        fold(t, "$display(\n", "    );"), fold(t, "fork", "join_none", after="$display(\n"),
        fold(t, "`ifdef", "`elsif"), fold(t, "`elsif", "`else\n"), fold(t, "`else\n", "`endif")])


def test_folds_of_every_construct(tmp_path):
    path = tmp_path / "constructs.sv"
    path.write_text(CONSTRUCTS)
    c = start(str(tmp_path), capabilities=CAPS, settings={"useBundledUvm": False, "elaboration": "off"})
    try:
        d = Doc(c, str(path))
        assert folds(d) == constructs_folds(CONSTRUCTS)
        d.change("\n" + CONSTRUCTS)
        assert folds(d) == constructs_folds(d.text)
    finally:
        c.close()


def test_branch_that_crosses_a_block(doc):
    text = ("module m;\n`ifdef A\n  initial begin\n`else\n  initial begin\n    x = 0;\n`endif\n    x = 1;\n"
            "  end\nendmodule\n")
    doc.change(text)
    # The `else branch would end inside the block its begin opens: it does not fold.
    assert folds(doc) == ordered([fold(text, "module", "endmodule"), fold(text, "`ifdef", "`else"),
                                  fold(text, "initial begin\n    x = 0", "  end\n")])


def test_comments_after_an_included_file(tmp_path):
    # slang moves the trivia after a header's last token to the token after its `include.
    (tmp_path / "head.svh").write_text("class h;\n  int x;\nendclass\n// footer one\n// footer two\n")
    path = tmp_path / "top.sv"
    path.write_text('module top;\n`include "head.svh"\n  // own one\n  // own two\n  int y;\nendmodule\n')
    c = start(str(tmp_path), capabilities=CAPS, settings={"useBundledUvm": False, "elaboration": "off"})
    try:
        d = Doc(c, str(path))
        assert folds(d) == [{"startLine": 0, "endLine": 4}, {"startLine": 2, "endLine": 3, "kind": "comment"}]
        assert folds(Doc(c, str(tmp_path / "head.svh"))) == [{"startLine": 0, "endLine": 1}]
    finally:
        c.close()


def test_library_comments(client):
    from uvmls.project import BUNDLED_UVM

    d = Doc(client, os.path.join(BUNDLED_UVM, "base", "uvm_object.svh"))
    try:
        found = folds(d)
        assert found[0] == span(d.text, "//", "\n\n\ntypedef class", "comment")
        assert span(d.text, "virtual class uvm_object", "\nendclass") in found
        assert span(d.text, "  // Function: new", "  // supplied", "comment") in found
    finally:
        d.close()


def test_crossing_intervals_and_shared_start():
    from uvmls.model import FoldRegion
    from uvmls.navigation import Navigator
    from uvmls.workspace import Workspace

    w = Workspace()
    folds = [(0, 10, None), (0, 7, None), (2, 5, None), (4, 8, None), (11, 15, None), (0, 10, None),
             (6, 9, "branch"), (3, 4, "branch"), (11, 15, "branch"), (12, 14, "region"), (13, 15, "branch")]
    w.folds_by_file["a.sv"] = [FoldRegion("a.sv", *f) for f in folds]
    # A branch or #region that crosses syntax (or one before it) is dropped, never the syntax.
    assert Navigator(w).folding_ranges("a.sv") == [(0, 10, None), (0, 7, None), (2, 5, None), (3, 4, "branch"),
                                                   (11, 15, None), (12, 14, "region")]


def test_deadline_and_intervening_edit(tmp_path, monkeypatch):
    import asyncio
    import threading

    from lsprotocol import types as T
    from uvmls import server as module
    from uvmls.documents import Workspace as LspWorkspace

    async def run():
        path = tmp_path / "a.sv"
        path.write_text("class a;\n int value;\nendclass\n")
        ls = module.UvmLanguageServer()
        ls.protocol._workspace = LspWorkspace(tmp_path.as_uri())
        ls.protocol.client_capabilities = T.ClientCapabilities()
        ls.workspace_roots = [str(tmp_path)]
        ls.settings = {"useBundledUvm": False}
        ls.ws.configure(ls.config())
        params = T.FoldingRangeParams(T.TextDocumentIdentifier(path.as_uri()))
        fresh = ls.fresh

        async def expired():
            return False

        ls.fresh = expired
        assert await module.folding_range(ls, params) == []
        ls.fresh = fresh
        entered, release = threading.Event(), threading.Event()
        compute = module._compute_folding_range

        def delayed(*args):
            entered.set()
            release.wait(5)
            return compute(*args)

        monkeypatch.setattr(module, "_compute_folding_range", delayed)
        try:
            task = asyncio.create_task(module.folding_range(ls, params))
            await asyncio.get_running_loop().run_in_executor(None, entered.wait, 5)
            assert entered.is_set()
            ls.pending.add(str(path))
            release.set()
            assert await task == []
        finally:
            release.set()

    asyncio.run(run())
