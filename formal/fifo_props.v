// SPDX-FileCopyrightText: © 2026 Prajesh-47
// SPDX-License-Identifier: Apache-2.0
//
// FIFO safety for any sequence of push, pop and clear. A shadow counter and one tracked word show that
// the count stays in range, full and empty are exact, and a word comes out unchanged and in order.
`default_nettype none

module fifo_props (input wire clk, input wire rst, input wire clear, input wire push, input wire pop,
                   input wire [15:0] din, input wire pick);
  wire full, empty;
  wire [15:0] head;
  splice_fifo #(.DEPTH_LOG2(2)) dut (
      .clk(clk), .rst_n(!rst), .clear(clear), .push(push), .din(din), .full(full), .pop(pop),
      .head(head), .empty(empty), .wp_out(wp), .rp_out(rp), .peek(slot), .peek_word(stored));

  // Not the claim, but what makes it inductive: the shadow state agrees with the real pointers and memory
  wire [2:0] wp, rp;
  wire [1:0] slot = rp[1:0] + ahead[1:0];
  wire [15:0] stored;

  reg [2:0] count;
  reg       tracking;      // the tracked word is queued
  reg [2:0] ahead;         // words in front of it
  reg [15:0] word;

  wire do_push = push && !full;
  wire do_pop = pop && !empty;

  always @(posedge clk) begin
    if (rst || clear) begin
      count <= 0;
      tracking <= 0;
      ahead <= 0;
    end else begin
      count <= count + do_push - do_pop;
      if (!tracking && do_push && pick) begin
        tracking <= 1;
        word <= din;
        ahead <= count - do_pop;
      end else if (tracking && do_pop) begin
        if (ahead == 0) tracking <= 0;
        else ahead <= ahead - 1;
      end
    end
  end

  always @(*) if (!rst) begin
    assert (count <= 4);
    assert (((wp - rp) & 3'd7) == count);
    assert (full == (count == 4));
    assert (empty == (count == 0));
    if (empty) assert (head == 16'd0);
    if (tracking) begin
      assert (ahead < count);
      assert (stored == word);
      if (ahead == 0) assert (head == word);
    end
  end
endmodule
