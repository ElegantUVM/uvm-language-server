"""Competing declarations make config_db type identities unknown, regardless of precedence."""

import pytest

from test_fixes import findings, index
from uvmls.elab import elaborate, request_for
from uvmls.views import config_db_report


SOURCE = '''`include "uvm_macros.svh"
EXTRA
package p;
  import uvm_pkg::*;
  PACKAGE
  class box #(type T = int); endclass
  class base BASE_PARAMS extends uvm_env;
    BASE
    function new(string name, uvm_component parent); super.new(name, parent); endfunction
  endclass
  class env PARAMS extends base;
    LOCAL
    function new(string name, uvm_component parent); super.new(name, parent); endfunction
    box #(SET) sent;
    box #(GET) received;
    function void build_phase(uvm_phase phase);
      uvm_config_db #(box #(SET)) a;
      uvm_config_db #(box #(GET)) b;
      uvm_config_db #(box #(SET))::set(this, "*", "value", sent);
      void'(uvm_config_db #(box #(GET))::get(this, "", "value", received));
      a = b;
    endfunction
  endclass
endpackage
module top; endmodule
'''


def config_source(extra="", package="", base="", local="", params="", base_params="", reverse=False):
    text = SOURCE
    for name, value in (("BASE_PARAMS", base_params), ("EXTRA", extra), ("PACKAGE", package),
                        ("BASE", base), ("LOCAL", local), ("PARAMS", params),
                        ("SET", "item" if reverse else "int"), ("GET", "int" if reverse else "item")):
        text = text.replace(name, value)
    return text


@pytest.mark.parametrize("reverse", [False, True], ids=["get", "set"])
@pytest.mark.parametrize("decls", [
    {"package": "class item; endclass", "base": "typedef int item;", "local": "class item; endclass"},
    {"package": "class item; endclass", "base": "typedef int item;"},
    {"base": "typedef int item;", "local": "class item; endclass"},
    {"package": "class item; endclass", "local": "class item; endclass"},
    {"package": "typedef int item;", "local": "class item; endclass"},
    {"base": "typedef int item;", "local": "typedef byte item;"},
    {"extra": "package q; class item; endclass endpackage", "package": "import q::*;",
     "base": "typedef int item;"},
    {"extra": "package q; typedef byte item; endpackage", "package": "import q::*;",
     "base": "typedef int item;"},
    {"extra": "package q; class item; endclass endpackage", "package": "import q::*; class item; endclass"},
    {"extra": "package q; class item; endclass endpackage", "package": "import q::*; typedef int item;"},
    {"extra": "package q; class item; endclass endpackage\npackage r; class item; endclass endpackage",
     "package": "import q::*; import r::*;"},
    {"extra": "package q; parameter type item = int; endpackage",
     "package": "import q::*; class item; endclass"},
    {"package": "class item; endclass", "params": "#(type item = int)"},
    {"package": "class item; endclass", "base_params": "#(type item = int)"},
    {"package": "class item; endclass", "local": "localparam type item = int;"},
    {"package": "class item; endclass", "base": "localparam type item = int;"},
], ids=["checker-nested", "inherited-package", "inherited-nested", "nested-package",
        "nested-alias", "inherited-local-alias", "inherited-imported-class", "inherited-imported-alias",
        "package-imported-class", "alias-imported-class", "imported-classes", "imported-parameter",
        "parameter", "inherited-parameter",
        "localparam", "inherited-localparam"])
def test_config_ambiguous_type_is_unknown(tmp_path, decls, reverse):
    w = index(tmp_path, {"t.sv": config_source(**decls, reverse=reverse)})
    [row] = config_db_report(w)["fields"]
    assert row["sets" if reverse else "gets"][0]["canon"] is None
    assert row["status"] == "ok"
    assert findings(w, "config-db-type-mismatch") == []


def test_config_checker_nested_class_elaborates_as_distinct(tmp_path):
    text = config_source(package="class item; endclass", base="typedef int item;", local="class item; endclass")
    w = index(tmp_path, {"t.sv": text}, bundled=True)
    result = elaborate(request_for(w))
    assert result["error"] is None
    assert [d["code"] for d in result["diagnostics"] if d["severity"] == "error"] == ["BadAssignment"]
    [row] = config_db_report(w)["fields"]
    # The index cannot prove which item is meant; silence must not claim equal identities.
    assert row["sets"][0]["canon"] == "p::box #(T=bit signed[31:0])"
    assert row["gets"][0]["canon"] is None
    assert row["status"] == "ok"
    assert findings(w, "config-db-type-mismatch") == []


@pytest.mark.parametrize("decls,mismatch", [
    ({"base": "typedef int item;"}, False),
    ({"local": "class item; endclass"}, True),
    ({"package": "class item; endclass"}, True),
    ({"extra": "package q; class item; endclass endpackage", "package": "import q::*;"}, True),
])
def test_config_single_declaration_still_resolves(tmp_path, decls, mismatch):
    w = index(tmp_path, {"t.sv": config_source(**decls)})
    [row] = config_db_report(w)["fields"]
    assert row["sets"][0]["canon"] is not None and row["gets"][0]["canon"] is not None
    assert row["status"] == ("type-mismatch" if mismatch else "ok")
    assert len(findings(w, "config-db-type-mismatch")) == int(mismatch)


def test_config_qualified_name_excludes_class_members(tmp_path):
    text = config_source(package="class item; endclass", base="typedef int item;", local="class item; endclass")
    text = text.replace("box #(item)", "box #(p::item)")
    w = index(tmp_path, {"t.sv": text})
    [row] = config_db_report(w)["fields"]
    assert row["gets"][0]["canon"] == "p::box #(T=p::item)"
    assert row["status"] == "type-mismatch"
    assert len(findings(w, "config-db-type-mismatch")) == 1


@pytest.mark.parametrize("decls", [
    {"package": "parameter int WIDTH = 32;", "base": "localparam int WIDTH = 16;"},
    {"extra": "package q; parameter int WIDTH = 16; endpackage",
     "package": "import q::*; parameter int WIDTH = 32;"},
])
@pytest.mark.parametrize("set_type,get_type", [
    ("bit [31:0]", "bit [WIDTH-1:0]"), ("numbers #(32)", "numbers #(WIDTH)"),
])
def test_config_competing_constants_are_unknown(tmp_path, decls, set_type, get_type):
    text = config_source(**decls).replace("class box #(type T = int);", "class numbers #(int N = 32);")
    text = text.replace("box #(int)", set_type).replace("box #(item)", get_type)
    w = index(tmp_path, {"t.sv": text})
    [row] = config_db_report(w)["fields"]
    assert row["gets"][0]["canon"] is None
    assert row["status"] == "ok"
    assert findings(w, "config-db-type-mismatch") == []
