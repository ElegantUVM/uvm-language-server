class alu_coverage extends uvm_subscriber #(alu_seq_item);
  `uvm_component_utils(alu_coverage)

  alu_seq_item item;

  covergroup cg_ops;
    cp_op : coverpoint item.op;
    cp_a  : coverpoint item.a { bins zero = {0}; bins max = {8'hFF}; bins other = default; }
    cp_b  : coverpoint item.b { bins zero = {0}; bins max = {8'hFF}; bins other = default; }
    x_op_a : cross cp_op, cp_a;
  endgroup

  function new(string name, uvm_component parent);
    super.new(name, parent);
    cg_ops = new();
  endfunction

  function void write(alu_seq_item t);
    item = t;
    cg_ops.sample();
  endfunction
endclass
