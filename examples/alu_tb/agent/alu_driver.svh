// Drives alu_seq_items onto the pin interface.
class alu_driver extends uvm_driver #(alu_seq_item);
  `uvm_component_utils(alu_driver)

  alu_agent_config m_cfg;
  virtual alu_if   vif;

  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction

  function void build_phase(uvm_phase phase);
    super.build_phase(phase);
    if (!uvm_config_db #(alu_agent_config)::get(this, "", "alu_agent_config", m_cfg))
      `uvm_fatal("NOCFG", "alu_agent_config not found")
    vif = m_cfg.vif;
  endfunction

  extern virtual task run_phase(uvm_phase phase);
  extern virtual protected task drive_item(alu_seq_item item);
endclass

task alu_driver::run_phase(uvm_phase phase);
  vif.drv_cb.valid <= 1'b0;
  forever begin
    seq_item_port.get_next_item(req);
    drive_item(req);
    seq_item_port.item_done();
  end
endtask

task alu_driver::drive_item(alu_seq_item item);
  @(vif.drv_cb);
  vif.drv_cb.valid <= 1'b1;
  vif.drv_cb.op    <= item.op;
  vif.drv_cb.a     <= item.a;
  vif.drv_cb.b     <= item.b;
  @(vif.drv_cb);
  vif.drv_cb.valid <= 1'b0;
  repeat (m_cfg.idle_cycles) @(vif.drv_cb);
endtask

// Error-injecting driver used by alu_error_test through a factory override.
class alu_error_driver extends alu_driver;
  `uvm_component_utils(alu_error_driver)

  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction

  virtual protected task drive_item(alu_seq_item item);
    if ($urandom_range(0, 9) == 0) item.b = ~item.b;
    super.drive_item(item);
  endtask
endclass
