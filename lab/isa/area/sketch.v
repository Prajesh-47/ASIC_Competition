// SPDX-FileCopyrightText: © 2026 Prajesh-47
// SPDX-License-Identifier: Apache-2.0
//
// THROWAWAY area sketch for comparing ISA features. Not the engine, not simulated, never in src/.
// Each `ifdef adds a feature, so synthesis can measure what that feature costs and how it affects
// the path from the macro's read data to the macro's next address.

`default_nettype none

module sketch (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [15:0] dout,        // instruction, from the program SRAM
    output wire [8:0]  next_pc,     // goes straight to the SRAM address pins
    input  wire [11:0] line_in,     // synchronized inputs
    output reg  [11:0] eng_out,
    output reg  [17:0] src_flat,    // 3 bits per line for six of the lines, as a stand-in
    output wire [15:0] push_data,
    output reg         push,
    input  wire [15:0] pull_data,
    input  wire        pull_empty,
    output reg         pull
);
  reg [8:0]  pc;
  reg [15:0] r0, r1, r2, r3, sr;
  reg [4:0]  bc;
  reg [15:0] to, stall;
  reg        z, f;
  reg [11:0] prev;
  assign push_data = sr
`ifdef F_TIMER
      ^ {2'd0, missed, tp}
`endif
`ifdef F_TSRC
      ^ {12'd0, ts_edge, 3'd0}
`endif
`ifdef F_CALL
      ^ {7'd0, lr}
`endif
`ifdef F_CODER
      ^ crc ^ {15'd0, st_p ^ arb_lost ^ level}
`endif
`ifdef F_MATCH
      ^ {8'd0, mt_sr[0]} ^ {mt_sr[1], 8'd0} ^ {15'd0, mt_hit[0] ^ mt_hit[1]}
`endif
      ;

  wire [3:0] opc = dout[15:12];
  wire [1:0] rn  = dout[11:10];
  wire [3:0] lf  = dout[11:8];
  wire       in_b = line_in[lf];
  wire [15:0] rval = rn == 0 ? r0 : rn == 1 ? r1 : rn == 2 ? r2 : r3;
  wire [15:0] alu = dout[9] ? rval + {8'd0, dout[7:0]} : rval ^ {8'd0, dout[7:0]};
  wire stalling = stall != 0;

`ifdef F_TIMER
  reg [12:0] tp, tcnt;
  reg        tick_p, missed;
  wire       tick = tcnt <= 1;
`endif
`ifdef F_TSRC
  // The tick can come from an input edge instead of the period timer.
  reg [3:0] ts_line;
  reg       ts_en, ts_want;
  wire      ts_edge = prev[ts_line] != ts_want && line_in[ts_line] == ts_want;
`endif
`ifdef F_TICKQ
  wire tick_q = dout[7];
  wire tick_block = tick_q && !tick_p;
`else
  wire tick_block = 1'b0;
`endif
`ifdef F_WAITCOND
  wire [1:0] wc = dout[9:8];
  wire [3:0] wl = dout[7:4];
  wire wait_met = wc == 0 ? !line_in[wl] : wc == 1 ? line_in[wl] :
                  wc == 2 ? (!prev[wl] && line_in[wl]) : (prev[wl] && !line_in[wl]);
`else
  wire wait_met = 1'b1;
`endif

  // Next-PC: the critical path. Read data -> decode -> condition -> mux -> SRAM address.
  wire [2:0] cc = dout[11:9];
  wire take = cc == 0 ? 1'b1 : cc == 1 ? z : cc == 2 ? !z : cc == 3 ? f : cc == 4 ? !f :
              cc == 5 ? bc == 0 : bc != 0;
  wire is_jmp = opc == 4'hA && take;
`ifdef F_FUSED
  wire [4:0] bc_next = bc - 5'd1;
  wire is_fused = (opc == 4'h6 || opc == 4'h7) && bc_next != 0;
  wire [8:0] fused_to = pc - {5'd0, dout[3:0]};
`else
  wire is_fused = 1'b0;
  wire [8:0] fused_to = pc;
`endif
  wire hold = stalling || tick_block || (opc == 4'h3 && !wait_met);
`ifdef F_CALL
  wire is_call = opc == 4'hC && dout[11];
  wire is_ret  = opc == 4'hC && dout[10];
  assign next_pc = hold ? pc : is_ret ? lr : (is_jmp || is_call) ? dout[8:0] : is_fused ? fused_to : pc + 9'd1;
`else
  assign next_pc = hold ? pc : is_jmp ? dout[8:0] : is_fused ? fused_to : pc + 9'd1;
`endif

`ifdef F_NS
  wire ns = dout[8];
`else
  wire ns = 1'b0;
`endif
  wire [11:0] line_mask = 12'd1 << lf[3:0] % 12;

`ifdef F_RAND
  reg [15:0] lfsr;
`endif
`ifdef F_SIDE
  reg [3:0] side_line;
  wire [1:0] side = dout[6:5];
`endif
`ifdef F_RULES
  // Two hardware rules, each with delay, width and holdoff counters and its own trigger.
  reg [15:0] ra_c [0:1];
  reg [15:0] ra_d [0:1];
  reg [15:0] ra_w [0:1];
  reg [15:0] ra_h [0:1];
  reg [2:0]  ra_t [0:1];
  reg [2:0]  ra_l [0:1];
  reg [2:0]  ra_s [0:1];
  reg [2:0]  ra_sv[0:1];
  reg [2:0]  ra_st[0:1];
  reg        ra_e [0:1];
  integer ri;
`endif
`ifdef F_CALL
  reg [8:0] lr;  // one-level link register, for CALL and RET
`endif
`ifdef F_CODER
  // Line coding on the output path: CRC, bit stuffing, NRZI, Manchester half-bit, arbitration.
  reg [15:0] crc, poly;
  reg [2:0]  stuff_n, run_len;
  reg [15:0] arb_at, arb_cnt;
  reg        crc_on, crc_msb, stuff_one, nrzi, level, manch, half_p, half_v, st_p, run_v, arb_lost;
  reg [3:0]  pair_line;
  wire       code_bit = sr[15];
  wire       crc_fb = code_bit ^ (crc_msb ? crc[15] : crc[0]);
  wire [15:0] crc_nx = (crc_msb ? {crc[14:0], 1'b0} : {1'b0, crc[15:1]}) ^ (crc_fb ? poly : 16'd0);
`endif
`ifdef F_MATCH
  // Per direction: a byte sampler with its own bit timer, plus pattern and mask comparison.
  reg [15:0] mt_period [0:1];
  reg [15:0] mt_cnt    [0:1];
  reg [7:0]  mt_sr     [0:1];
  reg [7:0]  mt_val    [0:1];
  reg [7:0]  mt_mask   [0:1];
  reg [3:0]  mt_n      [0:1];
  reg [2:0]  mt_line   [0:1];
  reg        mt_busy   [0:1];
  reg        mt_hit    [0:1];
  integer mi;
`endif
`ifdef F_CTX2
  // A second context: its own PC, registers, shift register, counters and flags.
  reg [8:0]  c2_pc;
  reg [15:0] c2_r0, c2_r1, c2_r2, c2_r3, c2_sr, c2_to, c2_stall;
  reg [4:0]  c2_bc;
  reg        c2_z, c2_f, sel;
`endif
`ifdef F_MODE2
  wire [2:0] m2l = dout[5:3];
  wire [2:0] m2s = dout[2:0];
`endif

  always @(posedge clk) begin
    if (!rst_n) begin
      pc <= 0; r0 <= 0; r1 <= 0; r2 <= 0; r3 <= 0; sr <= 0; bc <= 0; to <= 0; stall <= 0;
      z <= 0; f <= 0; prev <= 12'hFFF; eng_out <= 12'hFFF; src_flat <= 0; push <= 0; pull <= 0;
`ifdef F_TIMER
      tp <= 0; tcnt <= 0; tick_p <= 0; missed <= 0;
`endif
`ifdef F_TSRC
      ts_line <= 0; ts_want <= 0; ts_en <= 0;
`endif
`ifdef F_RAND
      lfsr <= 16'hACE1;
`endif
`ifdef F_SIDE
      side_line <= 0;
`endif
`ifdef F_RULES
      for (ri = 0; ri < 2; ri = ri + 1) begin
        ra_c[ri] <= 0; ra_d[ri] <= 0; ra_w[ri] <= 0; ra_h[ri] <= 0; ra_t[ri] <= 0;
        ra_l[ri] <= 0; ra_s[ri] <= 0; ra_sv[ri] <= 0; ra_st[ri] <= 0; ra_e[ri] <= 0;
      end
`endif
`ifdef F_CALL
      lr <= 0;
`endif
`ifdef F_CODER
      crc <= 0; poly <= 0; stuff_n <= 0; run_len <= 0; arb_at <= 0; arb_cnt <= 0; crc_on <= 0;
      crc_msb <= 0; stuff_one <= 0; nrzi <= 0; level <= 1; manch <= 0; half_p <= 0; half_v <= 0;
      st_p <= 0; run_v <= 0; arb_lost <= 0; pair_line <= 0;
`endif
`ifdef F_MATCH
      for (mi = 0; mi < 2; mi = mi + 1) begin
        mt_period[mi] <= 0; mt_cnt[mi] <= 0; mt_sr[mi] <= 0; mt_val[mi] <= 0; mt_mask[mi] <= 0;
        mt_n[mi] <= 0; mt_line[mi] <= 0; mt_busy[mi] <= 0; mt_hit[mi] <= 0;
      end
`endif
`ifdef F_CTX2
      c2_pc <= 0; c2_r0 <= 0; c2_r1 <= 0; c2_r2 <= 0; c2_r3 <= 0; c2_sr <= 0; c2_to <= 0;
      c2_stall <= 0; c2_bc <= 0; c2_z <= 0; c2_f <= 0; sel <= 0;
`endif
    end else begin
      prev <= line_in;
      push <= 0;
      pull <= 0;
      pc <= next_pc;
`ifdef F_CTX2
      // Swap the whole context each cycle, modelling the muxing of a 2-thread barrel.
      sel <= !sel;
      c2_pc <= pc; pc <= c2_pc;
      c2_r0 <= r0; r0 <= c2_r0; c2_r1 <= r1; r1 <= c2_r1; c2_r2 <= r2; r2 <= c2_r2;
      c2_r3 <= r3; r3 <= c2_r3; c2_sr <= sr; sr <= c2_sr; c2_to <= to; to <= c2_to;
      c2_stall <= stall; stall <= c2_stall; c2_bc <= bc; bc <= c2_bc; c2_z <= z; z <= c2_z;
      c2_f <= f; f <= c2_f;
`endif
`ifdef F_TIMER
`ifdef F_TSRC
      if (ts_en ? ts_edge : tick) begin
`else
      if (tick) begin
`endif
        tcnt <= tp;
        if (tick_p) missed <= 1;
        tick_p <= 1;
      end else tcnt <= tcnt - 13'd1;
`ifdef F_TSRC
      if (opc == 4'hE) begin
        ts_line <= dout[3:0];
        ts_want <= dout[4];
        ts_en <= dout[5];
      end
`endif
      if (opc[3:1] == 3'b111) tp <= dout[12:0];
`endif
      if (stalling) stall <= stall - 16'd1;
      else if (!tick_block) begin
`ifdef F_TICKQ
        if (tick_q) tick_p <= 0;
`endif
`ifdef F_SIDE
        if (opc == 4'hF) side_line <= dout[3:0];
        if (side[1]) eng_out[side_line % 12] <= side[0];
`endif
        case (opc)
          4'h1: begin
            eng_out[lf % 12] <= dout[7];
            stall <= {9'd0, dout[6:0]};
          end
          4'h2: begin
            src_flat[3 * dout[11:9] +: 3] <= dout[8:6];
`ifdef F_MODE2
            src_flat[3 * m2l +: 3] <= m2s;
`endif
            stall <= {10'd0, dout[5:0]};
          end
          4'h4: stall <= {4'd0, dout[11:0]};
          4'h5: stall <= rval;
          4'h6: begin
            sr <= {sr[14:0], in_b};
            bc <= bc - 5'd1;
          end
          4'h7: begin
            eng_out[lf % 12] <= sr[15];
            if (!ns) begin
              sr <= sr << 1;
              bc <= bc - 5'd1;
            end
          end
          4'h8: begin
            case (rn)
              0: r0 <= {8'd0, dout[7:0]};
              1: r1 <= {8'd0, dout[7:0]};
              2: r2 <= {8'd0, dout[7:0]};
              3: r3 <= {8'd0, dout[7:0]};
            endcase
          end
          4'h9: begin
            z <= alu == 0;
            case (rn)
              0: r0 <= alu;
              1: r1 <= alu;
              2: r2 <= alu;
              3: r3 <= alu;
            endcase
          end
          4'hB: begin
            if (dout[8]) begin
              push <= 1;
            end else if (pull_empty) f <= 1;
            else begin
              sr <= pull_data;
              pull <= 1;
              f <= 0;
            end
          end
`ifdef F_RAND
          4'hC: begin
            lfsr <= {1'b0, lfsr[15:1]} ^ (lfsr[0] ? 16'hB400 : 16'h0);
            r0 <= lfsr;
          end
`endif
`ifdef F_RULES
          4'hD: begin
            ra_d[dout[11]] <= r0;
            ra_w[dout[11]] <= r1;
            ra_h[dout[11]] <= r2;
            ra_t[dout[11]] <= dout[10:8];
            ra_l[dout[11]] <= dout[5:3];
            ra_s[dout[11]] <= dout[2:0];
            ra_st[dout[11]] <= 3'd1;
            ra_e[dout[11]] <= dout[7];
          end
`endif
          default: ;
        endcase
      end
`ifdef F_CALL
      if (opc == 4'hC && dout[11]) lr <= pc + 9'd1;
`endif
`ifdef F_CODER
      if (opc == 4'h3 && dout[3:0] == 4'hF) begin  // load configuration from the registers
        poly <= r1;
        crc <= r0;
        arb_at <= r2;
        {crc_on, crc_msb, stuff_one, nrzi, manch} <= dout[8:4];
        stuff_n <= dout[11:9];
        pair_line <= r3[3:0];
      end
      if (opc == 4'h7 && !stalling) begin  // each output bit passes through the coder
        if (crc_on) crc <= crc_nx;
        if (manch) begin
          half_p <= !half_p;
          half_v <= code_bit;
        end
        if (nrzi && !code_bit) level <= !level;
        if (st_p) st_p <= 0;
        else if (run_v == code_bit || stuff_one) begin
          run_len <= run_len + 3'd1;
          if (run_len == stuff_n - 3'd1) st_p <= 1;
        end else run_len <= 3'd1;
        run_v <= code_bit;
        arb_cnt <= arb_at;
        eng_out[pair_line % 12] <= !(nrzi ? level : (half_p ? !half_v : code_bit));
      end else if (arb_cnt != 0) begin
        arb_cnt <= arb_cnt - 16'd1;
        if (arb_cnt == 1 && run_v && !line_in[lf]) arb_lost <= 1;
      end
`endif
`ifdef F_MATCH
      if (opc == 4'hD && dout[0]) begin
        mt_period[dout[11]] <= r0;
        mt_val[dout[11]] <= r1[7:0];
        mt_mask[dout[11]] <= r1[15:8];
        mt_line[dout[11]] <= dout[10:8];
      end
      for (mi = 0; mi < 2; mi = mi + 1) begin
        if (!mt_busy[mi]) begin
          if (prev[mt_line[mi]] && !line_in[mt_line[mi]]) begin
            mt_busy[mi] <= 1;
            mt_n[mi] <= 0;
            mt_cnt[mi] <= mt_period[mi] + (mt_period[mi] >> 1);
          end
        end else if (mt_cnt[mi] <= 1) begin
          mt_cnt[mi] <= mt_period[mi];
          mt_sr[mi] <= {line_in[mt_line[mi]], mt_sr[mi][7:1]};
          mt_n[mi] <= mt_n[mi] + 4'd1;
          if (mt_n[mi] == 4'd8) begin
            mt_busy[mi] <= 0;
            mt_hit[mi] <= ((mt_sr[mi] ^ mt_val[mi]) & mt_mask[mi]) == 8'd0;
          end
        end else mt_cnt[mi] <= mt_cnt[mi] - 16'd1;
      end
`endif
`ifdef F_RULES
      for (ri = 0; ri < 2; ri = ri + 1) begin
        case (ra_st[ri])
          1: if (prev[ra_t[ri]] != ra_e[ri] && line_in[ra_t[ri]] == ra_e[ri]) begin
               ra_c[ri] <= ra_d[ri];
               ra_st[ri] <= 3'd2;
             end
          2: if (ra_c[ri] == 0) begin
               ra_sv[ri] <= src_flat[3 * ra_l[ri] +: 3];
               src_flat[3 * ra_l[ri] +: 3] <= ra_s[ri];
               ra_c[ri] <= ra_w[ri];
               ra_st[ri] <= 3'd3;
             end else ra_c[ri] <= ra_c[ri] - 16'd1;
          3: if (ra_c[ri] <= 1) begin
               src_flat[3 * ra_l[ri] +: 3] <= ra_sv[ri];
               ra_c[ri] <= ra_h[ri];
               ra_st[ri] <= 3'd4;
             end else ra_c[ri] <= ra_c[ri] - 16'd1;
          4: if (ra_c[ri] <= 1) ra_st[ri] <= 3'd1;
             else ra_c[ri] <= ra_c[ri] - 16'd1;
          default: ;
        endcase
      end
`endif
    end
  end
endmodule
