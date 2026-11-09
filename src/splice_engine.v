/*
 * SPLICE: timing engine, ISA v1 (docs/architecture.md section 5)
 * Copyright (c) 2026 Prajesh-47
 * SPDX-License-Identifier: Apache-2.0
 *
 * Up to four contexts share one program memory, in a fixed round robin: with n contexts running,
 * each gets every nth cycle, so its timing is still exact.
 * Only explicit waits stall. Everything else takes 1 cycle plus its delay field.
 */
`default_nettype none

module splice_engine #(parameter NCTX = 4, parameter NFULL = 2) (
    input  wire        clk,
    input  wire        rst,        // synchronous: reset, or the host starting or stopping a run
    input  wire        start,
    input  wire [1:0]  nctx_m1,    // contexts in the round robin, less one
    input  wire [26:0] ctx_pc,     // start addresses of contexts 1-3, 9 bits each from bit 0
    output wire [1:0]  cur_ctx,    // the context whose slot this cycle is, for FIFO routing

    // Program memory. Read data arrives the cycle after the address.
    input  wire [15:0] mem_data,
    output wire [8:0]  fetch_addr,

    // Synchronized input lines, and the same lines one cycle earlier
    input  wire [11:0] lines_in,
    input  wire [11:0] lines_prev,

    // Line state after this clock edge, so the fabric can register it with no extra delay
    output reg  [11:0] eng_nx,
    output reg  [23:0] src_nx,

    // Host FIFOs
    input  wire [15:0] h2e_head,
    input  wire        h2e_empty,
    output wire        h2e_pop,
    output wire [15:0] e2h_data,
    input  wire        e2h_full,
    output wire        e2h_push,

    output reg         running,
    output reg  [NCTX-1:0] halted,
    output reg         irq,
    output reg         trig,
    output wire        cap_on,
    output wire        cap_off,
    output wire        cap_mark
);

  // ------------------------------------------------------------------ State, per context
  reg [8:0]  pc      [0:NCTX-1];
  reg        to_act  [0:NFULL-1];
  reg        z       [0:NCTX-1];
  reg        f       [0:NCTX-1];
  reg        m       [0:NCTX-1];   // a tick fired while the last was still pending, until JMP M reads it
  reg        msb     [0:NCTX-1];
  reg [3:0]  side_ln [0:NCTX-1];
  reg [16:0] tcnt    [0:NCTX-1];   // one more, so TPHASE can start one and a half periods
  reg [15:0] since   [0:NFULL-1];   // cycles since the last tick, saturating
  reg [15:0] tint    [0:NFULL-1];   // cycles between the last two ticks, for TCAP
  reg        pending [0:NCTX-1];
  reg [3:0]  ts_line [0:NCTX-1];
  reg [1:0]  ts_want [0:NCTX-1];
  reg        lastb   [0:NFULL-1];   // the bit last shifted by OUT, OUTB, IN or INB, which CRC steps on

  reg        prim;            // first cycle of a run: only fetches

  // State written only in a context's own slot lives in latches (splice_latch_rf.v), one word per
  // context, written at the end of the cycle as a flop would be:
  //   lr, sr, bc, stall, tp (timer period: 16 bits from TSETR, TSET's constant fills the low 13),
  //   last (what this context saw at its previous slot, for edge waits), and for full contexts only
  //   to, to_left, lfsr and poly (the CRC polynomial, set by POLY).
  wire [9*NCTX-1:0]   lr_q;
  wire [16*NCTX-1:0]  sr_q, stall_q, tp_q;
  wire [5*NCTX-1:0]   bc_q;
  wire [12*NCTX-1:0]  last_q;
  wire [16*NFULL-1:0] to_q, to_left_q, lfsr_q, poly_q;
  localparam CW = NCTX > 2 ? 2 : 1;
  reg [CW-1:0] cur;
  /* verilator lint_off WIDTHTRUNC */
  localparam [CW-1:0] LAST = NCTX - 1;
  /* verilator lint_on WIDTHTRUNC */
  wire [CW-1:0] cur_nx = (cur == nctx_m1[CW-1:0] || cur == LAST) ? {CW{1'b0}} : cur + 1'b1;
  assign cur_ctx = cur;
  reg [11:0] eng;
  reg [23:0] src;

  wire [CW-1:0] c = cur;
  // Contexts 0 to NFULL-1 are full. The rest are light: they have no timeout, LFSR, CRC polynomial or
  // TCAP state of their own, so TO, the LFSR and the polynomial read as 0 and TCAP as 0xFFFF there, and
  // writes to them do nothing. NFULL is 1, 2 or 4, so the full contexts are indexed by c's low bits.
  localparam FW = NFULL > 2 ? 2 : 1;
  wire          full_c = NFULL >= NCTX || c < NFULL;
  wire [FW-1:0] cf = c[FW-1:0];

  // Only decode memory output while executing. It's undefined before the first read, and this keeps
  // that from reaching any logic, on silicon as well as in gate-level simulation.
  wire [15:0] ins = (running && !prim) ? mem_data : 16'd0;

  // Lines 12-15 don't exist: they read as 1 and ignore writes.
  wire [15:0] lines = {4'hF, lines_in};
  wire [15:0] plines = {4'hF, lines_prev};

  // ------------------------------------------------------------------ Tick sources, every cycle
  wire [NCTX-1:0] tick_now;
  genvar g;
  generate
    for (g = 0; g < NCTX; g = g + 1) begin : ticks
      wire edge_seen = lines[ts_line[g]] != plines[ts_line[g]];
      assign tick_now[g] = (ts_want[g] != 2'd3)
          ? edge_seen && (ts_want[g] == 2'd2 || lines[ts_line[g]] == ts_want[g][0])
          : (tp_q[16 * g +: 16] != 0 && tcnt[g] <= 17'd1);
    end
  endgenerate

  // ------------------------------------------------------------------ Decode
  // Straight from the opcode bits (docs/architecture.md section 5.5). Read data to a flop is the longest
  // path in the chip, so there's no encoded opcode in between. Each is exact, so reserved encodings match
  // none of them and do nothing.
  wire is_tset   = ins[15:13] == 3'b000;
  wire is_jmp    = ins[15:12] == 4'b0010;
  wire is_call   = ins[15:12] == 4'b0011;
  wire is_dly    = ins[15:12] == 4'b0100;
  wire is_ldi    = ins[15:12] == 4'b0101;
  wire is_sri    = ins[15:12] == 4'b0110;
  wire is_set    = ins[15:11] == 5'b01110;
  wire is_mode   = ins[15:11] == 5'b01111;
  wire is_out    = ins[15:11] == 5'b10000;
  wire is_in     = ins[15:11] == 5'b10001;
  wire is_outb   = ins[15:11] == 5'b10010;
  wire is_inb    = ins[15:11] == 5'b10011;
  wire is_djnz   = ins[15:11] == 5'b10100;
  wire is_wait   = ins[15:10] == 6'b101110;
  wire is_add    = ins[15:10] == 6'b101111;
  wire is_cmpi   = ins[15:10] == 6'b110000;
  wire is_and    = ins[15:8] == 8'b11001101;
  wire is_bci    = ins[15:8] == 8'b11001110;
  wire is_capmark = ins[15:8] == 8'b11001111;
  wire is_capoff = ins[15:8] == 8'b11010000;
  wire is_capon  = ins[15:8] == 8'b11010001;
  wire is_cmp    = ins[15:8] == 8'b11010010;
  wire is_dlyr   = ins[15:8] == 8'b11010011;
  wire is_halt   = ins[15:8] == 8'b11010100;
  wire is_irq    = ins[15:8] == 8'b11010101;
  wire is_mov    = ins[15:8] == 8'b11010110;
  wire is_msb    = ins[15:8] == 8'b11010111;
  wire is_nop    = ins[15:8] == 8'b11011000;
  wire is_probe  = ins[15:8] == 8'b11011001;
  wire is_pull   = ins[15:8] == 8'b11011010;
  wire is_pullw  = ins[15:8] == 8'b11011011;
  wire is_push   = ins[15:8] == 8'b11011100;
  wire is_ret    = ins[15:8] == 8'b11011101;
  wire is_side   = ins[15:8] == 8'b11011110;
  wire is_tphase = ins[15:8] == 8'b11011111;
  wire is_trig   = ins[15:8] == 8'b11100000;
  wire is_tsrc   = ins[15:8] == 8'b11100001;
  wire is_tstb   = ins[15:8] == 8'b11100010;
  wire is_waitt  = ins[15:8] == 8'b11100011;
  wire is_xor    = ins[15:8] == 8'b11100100;
  wire is_addr   = ins[15:8] == 8'b11100110;
  wire is_tsetr  = ins[15:8] == 8'b11100111;
  wire is_poly   = ins[15:8] == 8'b11101000;
  wire is_crc    = ins[15:8] == 8'b11101001;
  wire is_cmpu   = ins[15:8] == 8'b11101010;
  wire is_tcap   = ins[15:8] == 8'b11101011;
  wire is_rand   = ins[15:6] == 10'b1110010100;
  wire is_shl    = ins[15:6] == 10'b1110010101;
  wire is_shr    = ins[15:6] == 10'b1110010110;

  wire is_bit = is_outb || is_inb;
  wire tick_q = ((is_set || is_out) && ins[5]) || (is_mode && ins[4]) || ((is_in || is_bit) && ins[6])
             || (is_nop && ins[7]) || (is_probe && ins[3]) || (is_tcap && ins[5]);
  wire [1:0] side = ({2{is_set || is_out}} & ins[4:3]) | ({2{is_in || is_bit}} & ins[5:4])
                  | ({2{is_nop}} & ins[6:5]);
  wire [3:0] dly_field = ({4{is_set || is_out}} & {1'b0, ins[2:0]}) | ({4{is_mode || is_in}} & ins[3:0]);

  // ------------------------------------------------------------------ Operands
  wire [3:0]  ln_a = ins[10:7];              // SET, OUT, IN, OUTB, INB
  wire [3:0]  ln_w = ins[9:6];               // WAIT
  wire [3:0]  ln_p = ins[7:4];               // PROBE, SIDE, TSRC

  wire [1:0]  rn_djnz = ins[10:9];
  wire [1:0]  rn_ldi  = ins[11:10];
  wire [1:0]  rn_add  = ins[9:8];            // ADD, CMPI
  wire [1:0]  rn_alu  = ins[7:6];            // AND, XOR, DLYR
  wire [1:0]  rn_sft  = ins[5:4];            // RAND, SHL, SHR

  // Register file read ports for the current context. These are plain wires on purpose: a function
  // that reads state behind its arguments isn't re-evaluated in a continuous assign, so simulation
  // would use stale values while synthesis wouldn't.
  // R0-R3 of every context live in latches (splice_latch_rf.v), written at the end of the cycle as a
  // flop would be. rr_cx is the current context's four.
  wire [64*NCTX-1:0] rr_q;
  wire [63:0] rr_cx = rr_q[64 * c +: 64];
  wire [15:0] r0_c = rr_cx[15:0];
  wire [15:0] r1_c = rr_cx[31:16];
  wire [15:0] r2_c = rr_cx[47:32];
  wire [15:0] r3_c = rr_cx[63:48];
  wire [15:0] sr_c = sr_q[16 * c +: 16];
  wire [4:0]  bc_c = bc_q[5 * c +: 5];
  wire [15:0] to_c = full_c ? to_q[16 * cf +: 16] : 16'd0;
  wire [15:0] stall_c = stall_q[16 * c +: 16];
  wire [11:0] last_c = last_q[12 * c +: 12];
  wire [15:0] tp_c = tp_q[16 * c +: 16];

  function automatic [15:0] operand(input [2:0] code, input [15:0] r0, input [15:0] r1, input [15:0] r2,
                                    input [15:0] r3, input [15:0] srv, input [4:0] bcv, input [15:0] tov);
    case (code)
      3'd0:    operand = r0;
      3'd1:    operand = r1;
      3'd2:    operand = r2;
      3'd3:    operand = r3;
      3'd4:    operand = srv;
      3'd5:    operand = {11'd0, bcv};
      3'd6:    operand = tov;
      default: operand = {8'd0, srv[15:8]};
    endcase
  endfunction

  wire [15:0] opnd_alu = operand(ins[5:3], r0_c, r1_c, r2_c, r3_c, sr_c, bc_c, to_c);  // AND, XOR, ADDR
  wire [15:0] opnd_mov = operand(ins[4:2], r0_c, r1_c, r2_c, r3_c, sr_c, bc_c, to_c);  // MOV, CMP b
  wire [15:0] opnd_cmp = operand(ins[7:5], r0_c, r1_c, r2_c, r3_c, sr_c, bc_c, to_c);  // CMP a, TSETR, POLY

  wire        sbit = msb[c] ? sr_c[15] : sr_c[0];
  wire [15:0] sr_shifted_out = msb[c] ? {sr_c[14:0], 1'b0} : {1'b0, sr_c[15:1]};
  wire        in_bit = lines[ln_a];
  wire [15:0] sr_shifted_in = msb[c] ? {sr_c[14:0], in_bit} : {in_bit, sr_c[15:1]};
  wire [4:0]  bc_dec = (bc_c == 0) ? 5'd0 : bc_c - 5'd1;

  // ------------------------------------------------------------------ Execute conditions
  wire slot = running && !prim;
  wire gated = slot && !halted[c] && stall_c == 0;
  wire pend_eff = pending[c] || tick_now[c];
  wire active = gated && (!tick_q || pend_eff);   // reached: a side-set applies

  // WAIT
  wire wait_bit = lines[ln_w];
  wire wait_was = ln_w < 4'd12 ? last_c[ln_w[3:0]] : 1'b1;
  reg  wait_met;
  always @* begin
    case (ins[5:4])
      2'd0: wait_met = !wait_bit;
      2'd1: wait_met = wait_bit;
      2'd2: wait_met = !wait_was && wait_bit;
      default: wait_met = wait_was && !wait_bit;
    endcase
  end
  wire [15:0] to_cur = (full_c && to_act[cf]) ? to_left_q[16 * cf +: 16] : to_c;
  wire [15:0] to_dec = to_cur - 16'd1;
  wire        to_on = to_c != 0;
  wire        to_expired = to_on && to_dec == 0;
  wire        wait_block = is_wait && !wait_met && !to_expired;

  wire blocked = wait_block
              || (is_waitt && !pend_eff)
              || (is_pullw && h2e_empty);
  wire ex = active && !blocked;

  // Next PC. The critical path: read data to the program memory address.
  reg take_jmp;
  always @* begin
    case (ins[11:9])
      3'd0: take_jmp = 1'b1;
      3'd1: take_jmp = z[c];
      3'd2: take_jmp = !z[c];
      3'd3: take_jmp = f[c];
      3'd4: take_jmp = !f[c];
      3'd5: take_jmp = bc_c == 0;
      3'd6: take_jmp = bc_c != 0;
      default: take_jmp = m[c];
    endcase
  end
  wire [15:0] djnz_dec = rr_cx[16 * rn_djnz +: 16] - 16'd1;
  wire [8:0]  pc_inc = pc[c] + 9'd1;

  // Both are worked out from registers alone, and the instruction only selects. Equal to
  // djnz_dec != 0 and bc_dec != 0.
  wire [3:0] loops = {r3_c != 16'd1, r2_c != 16'd1, r1_c != 16'd1, r0_c != 16'd1};
  wire       djnz_loops = loops[rn_djnz];
  wire       bc_loops = bc_c > 5'd1;

  // pc - rel with the high part selected by the borrow, so the instruction doesn't ripple through 9 bits
  wire [4:0] back_lo = {1'b0, pc[c][3:0]} - {1'b0, ins[3:0]};
  wire [4:0] pc_hi = pc[c][8:4];
  wire [4:0] pc_hi_dec = pc_hi - 5'd1;
  wire [8:0] pc_back = {back_lo[4] ? pc_hi_dec : pc_hi, back_lo[3:0]};

  wire to_target = (is_jmp && take_jmp) || (is_djnz && djnz_loops);
  wire to_back = is_bit && bc_loops;
  wire [8:0] pc_taken = to_target ? ins[8:0]
                      : is_call   ? ins[11:3]
                      : is_ret    ? lr_q[9 * c +: 9]
                      : to_back   ? pc_back
                      :             pc_inc;
  wire [8:0] pc_next = ex ? pc_taken : pc[c];

  // With more than one context the next slot is another context's, and its PC is already settled.
  assign fetch_addr = prim ? pc[0] : (nctx_m1 != 0 ? pc[cur_nx] : pc_next);

  // ------------------------------------------------------------------ Lines
  always @* begin
    eng_nx = eng;
    src_nx = src;
    if (active && side[1] && side_ln[c] < 4'd12) eng_nx[side_ln[c][3:0]] = side[0];
    if (ex) begin
      if (is_set && ln_a < 4'd12) eng_nx[ln_a] = ins[6];
      if ((is_out || is_outb) && ln_a < 4'd12) eng_nx[ln_a] = sbit;
      if (is_mode) src_nx[3 * ins[10:8] +: 3] = ins[7:5];
    end
  end

  // ------------------------------------------------------------------ FIFOs
  assign h2e_pop = ex && (is_pullw || (is_pull && !h2e_empty));
  assign e2h_push = ex && is_push;
  assign e2h_data = sr_c;
  assign cap_on = ex && is_capon;
  assign cap_off = ex && is_capoff;
  assign cap_mark = ex && is_capmark;

  // ------------------------------------------------------------------ Clocked state
  // The immediate is 8 bits, so only the low half adds, and the high half is picked by its carry.
  wire [15:0] add_reg = rr_cx[16 * rn_add +: 16];
  wire [8:0]  add_lo = {1'b0, add_reg[7:0]} + {1'b0, ins[7:0]};
  wire [7:0]  add_hi_inc = add_reg[15:8] + 8'd1;
  wire [15:0] alu_add = {add_lo[8] ? add_hi_inc : add_reg[15:8], add_lo[7:0]};
  wire [15:0] alu_xor = rr_cx[16 * rn_alu +: 16] ^ opnd_alu;
  wire [15:0] alu_and = rr_cx[16 * rn_alu +: 16] & opnd_alu;
  wire [15:0] alu_addr = rr_cx[16 * rn_alu +: 16] + opnd_alu;
  // CRC step on the last shifted bit: left with a left-aligned polynomial, or right with a reflected one
  wire [15:0] crc_reg  = rr_cx[16 * rn_alu +: 16];
  wire        crc_fb   = (ins[5] ? crc_reg[15] : crc_reg[0]) ^ (full_c && lastb[cf]);
  wire [15:0] alu_crc  = (ins[5] ? {crc_reg[14:0], 1'b0} : {1'b0, crc_reg[15:1]}) ^ ({16{crc_fb && full_c}} & poly_q[16 * cf +: 16]);
  wire [15:0] lfsr_c = full_c ? lfsr_q[16 * cf +: 16] : 16'd0;
  wire [15:0] lfsr_nx = {1'b0, lfsr_c[15:1]} ^ (lfsr_c[0] ? 16'hB400 : 16'h0000);
  wire [15:0] mov_val = opnd_mov;
  wire [16:0] phase_half = {2'd0, tp_c[15:1]};

  // Write ports. Each state element takes one data word selected by AND-OR, rather than one path per
  // instruction, which would give every bit of the register file its own mux tree.
  wire [15:0] ldi_reg = rr_cx[16 * rn_ldi +: 16];
  // TCAP.t runs on the cycle of its tick, before the latch takes the interval ending there, so pass it on
  wire [15:0] since_c = full_c ? since[cf] : 16'hFFFF;
  wire [15:0] since_inc = since_c == 16'hFFFF ? 16'hFFFF : since_c + 16'd1;
  wire [15:0] tint_now = !full_c ? 16'hFFFF : tick_now[c] ? since_inc : tint[cf];
  wire [15:0] alu_reg = rr_cx[16 * rn_alu +: 16];
  wire [15:0] sft_reg = rr_cx[16 * rn_sft +: 16];
  wire        is_shift = is_shr || is_shl;
  wire        mov_rr = is_mov && !ins[7];

  wire rr_we = ex && (is_ldi || is_add || is_addr || is_crc || is_xor || is_and || is_shift || is_rand || is_djnz || mov_rr || is_tcap);
  wire [1:0] rr_idx = ({2{is_ldi}} & rn_ldi) | ({2{is_add}} & rn_add) | ({2{is_xor || is_and || is_addr || is_crc || is_tcap}} & rn_alu)
                    | ({2{is_shift || is_rand}} & rn_sft) | ({2{is_djnz}} & rn_djnz) | ({2{mov_rr}} & ins[6:5]);
  wire [15:0] rr_wd = ({16{is_ldi}} & (ins[9] ? {ins[8:1], ldi_reg[7:0]} : {8'd0, ins[8:1]}))
                    | ({16{is_add}} & alu_add)
                    | ({16{is_addr}} & alu_addr)
                    | ({16{is_crc}} & alu_crc)
                    | ({16{is_xor}} & alu_xor)
                    | ({16{is_and}} & alu_and)
                    | ({16{is_shr}} & {1'b0, sft_reg[15:1]})
                    | ({16{is_shl}} & {sft_reg[14:0], 1'b0})
                    | ({16{is_rand}} & lfsr_nx)
                    | ({16{is_djnz}} & djnz_dec)
                    | ({16{mov_rr}} & mov_val)
                    | ({16{is_tcap}} & tint_now);

  wire z_we = is_add || is_addr || is_crc || is_xor || is_and || is_cmp || is_cmpu || is_cmpi || is_tstb || is_probe;
  wire z_wd = (is_add && alu_add == 0) || (is_addr && alu_addr == 0) || (is_crc && alu_crc == 0) || (is_xor && alu_xor == 0) || (is_and && alu_and == 0)
           || ((is_cmp || is_cmpu) && opnd_cmp == opnd_mov) || (is_cmpi && add_reg == {8'd0, ins[7:0]})
           || (is_tstb && sbit) || (is_probe && lines[ln_p]);

  wire f_we = is_pull || (is_push && e2h_full) || is_shift || is_cmpu;
  wire f_wd = (is_pull && h2e_empty) || is_push || (is_shr && sft_reg[0]) || (is_shl && sft_reg[15])
           || (is_cmpu && opnd_cmp < opnd_mov);

  wire sr_out = (is_out && !ins[6]) || is_outb;
  wire sr_in = is_in || is_inb;
  wire sr_head = (is_pull && !h2e_empty) || is_pullw;
  wire mov_sr = is_mov && ins[7:5] == 3'd4;
  wire sr_we = sr_out || sr_in || sr_head || is_sri || mov_sr;
  wire [15:0] sr_wd = ({16{sr_out}} & sr_shifted_out) | ({16{sr_in}} & sr_shifted_in)
                    | ({16{sr_head}} & h2e_head) | ({16{is_sri}} & ({8'd0, ins[11:4]} << ins[3:0]))
                    | ({16{mov_sr}} & mov_val);

  wire mov_bc = is_mov && ins[7:5] == 3'd5;
  wire bc_we = sr_out || sr_in || is_pullw || is_bci || mov_bc;
  wire [4:0] bc_wd = ({5{sr_out || sr_in}} & bc_dec) | ({5{is_pullw || is_bci}} & ins[7:3]) | ({5{mov_bc}} & mov_val[4:0]);

  // A reset clears every register: all words take 0 at the end of the reset cycle.
  wire [4*NCTX-1:0] rr_wen;
  genvar gw;
  generate
    for (gw = 0; gw < 4 * NCTX; gw = gw + 1) begin : rr_w
      assign rr_wen[gw] = rst || (rr_we && {c, rr_idx} == gw);
    end
  endgenerate
  wire [15:0] stall_wd = is_dly ? {4'd0, ins[11:0]} : is_dlyr ? alu_reg : {12'd0, dly_field};

  // One word per context. A reset writes every word its reset value; otherwise the current context's.
  localparam [NCTX-1:0] ONE = 1;
  localparam [NFULL-1:0] ONE_F = 1;
  wire [NCTX-1:0]  all_w = {NCTX{rst}};
  wire [NCTX-1:0]  cur_w = ONE << c;
  wire [NFULL-1:0] full_w = full_c ? ONE_F << cf : {NFULL{1'b0}};
  wire stall_we = slot && !halted[c] && (stall_c != 0 || ex);
  wire to_left_we = gated && is_wait && !wait_met && to_on && !to_expired;
  splice_latch_rf #(.WORDS(NCTX), .WIDTH(9)) lr_rf (.clk(clk), .we(all_w | {NCTX{ex && is_call}} & cur_w),
      .wd(rst ? 9'd0 : pc_inc), .q(lr_q));
  splice_latch_rf #(.WORDS(NCTX), .WIDTH(16)) sr_rf (.clk(clk), .we(all_w | {NCTX{ex && sr_we}} & cur_w),
      .wd(rst ? 16'd0 : sr_wd), .q(sr_q));
  splice_latch_rf #(.WORDS(NCTX), .WIDTH(5)) bc_rf (.clk(clk), .we(all_w | {NCTX{ex && bc_we}} & cur_w),
      .wd(rst ? 5'd0 : bc_wd), .q(bc_q));
  splice_latch_rf #(.WORDS(NCTX), .WIDTH(16)) stall_rf (.clk(clk), .we(all_w | {NCTX{stall_we}} & cur_w),
      .wd(rst ? 16'd0 : stall_c != 0 ? stall_c - 16'd1 : stall_wd), .q(stall_q));
  splice_latch_rf #(.WORDS(NCTX), .WIDTH(16)) tp_rf (.clk(clk),
      .we(all_w | {NCTX{ex && (is_tset || is_tsetr)}} & cur_w),
      .wd(rst ? 16'd0 : is_tset ? {3'd0, ins[12:0]} : opnd_cmp), .q(tp_q));
  splice_latch_rf #(.WORDS(NCTX), .WIDTH(12)) last_rf (.clk(clk),
      .we(all_w | {NCTX{prim && running}} | {NCTX{slot}} & cur_w),
      .wd(rst ? 12'hFFF : lines_in), .q(last_q));
  splice_latch_rf #(.WORDS(NFULL), .WIDTH(16)) to_rf (.clk(clk),
      .we({NFULL{rst}} | {NFULL{ex && is_mov && ins[7:5] == 3'd6}} & full_w), .wd(rst ? 16'd0 : mov_val), .q(to_q));
  splice_latch_rf #(.WORDS(NFULL), .WIDTH(16)) to_left_rf (.clk(clk),
      .we({NFULL{rst}} | {NFULL{to_left_we}} & full_w), .wd(rst ? 16'd0 : to_dec), .q(to_left_q));
  splice_latch_rf #(.WORDS(NFULL), .WIDTH(16)) lfsr_rf (.clk(clk),
      .we({NFULL{rst}} | {NFULL{ex && (is_rand || (is_mov && ins[7:5] == 3'd7))}} & full_w),
      .wd(rst ? 16'hACE1 : is_rand ? lfsr_nx : mov_val), .q(lfsr_q));
  splice_latch_rf #(.WORDS(NFULL), .WIDTH(16)) poly_rf (.clk(clk),
      .we({NFULL{rst}} | {NFULL{ex && is_poly}} & full_w), .wd(rst ? 16'd0 : opnd_cmp), .q(poly_q));

  splice_latch_rf #(.WORDS(4 * NCTX)) rf (.clk(clk), .we(rr_wen), .wd(rst ? 16'd0 : rr_wd), .q(rr_q));

  integer i;
  always @(posedge clk) begin
    if (rst) begin
      running <= start;
      prim <= start;
      cur <= 0;
      halted <= 0;
      irq <= 1'b0;
      trig <= 1'b0;
      eng <= 12'h0FF;
      src <= 24'd0;
      for (i = 0; i < NCTX; i = i + 1) begin
        pc[i] <= 9'd0;
        z[i] <= 0;
        f[i] <= 0;
        m[i] <= 0;
        msb[i] <= 0;
        side_ln[i] <= 4'd15;
        tcnt[i] <= 0;
        pending[i] <= 0;
        ts_line[i] <= 0;
        ts_want[i] <= 2'd3;
      end
      for (i = 0; i < NFULL; i = i + 1) begin
        to_act[i] <= 0;
        since[i] <= 16'hFFFF;
        tint[i] <= 16'hFFFF;
        lastb[i] <= 0;
      end
      for (i = 1; i < NCTX; i = i + 1) pc[i] <= ctx_pc[9 * i - 9 +: 9];
    end else begin
      trig <= 1'b0;
      eng <= eng_nx;
      src <= src_nx;

      // Timers run on every clock, for both contexts.
      for (i = 0; i < NCTX; i = i + 1) begin
        if (ts_want[i] == 2'd3 && tp_q[16 * i +: 16] != 0)
          tcnt[i] <= tcnt[i] <= 17'd1 ? {1'b0, tp_q[16 * i +: 16]} : tcnt[i] - 17'd1;
        if (tick_now[i]) pending[i] <= 1'b1;
      end
      // TCAP: the interval that ends at each tick is latched, so firmware can read it late
      for (i = 0; i < NFULL; i = i + 1) begin
        if (tick_now[i]) begin
          tint[i] <= since[i] == 16'hFFFF ? 16'hFFFF : since[i] + 16'd1;  // since_inc, per context
          since[i] <= 16'd0;
        end else if (since[i] != 16'hFFFF) begin
          since[i] <= since[i] + 16'd1;
        end
      end

      if (prim) begin
        prim <= 1'b0;
      end else if (running) begin
        cur <= cur_nx;

        if (!halted[c]) begin
          if (stall_c != 0) begin
          end else begin
            if (active && tick_q) pending[c] <= 1'b0;

            // WAIT holds its timeout state even while blocked.
            if (is_wait && !halted[c]) begin
              if (wait_met) begin
                if (full_c) to_act[cf] <= 1'b0;
              end else if (to_on) begin  // never in a light context
                if (to_expired) begin
                  f[c] <= 1'b1;
                  to_act[cf] <= 1'b0;
                end else begin
                  to_act[cf] <= 1'b1;
                end
              end
            end

            if (ex) begin
              pc[c] <= pc_next;
              if (z_we) z[c] <= z_wd;
              if (f_we) f[c] <= f_wd;
              if (is_waitt) pending[c] <= 1'b0;
              if (is_tset) begin
                tcnt[c] <= {4'd0, ins[12:0]};
                pending[c] <= 1'b0;
              end
              if (is_tsetr) begin
                tcnt[c] <= {1'b0, opnd_cmp};
                pending[c] <= 1'b0;
              end
              if (is_tphase) begin
                case (ins[7:6])
                  2'd0: tcnt[c] <= {1'b0, tp_c} - 17'd1;
                  2'd1: tcnt[c] <= phase_half - 17'd1;
                  2'd2: tcnt[c] <= {1'b0, tp_c} + phase_half - 17'd1;
                  default: tcnt[c] <= {1'b0, r1_c};
                endcase
                pending[c] <= 1'b0;
              end
              if (is_tsrc) begin
                ts_line[c] <= ln_p;
                ts_want[c] <= ins[3:2];
                pending[c] <= 1'b0;
              end
              if ((is_out || is_outb) && full_c) lastb[cf] <= sbit;
              if ((is_in || is_inb) && full_c) lastb[cf] <= in_bit;
              if (is_msb) msb[c] <= ins[7];
              if (is_side) side_ln[c] <= ln_p;
              if (is_irq) irq <= 1'b1;
              if (is_trig) trig <= 1'b1;
              if (is_halt) halted[c] <= 1'b1;
              if (is_jmp && ins[11:9] == 3'd7) m[c] <= 1'b0;  // JMP M reads and clears
            end
          end
        end
      end

      // A missed tick sets M, not F, so a timer left running can't pass for a full FIFO or a timeout.
      // It is visible next cycle and beats JMP M clearing it, so a deadline miss can never be silently
      // lost. This must stay after the instruction logic.
      for (i = 0; i < NCTX; i = i + 1)
        if (tick_now[i] && pending[i]) m[i] <= 1'b1;
    end
  end
endmodule
