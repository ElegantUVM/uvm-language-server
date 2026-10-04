package alu_pkg;
  import uvm_pkg::*;
  `include "uvm_macros.svh"

  typedef enum bit [1:0] {ALU_ADD, ALU_SUB, ALU_AND, ALU_XOR} alu_op_e;

  `include "alu_seq_item.svh"
  `include "alu_agent_config.svh"
  `include "alu_sequencer.svh"
  `include "alu_driver.svh"
  `include "alu_monitor.svh"
  `include "alu_agent.svh"
endpackage
