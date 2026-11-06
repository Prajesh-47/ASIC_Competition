// SPDX-FileCopyrightText: © 2026 Prajesh-47
// SPDX-License-Identifier: Apache-2.0
//
// THROWAWAY sketch: the storage and matching a small Tomasulo core adds, for an area floor. It has
// N reservation stations, a register alias table for 4 registers, and a reorder buffer, because pin
// writes have to commit in order. The functional units and fetch are not included, so the real
// cost is higher than this. Not simulated, and never in src/.

`default_nettype none

module tomasulo #(parameter RS = 4, ROB = 8, W = 16, TAG = 4) (
    input  wire                clk,
    input  wire                rst_n,
    input  wire                issue,
    input  wire [1:0]          iss_rs,
    input  wire [3:0]          iss_op,
    input  wire [W-1:0]        iss_a,
    input  wire [W-1:0]        iss_b,
    input  wire [TAG-1:0]      iss_ta,
    input  wire [TAG-1:0]      iss_tb,
    input  wire                cdb_v,
    input  wire [TAG-1:0]      cdb_tag,
    input  wire [W-1:0]        cdb_val,
    output wire [RS-1:0]       ready,
    output reg  [W-1:0]        commit_val,
    output reg                 commit_v,
    input  wire [1:0]          rat_q,
    output wire [TAG:0]        rat_out
);
  reg [3:0]     op  [0:RS-1];
  reg [W-1:0]   va  [0:RS-1];
  reg [W-1:0]   vb  [0:RS-1];
  reg [TAG-1:0] qa  [0:RS-1];
  reg [TAG-1:0] qb  [0:RS-1];
  reg           ra  [0:RS-1];
  reg           rb  [0:RS-1];
  reg           busy[0:RS-1];
  reg [W-1:0]   rob_v [0:ROB-1];
  reg           rob_d [0:ROB-1];
  reg [$clog2(ROB)-1:0] head, tail;
  reg [TAG:0]   rat [0:3];
  integer i;
  genvar g;
  generate for (g = 0; g < RS; g = g + 1) assign ready[g] = busy[g] && ra[g] && rb[g]; endgenerate
  assign rat_out = rat[rat_q];

  always @(posedge clk) begin
    commit_v <= 0;
    if (!rst_n) begin
      head <= 0;
      tail <= 0;
      for (i = 0; i < RS; i = i + 1) begin
        busy[i] <= 0; ra[i] <= 0; rb[i] <= 0; op[i] <= 0; va[i] <= 0; vb[i] <= 0; qa[i] <= 0; qb[i] <= 0;
      end
      for (i = 0; i < ROB; i = i + 1) begin
        rob_v[i] <= 0;
        rob_d[i] <= 0;
      end
      for (i = 0; i < 4; i = i + 1) rat[i] <= 0;
      commit_val <= 0;
    end else begin
      if (issue) begin
        busy[iss_rs] <= 1; op[iss_rs] <= iss_op; va[iss_rs] <= iss_a; vb[iss_rs] <= iss_b;
        qa[iss_rs] <= iss_ta; qb[iss_rs] <= iss_tb; ra[iss_rs] <= iss_ta == 0; rb[iss_rs] <= iss_tb == 0;
        rat[iss_op[1:0]] <= {1'b1, iss_ta};
        rob_d[tail] <= 0;
        tail <= tail + 1'b1;
      end
      if (cdb_v) begin
        for (i = 0; i < RS; i = i + 1) begin
          if (qa[i] == cdb_tag) begin
            va[i] <= cdb_val;
            ra[i] <= 1;
          end
          if (qb[i] == cdb_tag) begin
            vb[i] <= cdb_val;
            rb[i] <= 1;
          end
        end
        for (i = 0; i < 4; i = i + 1) if (rat[i] == {1'b1, cdb_tag}) rat[i] <= 0;
        rob_v[cdb_tag[$clog2(ROB)-1:0]] <= cdb_val;
        rob_d[cdb_tag[$clog2(ROB)-1:0]] <= 1;
      end
      if (rob_d[head]) begin
        commit_val <= rob_v[head];
        commit_v <= 1;
        head <= head + 1'b1;
      end
    end
  end
endmodule
