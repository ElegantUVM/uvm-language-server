// Simple registered ALU used as the DUT for the example testbench.
module alu #(parameter int W = 8) (
  input  logic         clk,
  input  logic         rst_n,
  input  logic         valid,
  input  logic [1:0]   op,
  input  logic [W-1:0] a,
  input  logic [W-1:0] b,
  output logic [W:0]   result,
  output logic         ready
);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      result <= '0;
      ready  <= 1'b0;
    end else begin
      ready <= valid;
      if (valid) begin
        unique case (op)
          2'b00: result <= a + b;
          2'b01: result <= a - b;
          2'b10: result <= {1'b0, a & b};
          2'b11: result <= {1'b0, a ^ b};
        endcase
      end
    end
  end
endmodule
