"""Fixes found on real projects before the first public release (docs/STATUS.md F72: I19, I21, I22)."""
from test_fixes import findings, index

from uvmls.views import config_db_report, topology

NEW = "function new(string name, uvm_component parent); super.new(name, parent); endfunction"

MIXIN = f"""\
import uvm_pkg::*;
`include "uvm_macros.svh"
// A base named by a type parameter is bound by whoever specializes the class: no default is
// not an unknown base (I19).
class mixin #(type T) extends T;
  {NEW}
endclass
class param_env #(type BASE = uvm_env) extends BASE;
  `uvm_component_param_utils(param_env #(BASE))
  {NEW}
endclass
class my_env extends mixin #(uvm_env);
  `uvm_component_utils(my_env)
  {NEW}
endclass
class my_test extends no_such_base;
  `uvm_component_utils(my_test)
  {NEW}
endclass
"""


def test_a_base_named_by_a_type_parameter_is_not_unknown(tmp_path):
    w = index(tmp_path, {"t.sv": MIXIN})
    [d] = findings(w, "unknown-base")
    assert "no_such_base" in d.message


ENV_PARAM = f"""\
package p;
import uvm_pkg::*;
`include "uvm_macros.svh"
class my_agent extends uvm_agent;
  `uvm_component_utils(my_agent)
  {NEW}
endclass
class my_env extends uvm_env;
  `uvm_component_utils(my_env)
  my_agent m_agent;
  {NEW}
  function void build_phase(uvm_phase phase);
    m_agent = my_agent::type_id::create("m_agent", this);
  endfunction
endclass
class other_env extends uvm_env;
  `uvm_component_utils(other_env)
  {NEW}
endclass
// The env's type is a parameter of the base test (OpenTitan's ENV_T, UVMF) (I21).
class base_test #(type ENV_T = my_env) extends uvm_test;
  `uvm_component_param_utils(base_test #(ENV_T))
  ENV_T m_env;
  {NEW}
  function void build_phase(uvm_phase phase);
    m_env = ENV_T::type_id::create("m_env", this);
  endfunction
endclass
class my_test extends base_test #(my_env);
  `uvm_component_utils(my_test)
  {NEW}
endclass
class other_test extends base_test #(.ENV_T(other_env));
  `uvm_component_utils(other_test)
  {NEW}
endclass
class default_test extends base_test;
  `uvm_component_utils(default_test)
  {NEW}
endclass
endpackage
"""


def _env_of(w, test):
    root = topology(w, test)["root"]
    assert root["name"] == "uvm_test_top"
    [env] = root["children"]
    return env


def test_topology_follows_an_env_created_through_a_type_parameter(tmp_path):
    w = index(tmp_path, {"t.sv": ENV_PARAM})
    env = _env_of(w, "my_test")
    assert env["name"] == "m_env" and env["type"] == "my_env" and env["class"] == "p::my_env"
    assert [c["type"] for c in env["children"]] == ["my_agent"]
    assert _env_of(w, "other_test")["type"] == "other_env"
    assert _env_of(w, "default_test")["type"] == "my_env"  # the parameter's default


CONFIG_TYPEDEFS = f"""\
import uvm_pkg::*;
`include "uvm_macros.svh"
class cfg extends uvm_object;
  `uvm_object_utils(cfg)
  function new(string name = "cfg"); super.new(name); endfunction
endclass
typedef uvm_config_db #(cfg) cfg_db;
class env extends uvm_env;
  `uvm_component_utils(env)
  {NEW}
  function void build_phase(uvm_phase phase);
    int verbosity; cfg c; string nm;
    void'(uvm_config_int::get(this, "", "verbosity", verbosity));
    if (!cfg_db::get(this, "", "cfg", c)) `uvm_fatal("E", "no cfg")
    if (!uvm_config_string::get(this, "", "name", nm)) `uvm_fatal("E", "no name")
  endfunction
endclass
class test extends uvm_test;
  `uvm_component_utils(test)
  {NEW}
  function void build_phase(uvm_phase phase);
    cfg c = cfg::type_id::create("c");
    uvm_config_int::set(this, "*", "verbosity", 3);
    cfg_db::set(this, "*", "cfg", c);
  endfunction
endclass
"""


def test_typedefd_config_db_calls_count(tmp_path):
    # UVM's own uvm_config_int/string and a project typedef of uvm_config_db #(T) (I22).
    w = index(tmp_path, {"t.sv": CONFIG_TYPEDEFS})
    rows = {r["field"]: r for r in config_db_report(w)["fields"]}
    assert rows["verbosity"]["status"] == "ok" and rows["verbosity"]["types"] == ["uvm_bitstream_t"]
    assert rows["cfg"]["status"] == "ok" and rows["cfg"]["types"] == ["cfg"]
    assert rows["name"]["status"] == "no-set" and rows["name"]["types"] == ["string"]
    assert [d.data["field"] for d in findings(w, "config-db-no-set")] == ["name"]


DYNAMIC = f"""\
import uvm_pkg::*;
`include "uvm_macros.svh"
interface my_if; endinterface
class test extends uvm_test;
  `uvm_component_utils(test)
  {NEW}
  function void build_phase(uvm_phase phase);
    string names[$] = '{{"a", "b"}};
    foreach (names[i]) uvm_config_db #(int)::set(this, "*", names[i], i);
  endfunction
endclass
class env extends uvm_env;
  `uvm_component_utils(env)
  {NEW}
  function void build_phase(uvm_phase phase);
    int a; virtual my_if vif;
    if (!uvm_config_db #(int)::get(this, "", "a", a)) `uvm_fatal("E", "no a")
    if (!uvm_config_db #(virtual my_if)::get(this, "", "vif", vif)) `uvm_fatal("E", "no vif")
  endfunction
endclass
"""


def test_a_set_with_a_computed_name_only_covers_gets_of_its_type(tmp_path):
    # The sets in the loop may set "a" (an int) but can never set the virtual interface (I22).
    w = index(tmp_path, {"t.sv": DYNAMIC})
    assert [d.data["field"] for d in findings(w, "config-db-no-set")] == ["vif"]
