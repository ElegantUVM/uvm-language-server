"""config_db matches type identities, including specialization arguments."""

import pytest
import pyslang as P

from test_fixes import findings, index
from uvmls.views import config_db_report


def check_specialization_identity(a, b, declarations="", same=True):
    text = declarations + f"""
class identity #(type T = int); endclass
module top;
  identity #({a}) a;
  identity #({b}) b;
  initial a = b;
endmodule
"""
    comp = P.ast.Compilation()
    comp.addSyntaxTree(P.syntax.SyntaxTree.fromText(text))
    assert [str(d.code) for d in comp.getAllDiagnostics()] == ([] if same else ["DiagCode(BadAssignment)"])


def config_pair(tmp_path, set_type, get_type, declarations=""):
    text = '''import uvm_pkg::*;
DECLS
class env extends uvm_env;
  SET sent;
  GET received;
  function void build_phase(uvm_phase phase);
    uvm_config_db #(SET)::set(this, "*", "value", sent);
    void'(uvm_config_db #(GET)::get(this, "", "value", received));
  endfunction
endclass
'''.replace("DECLS", declarations).replace("SET", set_type).replace("GET", get_type)
    w = index(tmp_path, {"t.sv": text})
    [row] = config_db_report(w)["fields"]
    return w, row


@pytest.mark.parametrize("set_type,get_type", [
    ("int signed", "int"), ("reg [7:0]", "logic [7:0]"), ("reg", "logic"),
    ("integer", "logic signed [31:0]"), ("int", "bit signed [31:0]"),
    ("byte", "bit signed [7:0]"), ("time", "logic [63:0]"),
    ("bit unsigned [3:0]", "bit [3:0]"), ("bit [8-1:0]", "bit [7:0]"),
    ("addr_t", "bit [31:0]"), ("bit", "bit [0:0]"),
])
def test_config_equivalent_integral_types(tmp_path, set_type, get_type):
    declarations = "parameter int ADDR_W = 32; typedef bit [ADDR_W-1:0] addr_t;"
    check_specialization_identity(set_type, get_type, declarations)
    w, row = config_pair(tmp_path, set_type, get_type, declarations)
    assert findings(w, "config-db-type-mismatch") == []
    assert row["status"] == "ok"


@pytest.mark.parametrize("set_type,get_type", [
    ("int", "int unsigned"), ("bit [31:0]", "bit [0:31]"),
    ("logic signed [7:0]", "logic [7:0]"), ("bit [7:0]", "logic [7:0]"),
])
def test_config_distinct_integral_types(tmp_path, set_type, get_type):
    check_specialization_identity(set_type, get_type, same=False)
    w, row = config_pair(tmp_path, set_type, get_type)
    assert len(findings(w, "config-db-type-mismatch")) == 1
    assert row["status"] == "type-mismatch"


@pytest.mark.parametrize("set_type,get_type,mismatch", [
    ("box #(p::item)", "box #(q::item)", True),
    ("box #(int)", "box #(count_t)", False),
    ("box #(p::item)", "box #(pitem_t)", False),
    ("box #(box #(int))", "box #(box #(count_t))", False),
    ("box", "box #(int)", False),
    ("box #(.T(count_t))", "box #(int)", False),
    ("numbers #(8)", "numbers #(16)", True),
    ("numbers #(8)", "numbers #(W)", False),
])
def test_config_specialization_arguments(tmp_path, set_type, get_type, mismatch):
    declarations = '''package p; class item; endclass endpackage
package q; class item; endclass endpackage
class box #(type T = int); endclass
class numbers #(int N = 4); endclass
typedef int count_t;
typedef p::item pitem_t;
localparam int W = 8;
'''
    check_specialization_identity(set_type, get_type, declarations, same=not mismatch)
    w, row = config_pair(tmp_path, set_type, get_type, declarations)
    assert len(findings(w, "config-db-type-mismatch")) == int(mismatch)
    assert row["status"] == ("type-mismatch" if mismatch else "ok")


@pytest.mark.parametrize("get_type,mismatch", [
    ("p::pair #(p::item)", False),
    ("p::pair #(item)", True),
    ("p::pair #(pitem_t)", False),
])
def test_config_defaults_use_the_declaration_scope(tmp_path, get_type, mismatch):
    declarations = '''package p;
  class item; endclass
  class pair #(type REQ = item, type RSP = REQ); endclass
endpackage
package q; class item; endclass endpackage
import q::*;
typedef p::item pitem_t;
'''
    w, row = config_pair(tmp_path, "p::pair", get_type, declarations)
    assert len(findings(w, "config-db-type-mismatch")) == int(mismatch)
    assert row["status"] == ("type-mismatch" if mismatch else "ok")
    assert "REQ=p::item, RSP=p::item" in row["sets"][0]["canon"]


@pytest.mark.parametrize("get_type,mismatch", [
    ("virtual bus", False), ("virtual bus #(.WIDTH(8))", False), ("virtual bus #(16)", True),
])
def test_config_interface_parameter_identity(tmp_path, get_type, mismatch):
    w, row = config_pair(tmp_path, "virtual bus #(8)", get_type,
                         "interface bus #(parameter int WIDTH = 8); endinterface")
    assert len(findings(w, "config-db-type-mismatch")) == int(mismatch)
    assert row["status"] == ("type-mismatch" if mismatch else "ok")


@pytest.mark.parametrize("width,mismatch", [(32, False), (16, True)])
def test_config_package_typedef_bounds(tmp_path, width, mismatch):
    declarations = '''package p;
  parameter int ADDR_W = 32;
  typedef bit [ADDR_W-1:0] addr_t;
endpackage
'''
    w, row = config_pair(tmp_path, "p::addr_t", f"bit [{width}-1:0]", declarations)
    assert row["sets"][0]["canon"] == "bit[31:0]"
    assert len(findings(w, "config-db-type-mismatch")) == int(mismatch)


@pytest.mark.parametrize("bound", ["UNKNOWN", "$clog2(WIDTH)", "2147483647+1", "W"])
def test_config_unknown_bounds_do_not_claim_a_mismatch(tmp_path, bound):
    w, row = config_pair(tmp_path, f"bit [{bound}:0]", "bit [31:0]",
                         "parameter bit [3:0] W = 32;")
    assert row["sets"][0]["canon"] is None
    assert findings(w, "config-db-type-mismatch") == []


@pytest.mark.parametrize("other,mismatch", [("p::item", False), ("q::item", True)])
def test_config_uvm_sequencer_argument_identity(tmp_path, other, mismatch):
    text = '''`include "uvm_macros.svh"
package p; import uvm_pkg::*; class item extends uvm_sequence_item; endclass endpackage
package q; import uvm_pkg::*; class item extends uvm_sequence_item; endclass endpackage
import uvm_pkg::*;
import p::*;
class env extends uvm_env;
  uvm_sequencer #(item) a;
  uvm_sequencer #(OTHER) b;
  function void build_phase(uvm_phase phase);
    uvm_config_db #(uvm_sequencer #(item))::set(this, "*", "seqr", a);
    void'(uvm_config_db #(uvm_sequencer #(OTHER))::get(this, "", "seqr", b));
  endfunction
endclass
'''.replace("OTHER", other)
    w = index(tmp_path, {"t.sv": text}, bundled=True)
    [row] = config_db_report(w)["fields"]
    assert row["sets"][0]["canon"] == "uvm_pkg::uvm_sequencer #(REQ=p::item, RSP=p::item)"
    assert len(findings(w, "config-db-type-mismatch")) == int(mismatch)


@pytest.mark.parametrize("set_type,get_type", [
    ('words #("two  spaces")', 'words #("two spaces")'),
    ("numbers #(8'h08)", "numbers #(8)"),
])
def test_config_unknown_value_arguments_are_not_compared_as_text(tmp_path, set_type, get_type):
    w, row = config_pair(tmp_path, set_type, get_type,
                         'class words #(string S = ""); endclass class numbers #(int N = 4); endclass')
    assert row["sets"][0]["canon"] is None
    assert findings(w, "config-db-type-mismatch") == []
