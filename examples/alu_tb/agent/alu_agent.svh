class alu_agent extends uvm_agent;
  `uvm_component_utils(alu_agent)

  alu_agent_config m_cfg;
  alu_driver       m_driver;
  alu_sequencer    m_sequencer;
  alu_monitor      m_monitor;

  uvm_analysis_port #(alu_seq_item) ap;

  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction

  function void build_phase(uvm_phase phase);
    super.build_phase(phase);
    if (!uvm_config_db #(alu_agent_config)::get(this, "", "alu_agent_config", m_cfg))
      `uvm_fatal("NOCFG", "alu_agent_config not found")
    m_monitor = alu_monitor::type_id::create("m_monitor", this);
    if (m_cfg.is_active == UVM_ACTIVE) begin
      m_driver    = alu_driver::type_id::create("m_driver", this);
      m_sequencer = alu_sequencer::type_id::create("m_sequencer", this);
    end
  endfunction

  function void connect_phase(uvm_phase phase);
    ap = m_monitor.ap;
    if (m_cfg.is_active == UVM_ACTIVE)
      m_driver.seq_item_port.connect(m_sequencer.seq_item_export);
  endfunction
endclass
