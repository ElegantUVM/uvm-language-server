module tb_top;
  import uvm_pkg::*;
  import alu_test_pkg::*;
  `include "uvm_macros.svh"

  logic clk = 0;
  logic rst_n = 0;
  always #5 clk = ~clk;
  initial #20 rst_n = 1;

  alu_if #(.W(8)) u_if (.clk(clk), .rst_n(rst_n));

  alu #(.W(8)) u_dut (
    .clk(clk), .rst_n(rst_n),
    .valid(u_if.valid), .op(u_if.op), .a(u_if.a), .b(u_if.b),
    .result(u_if.result), .ready(u_if.ready)
  );

  initial begin
    uvm_config_db #(virtual alu_if)::set(null, "uvm_test_top", "vif", u_if);
    run_test("alu_random_test");
  end
endmodule
