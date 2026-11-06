/*
 * SPLICE: small FIFO with the head word always visible
 * Copyright (c) 2026 Prajesh-47
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module splice_fifo #(parameter DEPTH_LOG2 = 2) (
    input  wire        clk,
    input  wire        clear,
    input  wire        push,
    input  wire [15:0] din,
    output wire        full,
    input  wire        pop,
    output wire [15:0] head,
    output wire        empty
);
  localparam DEPTH = 1 << DEPTH_LOG2;
  reg [15:0] mem [0:DEPTH-1];
  reg [DEPTH_LOG2:0] wp, rp;

  assign empty = wp == rp;
  assign full  = wp[DEPTH_LOG2] != rp[DEPTH_LOG2] && wp[DEPTH_LOG2-1:0] == rp[DEPTH_LOG2-1:0];
  // An empty FIFO reads as 0, not as whatever an unwritten or old slot holds.
  assign head  = empty ? 16'd0 : mem[rp[DEPTH_LOG2-1:0]];

  always @(posedge clk) begin
    if (clear) begin
      wp <= 0;
      rp <= 0;
    end else begin
      if (push && !full) begin
        mem[wp[DEPTH_LOG2-1:0]] <= din;
        wp <= wp + 1'b1;
      end
      if (pop && !empty) rp <= rp + 1'b1;
    end
  end
endmodule
