class alu_random_test extends alu_base_test;
  `uvm_component_utils(alu_random_test)

  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction
endclass

// Same stimulus, but only ADD operations, swapped in through the factory.
class alu_add_test extends alu_base_test;
  `uvm_component_utils(alu_add_test)

  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction

  function void build_phase(uvm_phase phase);
    alu_random_seq::type_id::set_type_override(alu_add_only_seq::get_type());
    super.build_phase(phase);
  endfunction
endclass

// Replaces the driver with the error-injecting one.
class alu_error_test extends alu_base_test;
  `uvm_component_utils(alu_error_test)

  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction

  function void build_phase(uvm_phase phase);
    set_type_override_by_type(alu_driver::get_type(), alu_error_driver::get_type());
    super.build_phase(phase);
  endfunction
endclass

// Passive agent: only the monitor is built.
class alu_passive_test extends alu_base_test;
  `uvm_component_utils(alu_passive_test)

  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction

  virtual function void configure_agent(alu_agent_config cfg);
    cfg.is_active = UVM_PASSIVE;
  endfunction
endclass
