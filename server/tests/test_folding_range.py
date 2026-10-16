"""Structural folds come from matched syntax tokens, including unsaved buffers."""

import os
import shutil

import pytest

from uvmls.server import path_to_uri

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
                    fold(text, "UVM_ACTIVE) begin", "    end\n"),
                    # A one-statement body folds with its if, as indentation folding did.
                    span(text, "if (!uvm_config_db", "`uvm_fatal"),
                    span(text, "if (m_cfg.is_active == UVM_ACTIVE)\n", "m_driver.seq_item_port")])


@pytest.mark.parametrize("caps,present", [({}, False), (CAPS, True),
    ({"textDocument": {"foldingRange": {"dynamicRegistration": False}}}, True),
    ({"textDocument": {"formatting": {}, "rangeFormatting": {}, "signatureHelp": {},
                       "documentHighlight": {}, "foldingRange": {}}}, True)])
def test_registration(tb, caps, present):
    c = Client()
    try:
        result = c.request("initialize", {"processId": None, "rootUri": path_to_uri(str(tb)), "capabilities": caps})
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
                    span(text, "if (!uvm_config_db", "`uvm_fatal"),
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


CASE_ITEMS = ("module m;\n  always @* begin\n    case (x)\n      1: begin\n           y = 1;\n           z = 1;\n"
              "         end\n      2: begin\n           y = 2;\n           z = 2;\n         end\n    endcase\n"
              "\tif (y) begin\n\t  q = 1;\n        end\n  end\nendmodule\n")


@pytest.mark.parametrize("typed", ["", "    if (a) begin\n"])
def test_unclosed_begin_keeps_blocks_lined_up_with_their_begin(doc, typed):
    # A case item's end under its begin, and an end indented with spaces under a tab-indented if.
    text = CASE_ITEMS.replace("    case", typed + "    case")
    doc.change(text)
    inner = [fold(text, "1: begin", "         end"), fold(text, "2: begin", "         end", after="1: begin"),
             fold(text, "if (y) begin", "        end")]
    assert folds(doc) == ordered([fold(text, "module", "endmodule"), fold(text, "case", "endcase"), *inner,
                                  *([] if typed else [fold(text, "always", "  end\nendmodule")])])


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
    ("agent/alu_if.sv", lambda t: [fold(t, "interface alu_if", "endinterface"),
                                   fold(t, "clocking drv_cb", "endclocking"), fold(t, "clocking mon_cb", "endclocking")]),
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
        fold(t, "initial\n    fork", "join_any"), fold(t, "always_comb", "endcase"),
        span(t, '  initial\n    $display("other")', '$display("other")'),
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


BODIES = '''`define SHOW(a, b) begin $display(a, b); end
module bodies;
  always @(posedge clk)
    if (rst)
      q <= 0;
    else if (en)
      q <= d;
    else
      q <= q;
  always_comb
    case (x)
      1:
        y = 1;
      default: y = 2;
    endcase
  initial begin
    foreach (a[i])
      a[i] = 0;
    for (int i = 0;
         i < 2;
         i++) begin
      b = 1;
    end
    if (a)
      begin
        x = 1;
      end
    else
      `SHOW("msg",
            x)
    do
      x++;
    while (x < 3);
    if (c) x = 1;
    if (d)
      x = 1; else x = 2;
  end
  if (N > 1)
    assign w = 1;
  else
    assign w = 0;
  for (genvar g = 0; g < 2; g++)
    assign v[g] = 1;
endmodule
'''


def test_bodies_without_begin(tmp_path):
    path = tmp_path / "bodies.sv"
    path.write_text(BODIES)
    c = start(str(tmp_path), capabilities=CAPS, settings={"useBundledUvm": False, "elaboration": "off"})
    t = BODIES
    try:
        # What indentation folding gave: a body on a later line folds with its header, through the
        # line before a block body's closer; a body on the header's line folds by itself.
        assert folds(Doc(c, str(path))) == ordered([
            fold(t, "module", "endmodule"), span(t, "always @", "q <= q;"), span(t, "if (rst)", "q <= 0;"),
            span(t, "else if (en)", "q <= d;"), span(t, "    else\n      q <= q", "q <= q;"),
            fold(t, "always_comb", "endcase"), fold(t, "case (x)", "endcase"), span(t, "1:\n", "y = 1;"),
            fold(t, "initial begin", "  end\n  if (N"), span(t, "foreach", "a[i] = 0;"),
            fold(t, "for (int i", "    end\n"), fold(t, "i++) begin", "    end\n"),
            fold(t, "if (a)\n      begin", "      end\n"), fold(t, "begin\n        x = 1", "      end\n"),
            span(t, "else\n      `SHOW", "x)"), span(t, "do\n", "x++;"), span(t, "if (N > 1)", "assign w = 1;"),
            span(t, "  else\n    assign w = 0", "assign w = 0;"), span(t, "for (genvar", "assign v[g] = 1;")])
    finally:
        c.close()


def test_body_that_crosses_a_block(doc):
    text = "module m;\n  always @(posedge clk)\n    x <= 1; initial begin\n    y = 1;\n  end\nendmodule\n"
    doc.change(text)
    # The always's body ends on the line where the initial block starts: the block keeps its fold.
    assert folds(doc) == ordered([fold(text, "module", "endmodule"), fold(text, "initial begin", "  end")])


def test_branch_that_crosses_a_block(doc):
    text = ("module m;\n`ifdef A\n  initial begin\n`else\n  initial begin\n    x = 0;\n`endif\n    x = 1;\n"
            "  end\nendmodule\n")
    doc.change(text)
    # The `else branch would end inside the block its begin opens: it does not fold.
    assert folds(doc) == ordered([fold(text, "module", "endmodule"), fold(text, "`ifdef", "`else"),
                                  fold(text, "initial begin\n    x = 0", "  end\n")])


def test_region_markers_with_or_without_hash(doc):
    text = ("module m;\n  // region inputs\n  wire a;\n  // #region inner\n  wire b;\n  //#endregion\n"
            "  // endregion\n  // the next\n  // region of memory\n  // is spare\n  wire c;\nendmodule\n")
    doc.change(text)
    # A marker with no partner may be prose: it stays in its run of comments.
    assert folds(doc) == ordered([fold(text, "module", "endmodule"), span(text, "// region", "// endregion", "region"),
                                  span(text, "// #region", "//#endregion", "region"),
                                  span(text, "// the next", "// is spare", "comment")])


UTILS = '''class item extends uvm_sequence_item;
  rand int a;
  `uvm_object_utils_begin(item)
    `uvm_field_int(a, UVM_ALL_ON)
  `uvm_object_utils_end
  task t();
    `uvm_info_begin("ID", "msg", UVM_LOW)
      `uvm_message_add_int(a, UVM_DEC)
    `uvm_info_end
  endtask
endclass
'''


def test_uvm_begin_and_end_macros(doc):
    doc.change(UTILS)
    assert folds(doc) == ordered([fold(UTILS, "class", "endclass"), fold(UTILS, "task", "endtask"),
                                  fold(UTILS, "`uvm_object_utils_begin", "`uvm_object_utils_end"),
                                  fold(UTILS, "`uvm_info_begin", "`uvm_info_end")])
    # Only UVM's macros are known to pair.
    text = "`define my_begin\n`define my_end\n" + UTILS.replace("uvm_object_utils_", "my_").replace("(item)", "")
    doc.change(text)
    assert folds(doc) == ordered([fold(text, "class", "endclass"), fold(text, "task", "endtask"),
                                  fold(text, "`uvm_info_begin", "`uvm_info_end")])


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


@pytest.mark.parametrize("folding,refresh", [(True, True), (True, False), (True, None), (False, True)])
def test_refresh_after_index(tb, folding, refresh):
    c = Client()
    requests = []
    read = c.read

    def record(timeout=30):
        msg = read(timeout)
        if "id" in msg and "method" in msg:
            requests.append(msg["method"])
        return msg

    c.read = record
    caps = {"textDocument": {"foldingRange": {}}} if folding else {}
    if refresh is not None:
        caps["workspace"] = {"foldingRange": {"refreshSupport": refresh}}
    count = int(bool(folding and refresh))
    try:
        c.request("initialize", {"processId": None, "rootUri": path_to_uri(str(tb)), "capabilities": caps,
                                "initializationOptions": {"settings": {"filelists": [os.path.join(tb, "alu_tb.f")],
                                                                       "elaboration": "off"}}})
        c.notify("initialized", {})
        c.wait_for("uvm/indexChanged")
        c.request("workspace/symbol", {"query": "alu_agent"})  # reads what the server sent meanwhile
        # A fold request answered null while indexing is asked again.
        assert requests.count("workspace/foldingRange/refresh") == count
        d = Doc(c, os.path.join(tb, "agent", "alu_agent.svh"))
        d.change("\n" + d.text)
        c.wait_for("uvm/indexChanged")
        c.request("workspace/symbol", {"query": "alu_agent"})
        # An edit can change another open file's folds, an including or included one.
        assert requests.count("workspace/foldingRange/refresh") >= 2 * count
        assert count or "workspace/foldingRange/refresh" not in requests
    finally:
        c.close()


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


def test_crossing_check_matches_pairwise_check():
    import random
    import time

    from uvmls.model import FoldRegion
    from uvmls.navigation import CROSSING_FOLDS, Navigator
    from uvmls.workspace import Workspace

    def pairwise(folds):
        kinds = {}
        for f in sorted(folds, key=lambda f: f.kind in CROSSING_FOLDS):
            kinds.setdefault((f.start, f.end), f.kind)
        out, ends = [], []
        for start, end in sorted((p for p, k in kinds.items() if k not in CROSSING_FOLDS), key=lambda p: (p[0], -p[1])):
            while ends and start > ends[-1]:
                ends.pop()
            if not ends or end <= ends[-1]:
                out.append((start, end))
                ends.append(end)
        for start, end in sorted(p for p, k in kinds.items() if k in CROSSING_FOLDS):
            if not any(a < start <= b < end or start < a <= end < b for a, b in out):
                out.append((start, end))
        return [(a, b, kinds[a, b]) for a, b in sorted(out, key=lambda p: (p[0], -p[1]))]

    rng, w = random.Random(7), Workspace()
    for _ in range(3000):
        lines = rng.randint(2, 25)
        starts = [rng.randrange(lines) for _ in range(rng.randint(0, 14))]
        folds = [FoldRegion("a.sv", a, rng.randint(a + 1, lines), rng.choice([None, None, "comment", "branch", "region"]))
                 for a in starts]
        w.folds_by_file["a.sv"] = folds
        assert Navigator(w).folding_ranges("a.sv") == pairwise(folds)
    # Each of 4000 `ifdef branches around a class with two methods: a sweep, not a check against every fold.
    w.folds_by_file["a.sv"] = [FoldRegion("a.sv", *f) for i in range(0, 40000, 10)
                               for f in ((i, i + 8, "branch"), (i + 1, i + 7), (i + 2, i + 3), (i + 5, i + 6))]
    start = time.perf_counter()
    assert len(Navigator(w).folding_ranges("a.sv")) == 16000
    assert time.perf_counter() - start < 1


def test_no_crossing_sweeps_without_crossing_folds(monkeypatch):
    from uvmls import navigation
    from uvmls.model import FoldRegion
    from uvmls.workspace import Workspace

    w = Workspace()
    w.folds_by_file["a.sv"] = [FoldRegion("a.sv", *f) for f in ((0, 10, None), (2, 5, "comment"), (4, 8, None))]
    sweeps, nested = [], navigation._nested
    monkeypatch.setattr(navigation, "_start_inside", lambda *a: sweeps.append(a) or set())
    monkeypatch.setattr(navigation, "_nested", lambda spans: sweeps.append(spans) or nested(spans))
    assert navigation.Navigator(w).folding_ranges("a.sv") == [(0, 10, None), (2, 5, "comment")]
    assert len(sweeps) == 1  # the syntax's own nesting, nothing for crossings


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
        assert await module.folding_range(ls, params) is None  # the editor folds by indentation meanwhile
        ls.fresh = fresh
        entered, release = threading.Event(), threading.Event()
        compute = module._compute_folding_range

        def delayed(*args):
            entered.set()
            release.wait(5)
            return compute(*args)

        monkeypatch.setattr(module, "_compute_folding_range", delayed)
        monkeypatch.setattr(ls, "after_index", lambda: None)
        try:
            task = asyncio.create_task(module.folding_range(ls, params))
            await asyncio.get_running_loop().run_in_executor(None, entered.wait, 5)
            assert entered.is_set()
            path.write_text("\n\nclass a;\n int value;\nendclass\n")
            ls.pending.add(str(path))
            release.set()
            # Computed again for the edited file: never the lines it had.
            assert await task == [T.FoldingRange(2, 3)]
        finally:
            release.set()

    asyncio.run(run())


@pytest.mark.parametrize("limit", [2, 0])
def test_range_limit_on_the_agent(tb, limit):
    c = start(tb, capabilities={"textDocument": {"foldingRange": {"rangeLimit": limit, "lineFoldingOnly": True}}})
    try:
        d = Doc(c, os.path.join(tb, "agent", "alu_agent.svh"))
        text = d.text.replace("    super.build_phase(phase);\n", "    super.build_phase(phase);\n" + NESTED)
        d.change(text)
        expected = ordered(agent_folds(text) + [fold(text, "begin : outer", "end : outer"),
                                                fold(text, "begin : inner", "end : inner")])
        assert folds(d) == expected[:limit]
        assert expected[:2] == [fold(text, "class alu_agent", "endclass"), fold(text, "function new", "endfunction")]
    finally:
        c.close()
