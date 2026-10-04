// Compares every observed result against the item's reference model.
class alu_scoreboard extends uvm_scoreboard;
  `uvm_component_utils(alu_scoreboard)

  uvm_analysis_imp #(alu_seq_item, alu_scoreboard) item_export;
  int unsigned n_pass, n_fail;

  function new(string name, uvm_component parent);
    super.new(name, parent);
  endfunction

  function void build_phase(uvm_phase phase);
    item_export = new("item_export", this);
  endfunction

  function void write(alu_seq_item t);
    if (t.result == t.expected()) n_pass++;
    else begin
      n_fail++;
      `uvm_error("MISMATCH", $sformatf("%s: got %0d", t.convert2string(), t.result))
    end
  endfunction

  function void report_phase(uvm_phase phase);
    `uvm_info("SB", $sformatf("pass=%0d fail=%0d", n_pass, n_fail), UVM_LOW)
  endfunction
endclass
