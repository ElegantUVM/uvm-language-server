// Samples the interface and publishes completed operations.
class alu_monitor extends uvm_monitor;
  `uvm_component_utils(alu_monitor)

  uvm_analysis_port #(alu_seq_item) ap;
  alu_agent_config                  m_cfg;
  virtual alu_if                    vif;
  bit                               checks_enable = 1;

  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction

  function void build_phase(uvm_phase phase);
    super.build_phase(phase);
    ap = new("ap", this);
    if (!uvm_config_db #(alu_agent_config)::get(this, "", "alu_agent_config", m_cfg))
      `uvm_fatal("NOCFG", "alu_agent_config not found")
    // Nothing in the testbench sets this; the language server flags it.
    void'(uvm_config_db #(bit)::get(this, "", "checks_enable", checks_enable));
    vif = m_cfg.vif;
  endfunction

  task run_phase(uvm_phase phase);
    alu_seq_item item;
    forever begin
      @(vif.mon_cb);
      if (vif.mon_cb.valid) begin
        item = alu_seq_item::type_id::create("item");
        item.op = alu_op_e'(vif.mon_cb.op);
        item.a  = vif.mon_cb.a;
        item.b  = vif.mon_cb.b;
        @(vif.mon_cb);
        item.result = vif.mon_cb.result;
        ap.write(item);
      end
    end
  endtask
endclass
