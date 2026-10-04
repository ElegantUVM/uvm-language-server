class alu_env_config extends uvm_object;
  `uvm_object_utils(alu_env_config)

  alu_agent_config m_agent_cfg;
  bit              has_scoreboard = 1;
  bit              has_coverage   = 1;

  function new(string name = "alu_env_config");
    super.new(name);
  endfunction
endclass
