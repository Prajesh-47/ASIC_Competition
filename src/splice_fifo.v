/*
 * SPLICE: small FIFO with the head word always visible
 * Copyright (c) 2026 Prajesh-47
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module splice_fifo #(parameter DEPTH_LOG2 = 2, parameter LATCH = 0) (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        clear,
    input  wire        push,
    input  wire [15:0] din,
    output wire        full,
    input  wire        pop,
    output wire [15:0] head,
    output wire        empty,
    output wire [7:0]  level     // words held, saturating at 255
`ifdef FORMAL
    ,
    output wire [DEPTH_LOG2:0]   wp_out,
    output wire [DEPTH_LOG2:0]   rp_out,
    input  wire [DEPTH_LOG2-1:0] peek,
    output wire [15:0]           peek_word
`endif
);
  localparam DEPTH = 1 << DEPTH_LOG2;
  reg [DEPTH_LOG2:0] wp, rp;
  wire wr = !clear && push && !full;

`ifdef FORMAL
  assign wp_out = wp;
  assign rp_out = rp;
`endif

  assign empty = wp == rp;
  wire [DEPTH_LOG2:0] count = wp - rp;
  wire [15:0] count16 = {{(15 - DEPTH_LOG2){1'b0}}, count};
  assign level = count16 > 16'd255 ? 8'd255 : count16[7:0];
  assign full  = wp[DEPTH_LOG2] != rp[DEPTH_LOG2] && wp[DEPTH_LOG2-1:0] == rp[DEPTH_LOG2-1:0];
  // An empty FIFO reads as 0, not as whatever an unwritten or old slot holds.
  wire [15:0] slot;
  assign head  = empty ? 16'd0 : slot;

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

  // Storage: flops, or with LATCH the latch file of splice_latch_rf.v, which reads the same way: a
  // word pushed is at the head from the next cycle.
  generate
    if (LATCH) begin : lat
      wire [DEPTH-1:0] we;
      wire [16*DEPTH-1:0] q;
      genvar k;
      for (k = 0; k < DEPTH; k = k + 1) begin : w
        assign we[k] = wr && wp[DEPTH_LOG2-1:0] == k;
      end
      splice_latch_rf #(.WORDS(DEPTH), .WIDTH(16)) rf (.clk(clk), .we(we), .wd(din), .q(q));
      assign slot = q[16 * rp[DEPTH_LOG2-1:0] +: 16];
    end else begin : ff
      reg [15:0] mem [0:DEPTH-1];
      always @(posedge clk)
        if (wr) mem[wp[DEPTH_LOG2-1:0]] <= din;
      assign slot = mem[rp[DEPTH_LOG2-1:0]];
`ifdef FORMAL
      assign peek_word = mem[peek];
`endif
    end
  endgenerate
endmodule
