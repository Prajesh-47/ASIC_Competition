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

module splice_engine #(parameter NCTX = 4) (
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
  reg [8:0]  lr      [0:NCTX-1];
  reg [15:0] rr      [0:4*NCTX-1];   // R0-R3 for each context
  reg [15:0] sr      [0:NCTX-1];
  reg [4:0]  bc      [0:NCTX-1];
  reg [15:0] to      [0:NCTX-1];
  reg [15:0] to_left [0:NCTX-1];
  reg        to_act  [0:NCTX-1];
  reg [15:0] stall   [0:NCTX-1];
  reg        z       [0:NCTX-1];
  reg        f       [0:NCTX-1];
  reg        m       [0:NCTX-1];   // a tick fired while the last was still pending, until JMP M reads it
  reg        msb     [0:NCTX-1];
  reg [3:0]  side_ln [0:NCTX-1];
  reg [15:0] lfsr    [0:NCTX-1];
  reg [15:0] tp      [0:NCTX-1];   // 16 bits from TSETR; TSET's constant fills the low 13
  reg [16:0] tcnt    [0:NCTX-1];   // one more, so TPHASE can start one and a half periods
  reg [15:0] since   [0:NCTX-1];   // cycles since the last tick, saturating
  reg [15:0] tint    [0:NCTX-1];   // cycles between the last two ticks, for TCAP
  reg        pending [0:NCTX-1];
  reg [3:0]  ts_line [0:NCTX-1];
  reg [1:0]  ts_want [0:NCTX-1];
  reg [11:0] last    [0:NCTX-1];   // what this context saw at its previous slot, for edge waits
  reg [15:0] poly    [0:NCTX-1];   // CRC polynomial, set by POLY
  reg [7:0]  lastb   [0:NCTX-1];   // the lane group last shifted by OUT, OUTB, IN or INB, which CRC steps on
  reg        ldp_pend[0:NCTX-1];   // LDP: the data word arrives at this context's next slot
  reg [1:0]  ldp_dst [0:NCTX-1];
  reg [8:0]  ldp_addr[0:NCTX-1];   // with several contexts it's fetched in the slot before this one's next
  reg [1:0]  lw      [0:NCTX-1];   // LANES: OUT, IN, OUTB and INB move 1 << lw lines at once
  // CODE, the line coder (v2): NRZI, bit stuffing, Manchester, a differential pair and clock recovery
  reg        cd_nrzi [0:NCTX-1];
  reg        cd_kind [0:NCTX-1];   // stuffing: 0 a 0 after n ones (USB, HDLC), 1 the complement after n equal (CAN)
  reg [2:0]  cd_n    [0:NCTX-1];   // run length that forces a stuff bit, 0 off
  reg        cd_man  [0:NCTX-1];
  reg        cd_pol  [0:NCTX-1];   // Manchester half-bits: 0 !b then b (IEEE), 1 b then !b
  reg        cd_diff [0:NCTX-1];   // also drive line ^ 1 with the complement
  reg        cd_sync [0:NCTX-1];   // an edge on the TSRC line re-phases the period timer to half a period
  reg [2:0]  run     [0:NCTX-1];   // the run of data bits, saturating at 7
  reg        runv    [0:NCTX-1];   // the run's value, for kind 1
  reg        half    [0:NCTX-1];   // Manchester: the first half-bit has gone out
  reg        rxp     [0:NCTX-1];   // NRZI receive: the line at the previous coded IN

  reg        prim;            // first cycle of a run: only fetches
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

  // Only decode memory output while executing. It's undefined before the first read, and this keeps
  // that from reaching any logic, on silicon as well as in gate-level simulation.
  wire [15:0] ins = (running && !prim) ? mem_data : 16'd0;

  // Lines 12-15 don't exist: they read as 1 and ignore writes.
  wire [15:0] lines = {4'hF, lines_in};
  wire [15:0] plines = {4'hF, lines_prev};

  // ------------------------------------------------------------------ Tick sources, every cycle
  wire [NCTX-1:0] tick_now;
  wire [NCTX-1:0] resync;   // CODE sync: an edge on the TSRC line, so the period timer re-phases
  genvar g;
  generate
    for (g = 0; g < NCTX; g = g + 1) begin : ticks
      wire edge_seen = lines[ts_line[g]] != plines[ts_line[g]];
      assign resync[g] = cd_sync[g] && edge_seen;
      assign tick_now[g] = (ts_want[g] != 2'd3)
          ? edge_seen && (ts_want[g] == 2'd2 || lines[ts_line[g]] == ts_want[g][0])
          : (tp[g] != 0 && tcnt[g] <= 17'd1);
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
  wire is_code   = ins[15:10] == 6'b110001;
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
  wire is_ldp    = ins[15:8] == 8'b11101101;
  wire is_jmpr   = ins[15:8] == 8'b11101110;
  wire is_lanes  = ins[15:8] == 8'b11101111;
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
  wire [15:0] r0_c = rr[{c, 2'd0}];
  wire [15:0] r1_c = rr[{c, 2'd1}];
  wire [15:0] r2_c = rr[{c, 2'd2}];
  wire [15:0] r3_c = rr[{c, 2'd3}];
  wire [15:0] sr_c = sr[c];
  wire [4:0]  bc_c = bc[c];
  wire [15:0] to_c = to[c];

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

  wire        sbit = msb[c] ? sr[c][15] : sr[c][0];
  // Lanes: a group of W = 1 << lw lines from the lane-aligned base ln_a, line base + k being bit k
  wire [1:0]  lw_c = lw[c];
  wire        msb_c = msb[c];
  // CODE at one lane. What repeats an instruction is worked out from registers alone, and the instruction
  // only selects, as for the loops below: a stuff bit, or Manchester's first half-bit.
  wire        cd_any   = cd_nrzi[c] || cd_n[c] != 3'd0;
  wire        cd_tx_on = lw[c] == 2'd0 && (cd_any || cd_man[c]);
  wire        cd_rx_on = lw[c] == 2'd0 && cd_any && !cd_man[c];
  wire        stuff_due = cd_n[c] != 3'd0 && run[c] == cd_n[c];
  wire        rep_tx_r = cd_tx_on && (cd_man[c] ? !half[c] : stuff_due);
  wire        rep_rx_r = cd_rx_on && stuff_due;
  wire        shop_tx  = (is_out && !ins[6]) || is_outb;
  wire        shop_rx  = is_in || is_inb;
  wire        code_tx  = cd_tx_on && shop_tx;
  wire        code_rx  = cd_rx_on && shop_rx;
  wire        rep      = (shop_tx && rep_tx_r) || (shop_rx && rep_rx_r);
  wire        rx_raw   = lines[ln_a];
  wire        rx_bit   = (cd_rx_on && cd_nrzi[c]) ? rx_raw == rxp[c] : rx_raw;   // NRZI: no change is a 1
  wire        stuff_bit = cd_kind[c] ? !runv[c] : 1'b0;
  wire        rx_stuff_err = code_rx && stuff_due && rx_bit != stuff_bit;
  wire        tx_b     = msb[c] ? sr[c][15] : sr[c][0];
  wire        tx_d     = stuff_due ? stuff_bit : tx_b;
  wire        tx_cur   = ln_a < 4'd12 ? eng[ln_a] : 1'b0;
  wire        man_first = cd_pol[c] ? tx_b : !tx_b;
  wire        tx_line  = cd_man[c] ? (half[c] ? !man_first : man_first)
                       : cd_nrzi[c] ? (tx_d ? tx_cur : !tx_cur) : tx_d;
  wire [3:0]  ln_pair  = ln_a ^ 4'd1;
  // The run after a data or stuff bit d
  wire        run_d    = code_rx ? rx_bit : tx_d;
  wire [2:0]  run_inc  = run[c] == 3'd7 ? 3'd7 : run[c] + 3'd1;
  wire [2:0]  run_nx   = stuff_due ? {2'd0, cd_kind[c]}
                       : cd_kind[c] ? ((run[c] != 3'd0 && run_d == runv[c]) ? run_inc : 3'd1)
                       : (run_d ? run_inc : 3'd0);
  wire        runv_nx  = (stuff_due || cd_kind[c]) ? run_d : runv[c];

  reg  [7:0]  grp_out, grp_in;
  reg  [15:0] sr_shifted_out, sr_shifted_in;
  always @* begin
    case (lw_c)
      2'd0: begin
        grp_out = {7'd0, msb_c ? sr_c[15] : sr_c[0]};
        grp_in = {7'd0, rx_bit};
        sr_shifted_out = msb_c ? {sr_c[14:0], 1'b0} : {1'b0, sr_c[15:1]};
        sr_shifted_in = msb_c ? {sr_c[14:0], grp_in[0]} : {grp_in[0], sr_c[15:1]};
      end
      2'd1: begin
        grp_out = {6'd0, msb_c ? sr_c[15:14] : sr_c[1:0]};
        grp_in = {6'd0, lines[{ln_a[3:1], 1'b0} +: 2]};
        sr_shifted_out = msb_c ? {sr_c[13:0], 2'd0} : {2'd0, sr_c[15:2]};
        sr_shifted_in = msb_c ? {sr_c[13:0], grp_in[1:0]} : {grp_in[1:0], sr_c[15:2]};
      end
      2'd2: begin
        grp_out = {4'd0, msb_c ? sr_c[15:12] : sr_c[3:0]};
        grp_in = {4'd0, lines[{ln_a[3:2], 2'b00} +: 4]};
        sr_shifted_out = msb_c ? {sr_c[11:0], 4'd0} : {4'd0, sr_c[15:4]};
        sr_shifted_in = msb_c ? {sr_c[11:0], grp_in[3:0]} : {grp_in[3:0], sr_c[15:4]};
      end
      default: begin
        grp_out = msb_c ? sr_c[15:8] : sr_c[7:0];
        grp_in = ln_a[3] ? lines[15:8] : lines[7:0];
        sr_shifted_out = msb_c ? {sr_c[7:0], 8'd0} : {8'd0, sr_c[15:8]};
        sr_shifted_in = msb_c ? {sr_c[7:0], grp_in} : {grp_in, sr_c[15:8]};
      end
    endcase
  end
  wire [4:0]  bc_dec = (bc[c] == 0) ? 5'd0 : bc[c] - 5'd1;

  // FIFO view of the current context. The top level routes it to the host FIFOs or a link
  wire        in_empty = h2e_empty;
  wire [15:0] in_head = h2e_head;
  wire        out_full = e2h_full;

  // ------------------------------------------------------------------ Execute conditions
  wire slot = running && !prim;
  wire gated = slot && !halted[c] && stall[c] == 0;
  wire pend_eff = pending[c] || tick_now[c];
  wire active = gated && (!tick_q || pend_eff);   // reached: a side-set applies

  // WAIT
  wire wait_bit = lines[ln_w];
  wire wait_was = ln_w < 4'd12 ? last[c][ln_w[3:0]] : 1'b1;
  reg  wait_met;
  always @* begin
    case (ins[5:4])
      2'd0: wait_met = !wait_bit;
      2'd1: wait_met = wait_bit;
      2'd2: wait_met = !wait_was && wait_bit;
      default: wait_met = wait_was && !wait_bit;
    endcase
  end
  wire [15:0] to_cur = to_act[c] ? to_left[c] : to[c];
  wire [15:0] to_dec = to_cur - 16'd1;
  wire        to_on = to[c] != 0;
  wire        to_expired = to_on && to_dec == 0;
  wire        wait_block = is_wait && !wait_met && !to_expired;

  wire blocked = wait_block
              || (is_waitt && !pend_eff)
              || (is_pullw && in_empty);
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
      3'd5: take_jmp = bc[c] == 0;
      3'd6: take_jmp = bc[c] != 0;
      default: take_jmp = m[c];
    endcase
  end
  wire [15:0] djnz_dec = rr[{c, rn_djnz}] - 16'd1;
  wire [8:0]  pc_inc = pc[c] + 9'd1;

  // Both are worked out from registers alone, and the instruction only selects. Equal to
  // djnz_dec != 0 and bc_dec != 0.
  wire [3:0] loops = {r3_c != 16'd1, r2_c != 16'd1, r1_c != 16'd1, r0_c != 16'd1};
  wire       djnz_loops = loops[rn_djnz];
  wire       bc_loops = bc[c] > 5'd1;

  // pc - rel with the high part selected by the borrow, so the instruction doesn't ripple through 9 bits
  wire [4:0] back_lo = {1'b0, pc[c][3:0]} - {1'b0, ins[3:0]};
  wire [4:0] pc_hi = pc[c][8:4];
  wire [4:0] pc_hi_dec = pc_hi - 5'd1;
  wire [8:0] pc_back = {back_lo[4] ? pc_hi_dec : pc_hi, back_lo[3:0]};

  wire to_target = (is_jmp && take_jmp) || (is_djnz && djnz_loops);
  wire to_back = is_bit && bc_loops;
  wire [8:0] pc_taken = to_target ? ins[8:0]
                      : is_call   ? ins[11:3]
                      : is_ret    ? lr[c]
                      : is_jmpr   ? opnd_cmp[8:0]
                      : to_back   ? pc_back
                      :             pc_inc;
  wire [8:0] pc_next = (ex && !rep) ? pc_taken : pc[c];   // CODE: a repeat keeps the PC

  // With more than one context the next slot is another context's, and its PC is already settled. LDP reads
  // its word in place of an instruction fetch: straight away with one context, and with several in the slot
  // before the LDP context's next, which would otherwise fetch that context's next instruction.
  wire [8:0] ldp_src = rr[{c, ins[5:4]}][8:0];
  assign fetch_addr = prim ? pc[0]
                    : nctx_m1 != 0 ? (ldp_pend[cur_nx] ? ldp_addr[cur_nx] : pc[cur_nx])
                    : (ex && is_ldp) ? ldp_src : pc_next;

  // ------------------------------------------------------------------ Lines
  integer l;
  always @* begin
    eng_nx = eng;
    src_nx = src;
    if (active && side[1] && side_ln[c] < 4'd12) eng_nx[side_ln[c][3:0]] = side[0];
    if (ex) begin
      if (is_set && ln_a < 4'd12) eng_nx[ln_a] = ins[6];
      if (is_out || is_outb)
        for (l = 0; l < 12; l = l + 1)
          if ((l >> lw_c) == (ln_a >> lw_c)) eng_nx[l] = grp_out[l[2:0] & ((3'd1 << lw_c) - 3'd1)];
      if (code_tx) begin
        if (ln_a < 4'd12) eng_nx[ln_a] = tx_line;
        if (cd_diff[c] && ln_pair < 4'd12) eng_nx[ln_pair] = !tx_line;
      end
      if (is_mode) src_nx[3 * ins[10:8] +: 3] = ins[7:5];
    end
  end

  // ------------------------------------------------------------------ FIFOs
  wire   take_in = ex && (is_pullw || (is_pull && !in_empty));
  assign h2e_pop = take_in;
  assign e2h_push = ex && is_push;
  assign e2h_data = sr[c];
  assign cap_on = ex && is_capon;
  assign cap_off = ex && is_capoff;
  assign cap_mark = ex && is_capmark;

  // ------------------------------------------------------------------ Clocked state
  // The immediate is 8 bits, so only the low half adds, and the high half is picked by its carry.
  wire [15:0] add_reg = rr[{c, rn_add}];
  wire [8:0]  add_lo = {1'b0, add_reg[7:0]} + {1'b0, ins[7:0]};
  wire [7:0]  add_hi_inc = add_reg[15:8] + 8'd1;
  wire [15:0] alu_add = {add_lo[8] ? add_hi_inc : add_reg[15:8], add_lo[7:0]};
  wire [15:0] alu_xor = rr[{c, rn_alu}] ^ opnd_alu;
  wire [15:0] alu_and = rr[{c, rn_alu}] & opnd_alu;
  wire [15:0] alu_addr = rr[{c, rn_alu}] + opnd_alu;
  // CRC step on the last shifted bit: left with a left-aligned polynomial, or right with a reflected one
  // CRC r, dir, lane: one step on bit `lane` of the last group. CRCW (ins[4]): one lane's bits in SR, oldest
  // first, 4 steps at stride 4, or 2 at stride 8 with 8 lanes.
  wire [15:0] crc_reg  = rr[{c, rn_alu}];
  wire        crc_dir  = ins[5];
  wire [2:0]  crc_lane = ins[3:1];
  wire        crc_wide = lw_c == 2'd3;
  wire [31:0] sr_pad   = {16'd0, sr_c};
  function automatic [15:0] crc_step(input [15:0] v, input b, input dir, input [15:0] p);
    crc_step = (dir ? {v[14:0], 1'b0} : {1'b0, v[15:1]}) ^ ({16{(dir ? v[15] : v[0]) ^ b}} & p);
  endfunction
  reg [3:0] crc_bits;   // oldest first, in the order the steps take them
  always @* begin
    if (crc_wide)
      crc_bits = msb_c ? {sr_pad[8 + crc_lane], sr_pad[crc_lane], 2'b00} : {sr_pad[crc_lane], sr_pad[8 + crc_lane], 2'b00};
    else
      crc_bits = msb_c ? {sr_pad[12 + crc_lane], sr_pad[8 + crc_lane], sr_pad[4 + crc_lane], sr_pad[crc_lane]}
                       : {sr_pad[crc_lane], sr_pad[4 + crc_lane], sr_pad[8 + crc_lane], sr_pad[12 + crc_lane]};
  end
  wire [15:0] poly_c   = poly[c];
  wire [7:0]  lastb_c  = lastb[c];
  wire [15:0] crc_s1   = crc_step(crc_reg, ins[4] ? crc_bits[3] : lastb_c[crc_lane], crc_dir, poly_c);
  wire [15:0] crc_s2   = crc_step(crc_s1, crc_bits[2], crc_dir, poly_c);
  wire [15:0] crc_s4   = crc_step(crc_step(crc_s2, crc_bits[1], crc_dir, poly_c), crc_bits[0], crc_dir, poly_c);
  wire [15:0] alu_crc  = !ins[4] ? crc_s1 : crc_wide ? crc_s2 : crc_s4;
  wire [15:0] lfsr_nx = {1'b0, lfsr[c][15:1]} ^ (lfsr[c][0] ? 16'hB400 : 16'h0000);
  wire [15:0] mov_val = opnd_mov;
  wire [16:0] phase_half = {2'd0, tp[c][15:1]};

  // Write ports. Each state element takes one data word selected by AND-OR, rather than one path per
  // instruction, which would give every bit of the register file its own mux tree.
  wire [15:0] ldi_reg = rr[{c, rn_ldi}];
  // TCAP.t runs on the cycle of its tick, before the latch takes the interval ending there, so pass it on
  wire [15:0] since_inc = since[c] == 16'hFFFF ? 16'hFFFF : since[c] + 16'd1;
  wire [15:0] tint_now = tick_now[c] ? since_inc : tint[c];
  wire [15:0] alu_reg = rr[{c, rn_alu}];
  wire [15:0] sft_reg = rr[{c, rn_sft}];
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

  wire f_we = is_pull || (is_push && out_full) || is_shift || is_cmpu;
  wire f_wd = (is_pull && in_empty) || is_push || (is_shr && sft_reg[0]) || (is_shl && sft_reg[15])
           || (is_cmpu && opnd_cmp < opnd_mov);

  wire sr_out = ((is_out && !ins[6]) || is_outb) && !rep;
  wire sr_in = (is_in || is_inb) && !rep;
  wire sr_head = (is_pull && !in_empty) || is_pullw;
  wire mov_sr = is_mov && ins[7:5] == 3'd4;
  wire sr_we = sr_out || sr_in || sr_head || is_sri || mov_sr;
  wire [15:0] sr_wd = ({16{sr_out}} & sr_shifted_out) | ({16{sr_in}} & sr_shifted_in)
                    | ({16{sr_head}} & in_head) | ({16{is_sri}} & ({8'd0, ins[11:4]} << ins[3:0]))
                    | ({16{mov_sr}} & mov_val);

  wire mov_bc = is_mov && ins[7:5] == 3'd5;
  wire bc_we = sr_out || sr_in || is_pullw || is_bci || mov_bc;
  wire [4:0] bc_wd = ({5{sr_out || sr_in}} & bc_dec) | ({5{is_pullw || is_bci}} & ins[7:3]) | ({5{mov_bc}} & mov_val[4:0]);

  wire [15:0] stall_wd = is_dly ? {4'd0, ins[11:0]} : is_dlyr ? alu_reg : is_ldp ? 16'd1 : {12'd0, dly_field};

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
        lr[i] <= 0;
        sr[i] <= 0;
        bc[i] <= 0;
        to[i] <= 0;
        to_left[i] <= 0;
        to_act[i] <= 0;
        stall[i] <= 0;
        z[i] <= 0;
        f[i] <= 0;
        m[i] <= 0;
        msb[i] <= 0;
        side_ln[i] <= 4'd15;
        lfsr[i] <= 16'hACE1;
        tp[i] <= 0;
        tcnt[i] <= 0;
        pending[i] <= 0;
        since[i] <= 16'hFFFF;
        tint[i] <= 16'hFFFF;
        ts_line[i] <= 0;
        ts_want[i] <= 2'd3;
        last[i] <= 12'hFFF;
        poly[i] <= 0;
        lastb[i] <= 0;
        lw[i] <= 0;
        cd_nrzi[i] <= 0;
        cd_kind[i] <= 0;
        cd_n[i] <= 0;
        cd_man[i] <= 0;
        cd_pol[i] <= 0;
        cd_diff[i] <= 0;
        cd_sync[i] <= 0;
        run[i] <= 0;
        runv[i] <= 0;
        half[i] <= 0;
        rxp[i] <= 0;
        ldp_pend[i] <= 0;
        ldp_dst[i] <= 0;
        ldp_addr[i] <= 0;
      end
      for (i = 1; i < NCTX; i = i + 1) pc[i] <= ctx_pc[9 * i - 9 +: 9];
      for (i = 0; i < 4 * NCTX; i = i + 1) rr[i] <= 0;
    end else begin
      trig <= 1'b0;
      eng <= eng_nx;
      src <= src_nx;

      // Timers run on every clock, for both contexts.
      for (i = 0; i < NCTX; i = i + 1) begin
        if (ts_want[i] == 2'd3 && tp[i] != 0)
          tcnt[i] <= resync[i] ? {2'd0, tp[i][15:1]} : tcnt[i] <= 17'd1 ? {1'b0, tp[i]} : tcnt[i] - 17'd1;
        if (tick_now[i]) pending[i] <= 1'b1;
        // TCAP: the interval that ends at each tick is latched, so firmware can read it late
        if (tick_now[i]) begin
          tint[i] <= since[i] == 16'hFFFF ? 16'hFFFF : since[i] + 16'd1;  // since_inc, per context
          since[i] <= 16'd0;
        end else if (since[i] != 16'hFFFF) begin
          since[i] <= since[i] + 16'd1;
        end
      end

      if (prim) begin
        prim <= 1'b0;
        for (i = 0; i < NCTX; i = i + 1) last[i] <= lines_in;
      end else if (running) begin
        last[c] <= lines_in;
        cur <= cur_nx;

        if (!halted[c]) begin
          if (stall[c] != 0) begin
            stall[c] <= stall[c] - 16'd1;
            if (ldp_pend[c]) begin
              rr[{c, ldp_dst[c]}] <= mem_data;
              ldp_pend[c] <= 1'b0;
            end
          end else begin
            if (active && tick_q) pending[c] <= 1'b0;

            // WAIT holds its timeout state even while blocked.
            if (is_wait && !halted[c]) begin
              if (wait_met) begin
                to_act[c] <= 1'b0;
              end else if (to_on) begin
                if (to_expired) begin
                  f[c] <= 1'b1;
                  to_act[c] <= 1'b0;
                end else begin
                  to_left[c] <= to_dec;
                  to_act[c] <= 1'b1;
                end
              end
            end

            if (ex) begin
              pc[c] <= pc_next;
              stall[c] <= stall_wd;
              if (rr_we) rr[{c, rr_idx}] <= rr_wd;
              if (z_we) z[c] <= z_wd;
              if (f_we) f[c] <= f_wd;
              if (sr_we) sr[c] <= sr_wd;
              if (bc_we) bc[c] <= bc_wd;
              if (is_waitt) pending[c] <= 1'b0;
              if (is_tset) begin
                tp[c] <= {3'd0, ins[12:0]};
                tcnt[c] <= {4'd0, ins[12:0]};
                pending[c] <= 1'b0;
              end
              if (is_tsetr) begin
                tp[c] <= opnd_cmp;
                tcnt[c] <= {1'b0, opnd_cmp};
                pending[c] <= 1'b0;
              end
              if (is_tphase) begin
                case (ins[7:6])
                  2'd0: tcnt[c] <= {1'b0, tp[c]} - 17'd1;
                  2'd1: tcnt[c] <= phase_half - 17'd1;
                  2'd2: tcnt[c] <= {1'b0, tp[c]} + phase_half - 17'd1;
                  default: tcnt[c] <= {1'b0, rr[{c, 2'd1}]};
                endcase
                pending[c] <= 1'b0;
              end
              if (is_tsrc) begin
                ts_line[c] <= ln_p;
                ts_want[c] <= ins[3:2];
                pending[c] <= 1'b0;
              end
              if (is_poly) poly[c] <= opnd_cmp;
              if ((is_out || is_outb) && !rep) lastb[c] <= grp_out;
              if ((is_in || is_inb) && !rep) lastb[c] <= grp_in;
              if (is_code) begin
                {cd_nrzi[c], rxp[c], cd_kind[c], cd_n[c], cd_man[c], cd_pol[c], cd_diff[c], cd_sync[c]} <= ins[9:0];
                run[c] <= 3'd0;
                runv[c] <= 1'b0;
                half[c] <= 1'b0;
              end
              if (code_tx && cd_man[c]) half[c] <= !half[c];
              if ((code_tx && !cd_man[c]) || code_rx) begin
                run[c] <= run_nx;
                runv[c] <= runv_nx;
              end
              if (code_rx) rxp[c] <= rx_raw;
              if (rx_stuff_err) f[c] <= 1'b1;
              if (is_lanes) lw[c] <= ins[7:6];
              if (is_ldp) begin
                ldp_pend[c] <= 1'b1;
                ldp_dst[c] <= ins[7:6];
                ldp_addr[c] <= ldp_src;
              end
              if (is_msb) msb[c] <= ins[7];
              if (is_side) side_ln[c] <= ln_p;
              if (is_mov && ins[7:5] == 3'd6) to[c] <= mov_val;
              if (is_rand || (is_mov && ins[7:5] == 3'd7)) lfsr[c] <= is_rand ? lfsr_nx : mov_val;
              if (is_call) lr[c] <= pc_inc;
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
