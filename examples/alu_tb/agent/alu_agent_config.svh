// Knobs and handles shared by the agent's components.
class alu_agent_config extends uvm_object;
  `uvm_object_utils(alu_agent_config)

  virtual alu_if        vif;
  uvm_active_passive_enum is_active = UVM_ACTIVE;
  bit                   has_coverage = 1;
  int unsigned          idle_cycles  = 0;

  function new(string name = "alu_agent_config");
    super.new(name);
  endfunction
endclass
