/*
 * SPLICE: a small register file in latches, for state kept per context
 * Copyright (c) 2026 Prajesh-47
 * SPDX-License-Identifier: Apache-2.0
 *
 * A latch is about 60% of a flop's area here, and a word written through a clock gate needs no
 * enable mux or reset gate per bit. Synthesis puts a flop word with its enable at well over twice
 * the area of a latch word.
 *
 * Timing is the same as a flop array written on the clock edge: the write data is registered at the
 * edge that ends the writing cycle, and the word's clock gate opens its latches for the high half of
 * the next cycle, so the new value is readable in that cycle, as a flop's would be. The registered
 * data also breaks the loop a latch read and written in the same cycle would make.
 */
`default_nettype none

module splice_latch_rf #(parameter WORDS = 4, parameter WIDTH = 16) (
    input  wire                     clk,
    input  wire [WORDS-1:0]         we,   // word k takes wd at the end of this cycle
    input  wire [WIDTH-1:0]         wd,
    output wire [WIDTH*WORDS-1:0]   q
);
  reg [WIDTH-1:0] wd_q;
  always @(posedge clk) wd_q <= wd;

`ifdef FUNCTIONAL
  // Simulation model. Assignments are non-blocking so logic clocked on the same edge still reads the
  // old value, as it does in silicon, where the latch opens after the edge.
  reg [WIDTH-1:0] mem [0:WORDS-1];
`endif

  genvar k;
  generate
    for (k = 0; k < WORDS; k = k + 1) begin : w
`ifdef FUNCTIONAL
      reg en;
      always @(clk or we[k]) if (!clk) en <= we[k];   // the clock gate's latch, open while clk is low
      wire g = clk && en;
      always @(g or wd_q) if (g) mem[k] <= wd_q;
      assign q[WIDTH * k +: WIDTH] = mem[k];
`else
      wire g;
      sg13cmos5l_lgcp_1 icg (.GCLK(g), .CLK(clk), .GATE(we[k]));
      genvar b;
      for (b = 0; b < WIDTH; b = b + 1) begin : bit_
        sg13cmos5l_dlhq_1 l (.Q(q[WIDTH * k + b]), .D(wd_q[b]), .GATE(g));
      end
`endif
    end
  endgenerate
endmodule
