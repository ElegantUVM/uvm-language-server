// One ALU operation: operands, opcode and the observed result.
class alu_seq_item extends uvm_sequence_item;
  rand alu_op_e   op;
  rand bit [7:0]  a;
  rand bit [7:0]  b;
       bit [8:0]  result;

  constraint c_no_trivial_sub { op == ALU_SUB -> a >= b; }

  `uvm_object_utils_begin(alu_seq_item)
    `uvm_field_enum(alu_op_e, op, UVM_ALL_ON)
    `uvm_field_int(a, UVM_ALL_ON)
    `uvm_field_int(b, UVM_ALL_ON)
    `uvm_field_int(result, UVM_ALL_ON | UVM_NOCOMPARE)
  `uvm_object_utils_end

  function new(string name = "alu_seq_item");
    super.new(name);
  endfunction

  // Reference model used by the scoreboard.
  function bit [8:0] expected();
    case (op)
      ALU_ADD: return a + b;
      ALU_SUB: return a - b;
      ALU_AND: return {1'b0, a & b};
      ALU_XOR: return {1'b0, a ^ b};
    endcase
  endfunction
endclass
