// SPDX-FileCopyrightText: © 2026 Prajesh-47
// SPDX-License-Identifier: Apache-2.0
//
// Closed-loop proof of the open-drain bridge: splice_bridge, its 2-flop synchronizers and output
// registers exactly as in the top level, and both open-drain buses with pull-ups.
//
// The two devices (ext_a, ext_b) can do anything at all, on any cycle. Each bus falls at once when
// pulled low, and rises at any time up to RISE cycles after release, chosen freely by the prover.
//
// Proved for HOLDOFF large enough, and it must FAIL when HOLDOFF is too short (see run.sh):
//   P1  the bridge never drives both sides at once
//   P2  while the bridge is holding off it drives neither side
//   P3  no deadlock: once both devices let go, within BOUND cycles the bridge has released both
//       sides, it is idle, and both buses are high

`default_nettype none

module bridge_closed_loop #(
    parameter [7:0] HOLDOFF = 8'd20,
    parameter integer RISE = 10,
    parameter integer BOUND = 40
) (
    input wire clk,
    input wire rst,
    input wire ext_a,       // device on side A pulls low
    input wire ext_b,       // device on side B pulls low
    input wire early_a,     // the bus may finish rising early
    input wire early_b
);
  // Bus A and bus B
  reg bus_a, bus_b;
  reg [7:0] rem_a, rem_b;
  reg sa1, sa2, sb1, sb2;   // synchronizers
  reg drive_a, drive_b;     // output registers

  wire low_a_nx, low_b_nx;
  wire [1:0] bridge_state;
  wire [7:0] bridge_cnt;

  splice_bridge bridge (
      .clk(clk), .rst(rst), .enable(1'b1), .line_a(sa2), .line_b(sb2), .holdoff(HOLDOFF),
      .low_a_nx(low_a_nx), .low_b_nx(low_b_nx), .state_out(bridge_state), .cnt_out(bridge_cnt));

  wire pull_a = ext_a || drive_a;
  wire pull_b = ext_b || drive_b;

  // How long both devices have been quiet
  reg [7:0] quiet;

  always @(posedge clk) begin
    if (rst) begin
      bus_a <= 1'b1;
      bus_b <= 1'b1;
      rem_a <= 0;
      rem_b <= 0;
      sa1 <= 1'b1;
      sa2 <= 1'b1;
      sb1 <= 1'b1;
      sb2 <= 1'b1;
      drive_a <= 1'b0;
      drive_b <= 1'b0;
      quiet <= 0;
    end else begin
      if (pull_a) begin
        bus_a <= 1'b0;
        rem_a <= RISE;
      end else if (rem_a == 0 || early_a) begin
        bus_a <= 1'b1;
        rem_a <= 0;
      end else rem_a <= rem_a - 1'b1;

      if (pull_b) begin
        bus_b <= 1'b0;
        rem_b <= RISE;
      end else if (rem_b == 0 || early_b) begin
        bus_b <= 1'b1;
        rem_b <= 0;
      end else rem_b <= rem_b - 1'b1;

      sa1 <= bus_a;
      sa2 <= sa1;
      sb1 <= bus_b;
      sb2 <= sb1;
      drive_a <= low_a_nx;
      drive_b <= low_b_nx;

      if (ext_a || ext_b) quiet <= 0;
      else if (quiet != 8'hFF) quiet <= quiet + 1'b1;
    end
  end

  localparam [1:0] HOLDING = 2'd3, IDLE = 2'd0;

  always @(posedge clk) begin
    if (!rst) begin
      // Facts about reachable states, proved too. Induction needs them to rule out impossible starts.
      assert (rem_a <= RISE && rem_b <= RISE);
      assert (bridge_cnt <= HOLDOFF);
      assert (drive_a == (bridge_state == 2'd2));
      assert (drive_b == (bridge_state == 2'd1));
      assert (drive_a || bus_a || rem_a <= RISE);

      assert (!(drive_a && drive_b));                                   // P1
      assert (!(bridge_state == HOLDING && (low_a_nx || low_b_nx)));    // P2
      if (quiet >= BOUND) begin                                         // P3
        assert (!drive_a && !drive_b);
        assert (bridge_state == IDLE);
        assert (bus_a && bus_b);
      end
    end
  end
endmodule
