// Builds the configuration objects and the environment.
class alu_base_test extends uvm_test;
  `uvm_component_utils(alu_base_test)

  alu_env          m_env;
  alu_env_config   m_env_cfg;
  alu_agent_config m_agent_cfg;

  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction

  function void build_phase(uvm_phase phase);
    super.build_phase(phase);
    m_agent_cfg = alu_agent_config::type_id::create("m_agent_cfg");
    if (!uvm_config_db #(virtual alu_if)::get(this, "", "vif", m_agent_cfg.vif))
      `uvm_fatal("NOVIF", "virtual interface not set")
    configure_agent(m_agent_cfg);

    m_env_cfg = alu_env_config::type_id::create("m_env_cfg");
    m_env_cfg.m_agent_cfg = m_agent_cfg;
    uvm_config_db #(alu_env_config)::set(this, "m_env", "alu_env_config", m_env_cfg);

    m_env = alu_env::type_id::create("m_env", this);
  endfunction

  // Hook for derived tests.
  virtual function void configure_agent(alu_agent_config cfg);
  endfunction

  task run_phase(uvm_phase phase);
    alu_random_seq seq;
    phase.raise_objection(this);
    seq = alu_random_seq::type_id::create("seq");
    if (!seq.randomize()) `uvm_error("RAND", "randomize failed")
    seq.start(m_env.m_agent.m_sequencer);
    phase.drop_objection(this);
  endtask
endclass
