interface alu_if #(parameter int W = 8) (input logic clk, input logic rst_n);
  logic         valid;
  logic [1:0]   op;
  logic [W-1:0] a;
  logic [W-1:0] b;
  logic [W:0]   result;
  logic         ready;

  clocking drv_cb @(posedge clk);
    output valid, op, a, b;
  endclocking

  clocking mon_cb @(posedge clk);
    input valid, op, a, b, result, ready;
  endclocking

  modport drv (clocking drv_cb, input rst_n);
  modport mon (clocking mon_cb, input rst_n);
endinterface
