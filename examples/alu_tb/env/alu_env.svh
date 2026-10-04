class alu_env extends uvm_env;
  `uvm_component_utils(alu_env)

  alu_env_config m_cfg;
  alu_agent      m_agent;
  alu_scoreboard m_scoreboard;
  alu_coverage   m_coverage;

  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction

  function void build_phase(uvm_phase phase);
    if (!uvm_config_db #(alu_env_config)::get(this, "", "alu_env_config", m_cfg))
      `uvm_fatal("NOCFG", "alu_env_config not found")
    uvm_config_db #(alu_agent_config)::set(this, "m_agent*", "alu_agent_config", m_cfg.m_agent_cfg);
    m_agent = alu_agent::type_id::create("m_agent", this);
    if (m_cfg.has_scoreboard)
      m_scoreboard = alu_scoreboard::type_id::create("m_scoreboard", this);
    if (m_cfg.has_coverage)
      m_coverage = alu_coverage::type_id::create("m_coverage", this);
  endfunction

  function void connect_phase(uvm_phase phase);
    if (m_cfg.has_scoreboard) m_agent.ap.connect(m_scoreboard.item_export);
    if (m_cfg.has_coverage)   m_agent.ap.connect(m_coverage.analysis_export);
  endfunction
endclass
