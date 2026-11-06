/*
 * SPLICE: open-drain bridge for one line pair (docs/architecture.md section 4.1)
 * Copyright (c) 2026 Prajesh-47
 * SPDX-License-Identifier: Apache-2.0
 *
 * Either side may pull low, and the other side copies it. When the side that started it lets go, the
 * bridge stops driving and then ignores both lines for `holdoff` cycles. Without that, it would read
 * its own slowly rising output as a new low and latch up. If both sides go low together, A wins.
 *
 * The outputs are next-state values, so the fabric can register them with no added delay.
 */
`default_nettype none

module splice_bridge (
    input  wire       clk,
    input  wire       rst,
    input  wire       enable,
    input  wire       line_a,    // synchronized
    input  wire       line_b,
    input  wire [7:0] holdoff,
    output wire       low_a_nx,  // drive side A low after this edge
    output wire       low_b_nx
`ifdef FORMAL
    ,
    output wire [1:0] state_out,
    output wire [7:0] cnt_out
`endif
);
  localparam [1:0] IDLE = 2'd0, A_HOLDS = 2'd1, B_HOLDS = 2'd2, HOLDOFF = 2'd3;

  reg [1:0] state;
  reg [7:0] cnt;
  reg [1:0] state_nx;
  reg [7:0] cnt_nx;

  always @* begin
    state_nx = state;
    cnt_nx = cnt;
    if (!enable) begin
      state_nx = IDLE;
    end else begin
      case (state)
        IDLE: begin
          if (!line_a) state_nx = A_HOLDS;
          else if (!line_b) state_nx = B_HOLDS;
        end
        A_HOLDS: if (line_a) begin
          state_nx = HOLDOFF;
          cnt_nx = holdoff;
        end
        B_HOLDS: if (line_b) begin
          state_nx = HOLDOFF;
          cnt_nx = holdoff;
        end
        default: begin
          if (cnt <= 8'd1) state_nx = IDLE;
          else cnt_nx = cnt - 8'd1;
        end
      endcase
    end
  end

`ifdef FORMAL
  assign state_out = state;
  assign cnt_out = cnt;
`endif
  assign low_a_nx = state_nx == B_HOLDS;
  assign low_b_nx = state_nx == A_HOLDS;

  always @(posedge clk) begin
    if (rst) begin
      state <= IDLE;
      cnt <= 8'd0;
    end else begin
      state <= state_nx;
      cnt <= cnt_nx;
    end
  end
endmodule
