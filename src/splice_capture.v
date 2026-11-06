/*
 * SPLICE: capture unit (docs/architecture.md section 6)
 * Copyright (c) 2026 Prajesh-47
 * SPDX-License-Identifier: Apache-2.0
 *
 * Watches 4 lines and writes one 16-bit entry for each change: {line states[3:0], dt[11:0]}. dt counts
 * prescaler ticks since the previous entry. Reserved dt values:
 *   0xFFF  nothing changed for 0xFFD ticks; keep counting
 *   0xFFE  a mark from the engine, taking no time
 * The first entry of a run records the starting state, with dt = 0.
 *
 * mode[0] ring: wrap and keep the newest entries. A trigger then stops capture `post` entries later,
 *         which is what gives pre-trigger history. Without ring, capture stops when memory is full.
 * mode[1] TRIG_IN's rising edge starts capture.
 * mode[2] in ring mode, a mark from the engine counts as the trigger as well as TRIG_IN.
 */
`default_nettype none

module splice_capture (
    input  wire        clk,
    input  wire        rst,        // reset, or the start of a run
    input  wire [11:0] lines,
    input  wire        trig_rise,
    input  wire [15:0] watch,      // 4 line numbers
    input  wire [15:0] prescale,   // a tick every prescale + 1 cycles
    input  wire [2:0]  mode,
    input  wire [8:0]  post,
    input  wire        on,         // from the engine or the host
    input  wire        off,
    input  wire        mark,

    output reg         we,
    output reg  [8:0]  waddr,
    output reg  [15:0] wdata,
    output reg         active,
    output reg         wrapped,
    output reg         triggered,
    output reg  [8:0]  ptr
);
  localparam [11:0] DT_REPEAT = 12'hFFF, DT_MARK = 12'hFFE, DT_LIMIT = 12'hFFD;

  wire [15:0] lines_x = {4'hF, lines};
  wire [3:0]  states = {lines_x[watch[15:12]], lines_x[watch[11:8]], lines_x[watch[7:4]], lines_x[watch[3:0]]};

  reg  [3:0]  last;
  reg  [11:0] dt;
  reg  [15:0] pre;
  reg         first;
  reg  [8:0]  post_left;

  wire tick = pre == prescale;
  wire change = states != last;
  wire repeat_due = dt == DT_LIMIT;
  wire do_write = active && !off && (first || change || mark || repeat_due);
  wire [11:0] entry_dt = first ? 12'd0 : change ? dt : mark ? DT_MARK : DT_REPEAT;
  wire trigger = active && mode[0] && !triggered && (trig_rise || (mode[2] && mark));

  // A mark taking no time leaves dt running, unless the same cycle also records a change.
  wire clears_dt = first || change || (repeat_due && !mark);
  // After a trigger, keep `post` more entries. The trigger's own cycle uses `post` directly, because
  // post_left hasn't been loaded yet.
  wire [8:0] remaining = triggered ? post_left : post;

  always @(posedge clk) begin
    we <= 1'b0;
    if (rst) begin
      active <= 1'b0;
      wrapped <= 1'b0;
      triggered <= 1'b0;
      ptr <= 9'd0;
      last <= 4'd0;
      dt <= 12'd0;
      pre <= 16'd0;
      first <= 1'b0;
      post_left <= 9'd0;
      waddr <= 9'd0;
      wdata <= 16'd0;
    end else begin
      if (active) begin
        pre <= tick ? 16'd0 : pre + 16'd1;  // starts with capture, so ticks line up with its first entry
        dt <= (clears_dt ? 12'd0 : dt) + {11'd0, tick};
        if (trigger) begin
          triggered <= 1'b1;
          post_left <= post;
        end
        if (do_write) begin
          we <= 1'b1;
          waddr <= ptr;
          wdata <= {states, entry_dt};
          last <= states;
          first <= 1'b0;
          ptr <= ptr + 9'd1;
          if (ptr == 9'd511) wrapped <= 1'b1;
          if (ptr == 9'd511 && !mode[0]) active <= 1'b0;  // full
          if (triggered || trigger) begin
            if (remaining == 0) active <= 1'b0;
            else post_left <= remaining - 9'd1;
          end
        end
        if (off) active <= 1'b0;
      end else if (on || (mode[1] && trig_rise)) begin
        active <= 1'b1;
        first <= 1'b1;
        dt <= 12'd0;
        pre <= 16'd0;
      end
    end
  end
endmodule
