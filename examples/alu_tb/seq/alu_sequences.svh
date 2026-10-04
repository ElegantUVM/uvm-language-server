virtual class alu_base_seq extends uvm_sequence #(alu_seq_item);
  rand int unsigned n_items = 20;
  constraint c_len { n_items inside {[1:100]}; }

  function new(string name = "alu_base_seq");
    super.new(name);
  endfunction
endclass

class alu_random_seq extends alu_base_seq;
  `uvm_object_utils(alu_random_seq)

  function new(string name = "alu_random_seq");
    super.new(name);
  endfunction

  virtual task body();
    repeat (n_items) begin
      req = alu_seq_item::type_id::create("req");
      start_item(req);
      if (!req.randomize()) `uvm_error("RAND", "randomize failed")
      finish_item(req);
    end
  endtask
endclass

class alu_add_only_seq extends alu_random_seq;
  `uvm_object_utils(alu_add_only_seq)

  function new(string name = "alu_add_only_seq");
    super.new(name);
  endfunction

  virtual task body();
    repeat (n_items) begin
      `uvm_do_with(req, { op == ALU_ADD; })
    end
  endtask
endclass

class alu_corner_seq extends alu_base_seq;
  `uvm_object_utils(alu_corner_seq)

  alu_op_e req_ops[] = '{ALU_ADD, ALU_SUB, ALU_AND, ALU_XOR};

  function new(string name = "alu_corner_seq");
    super.new(name);
  endfunction

  virtual task body();
    foreach (req_ops[i]) begin
      req = alu_seq_item::type_id::create("req");
      start_item(req);
      void'(req.randomize() with { op == req_ops[i]; a inside {0, 8'hFF}; b inside {0, 8'hFF}; });
      finish_item(req);
    end
  endtask
endclass
