/*
 * SPLICE: small FIFO with the head word always visible
 * Copyright (c) 2026 Prajesh-47
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module splice_fifo #(parameter DEPTH_LOG2 = 2) (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        clear,
    input  wire        push,
    input  wire [15:0] din,
    output wire        full,
    input  wire        pop,
    output wire [15:0] head,
    output wire        empty
`ifdef FORMAL
    ,
    output wire [DEPTH_LOG2:0]   wp_out,
    output wire [DEPTH_LOG2:0]   rp_out,
    input  wire [DEPTH_LOG2-1:0] peek,
    output wire [15:0]           peek_word
`endif
);
  localparam DEPTH = 1 << DEPTH_LOG2;
  reg [15:0] mem [0:DEPTH-1];
  reg [DEPTH_LOG2:0] wp, rp;

`ifdef FORMAL
  assign wp_out = wp;
  assign rp_out = rp;
  assign peek_word = mem[peek];
`endif

  assign empty = wp == rp;
  assign full  = wp[DEPTH_LOG2] != rp[DEPTH_LOG2] && wp[DEPTH_LOG2-1:0] == rp[DEPTH_LOG2-1:0];
  // An empty FIFO reads as 0, not as whatever an unwritten or old slot holds.
  assign head  = empty ? 16'd0 : mem[rp[DEPTH_LOG2-1:0]];

  // Asynchronous reset on the pointers only. Synthesis folds a synchronous clear into the increment,
  // giving !(wp | !wp), which is 0 on silicon but stays X forever in gate-level simulation. Clear is
  // still high when rst_n rises, so D is 0 across the release and recovery time can't matter.
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      wp <= 0;
      rp <= 0;
    end else if (clear) begin
      wp <= 0;
      rp <= 0;
    end else begin
      if (push && !full) wp <= wp + 1'b1;
      if (pop && !empty) rp <= rp + 1'b1;
    end
  end

  always @(posedge clk)
    if (!clear && push && !full) mem[wp[DEPTH_LOG2-1:0]] <= din;
endmodule
