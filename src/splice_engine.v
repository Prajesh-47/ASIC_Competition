/*
 * SPLICE: timing engine, ISA v1 (docs/architecture.md section 5)
 * Copyright (c) 2026 Prajesh-47
 * SPDX-License-Identifier: Apache-2.0
 *
 * Two contexts share one program memory: one context at full rate, or both at half rate.
 * Only explicit waits stall. Everything else takes 1 cycle plus its delay field.
 */
`default_nettype none

module splice_engine (
    input  wire        clk,
    input  wire        rst,        // synchronous: reset, or the host starting or stopping a run
    input  wire        start,
    input  wire        two_ctx,
    input  wire [8:0]  ctx1_pc,

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
    output reg  [1:0]  halted,
    output reg         irq,
    output reg         trig,
    output wire        cap_on,
    output wire        cap_off,
    output wire        cap_mark
);

  // ------------------------------------------------------------------ State, per context
  reg [8:0]  pc      [0:1];
  reg [8:0]  lr      [0:1];
  reg [15:0] rr      [0:7];   // R0-R3 for each context
  reg [15:0] sr      [0:1];
  reg [4:0]  bc      [0:1];
  reg [15:0] to      [0:1];
  reg [15:0] to_left [0:1];
  reg        to_act  [0:1];
  reg [15:0] stall   [0:1];
  reg        z       [0:1];
  reg        f       [0:1];
  reg        msb     [0:1];
  reg [3:0]  side_ln [0:1];
  reg [15:0] lfsr    [0:1];
  reg [15:0] tp      [0:1];   // 16 bits from TSETR; TSET's constant fills the low 13
  reg [16:0] tcnt    [0:1];   // one more, so TPHASE can start one and a half periods
  reg        pending [0:1];
  reg [3:0]  ts_line [0:1];
  reg [1:0]  ts_want [0:1];
  reg [11:0] last    [0:1];   // what this context saw at its previous slot, for edge waits

  reg        prim;            // first cycle of a run: only fetches
  reg        cur;
  reg [11:0] eng;
  reg [23:0] src;

  wire c = cur;

  // Only decode memory output while executing. It's undefined before the first read, and this keeps
  // that from reaching any logic, on silicon as well as in gate-level simulation.
  wire [15:0] ins = (running && !prim) ? mem_data : 16'd0;

  // Lines 12-15 don't exist: they read as 1 and ignore writes.
  wire [15:0] lines = {4'hF, lines_in};
  wire [15:0] plines = {4'hF, lines_prev};

  // ------------------------------------------------------------------ Tick sources, every cycle
  wire [1:0] tick_now;
  genvar g;
  generate
    for (g = 0; g < 2; g = g + 1) begin : ticks
      wire edge_seen = lines[ts_line[g]] != plines[ts_line[g]];
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
  wire is_rand   = ins[15:6] == 10'b1110010100;
  wire is_shl    = ins[15:6] == 10'b1110010101;
  wire is_shr    = ins[15:6] == 10'b1110010110;

  wire is_bit = is_outb || is_inb;
  wire tick_q = ((is_set || is_out) && ins[5]) || (is_mode && ins[4]) || ((is_in || is_bit) && ins[6])
             || (is_nop && ins[7]) || (is_probe && ins[3]);
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
  wire [15:0] opnd_cmp = operand(ins[7:5], r0_c, r1_c, r2_c, r3_c, sr_c, bc_c, to_c);  // CMP a, TSETR

  wire        sbit = msb[c] ? sr[c][15] : sr[c][0];
  wire [15:0] sr_shifted_out = msb[c] ? {sr[c][14:0], 1'b0} : {1'b0, sr[c][15:1]};
  wire        in_bit = lines[ln_a];
  wire [15:0] sr_shifted_in = msb[c] ? {sr[c][14:0], in_bit} : {in_bit, sr[c][15:1]};
  wire [4:0]  bc_dec = (bc[c] == 0) ? 5'd0 : bc[c] - 5'd1;

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
      3'd5: take_jmp = bc[c] == 0;
      3'd6: take_jmp = bc[c] != 0;
      default: take_jmp = 1'b0;
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
                      : to_back   ? pc_back
                      :             pc_inc;
  wire [8:0] pc_next = ex ? pc_taken : pc[c];

  assign fetch_addr = prim ? pc[0] : (two_ctx ? pc[!c] : pc_next);

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
  wire [15:0] lfsr_nx = {1'b0, lfsr[c][15:1]} ^ (lfsr[c][0] ? 16'hB400 : 16'h0000);
  wire [15:0] mov_val = opnd_mov;
  wire [16:0] phase_half = {2'd0, tp[c][15:1]};

  // Write ports. Each state element takes one data word selected by AND-OR, rather than one path per
  // instruction, which would give every bit of the register file its own mux tree.
  wire [15:0] ldi_reg = rr[{c, rn_ldi}];
  wire [15:0] alu_reg = rr[{c, rn_alu}];
  wire [15:0] sft_reg = rr[{c, rn_sft}];
  wire        is_shift = is_shr || is_shl;
  wire        mov_rr = is_mov && !ins[7];

  wire rr_we = ex && (is_ldi || is_add || is_addr || is_xor || is_and || is_shift || is_rand || is_djnz || mov_rr);
  wire [1:0] rr_idx = ({2{is_ldi}} & rn_ldi) | ({2{is_add}} & rn_add) | ({2{is_xor || is_and || is_addr}} & rn_alu)
                    | ({2{is_shift || is_rand}} & rn_sft) | ({2{is_djnz}} & rn_djnz) | ({2{mov_rr}} & ins[6:5]);
  wire [15:0] rr_wd = ({16{is_ldi}} & (ins[9] ? {ins[8:1], ldi_reg[7:0]} : {8'd0, ins[8:1]}))
                    | ({16{is_add}} & alu_add)
                    | ({16{is_addr}} & alu_addr)
                    | ({16{is_xor}} & alu_xor)
                    | ({16{is_and}} & alu_and)
                    | ({16{is_shr}} & {1'b0, sft_reg[15:1]})
                    | ({16{is_shl}} & {sft_reg[14:0], 1'b0})
                    | ({16{is_rand}} & lfsr_nx)
                    | ({16{is_djnz}} & djnz_dec)
                    | ({16{mov_rr}} & mov_val);

  wire z_we = is_add || is_addr || is_xor || is_and || is_cmp || is_cmpi || is_tstb || is_probe;
  wire z_wd = (is_add && alu_add == 0) || (is_addr && alu_addr == 0) || (is_xor && alu_xor == 0) || (is_and && alu_and == 0)
           || (is_cmp && opnd_cmp == opnd_mov) || (is_cmpi && add_reg == {8'd0, ins[7:0]})
           || (is_tstb && sbit) || (is_probe && lines[ln_p]);

  wire f_we = is_pull || (is_push && e2h_full) || is_shift;
  wire f_wd = (is_pull && h2e_empty) || is_push || (is_shr && sft_reg[0]) || (is_shl && sft_reg[15]);

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

  wire [15:0] stall_wd = is_dly ? {4'd0, ins[11:0]} : is_dlyr ? alu_reg : {12'd0, dly_field};

  integer i;
  always @(posedge clk) begin
    if (rst) begin
      running <= start;
      prim <= start;
      cur <= 1'b0;
      halted <= 2'b00;
      irq <= 1'b0;
      trig <= 1'b0;
      eng <= 12'h0FF;
      src <= 24'd0;
      for (i = 0; i < 2; i = i + 1) begin
        pc[i] <= i == 0 ? 9'd0 : ctx1_pc;
        lr[i] <= 0;
        sr[i] <= 0;
        bc[i] <= 0;
        to[i] <= 0;
        to_left[i] <= 0;
        to_act[i] <= 0;
        stall[i] <= 0;
        z[i] <= 0;
        f[i] <= 0;
        msb[i] <= 0;
        side_ln[i] <= 4'd15;
        lfsr[i] <= 16'hACE1;
        tp[i] <= 0;
        tcnt[i] <= 0;
        pending[i] <= 0;
        ts_line[i] <= 0;
        ts_want[i] <= 2'd3;
        last[i] <= 12'hFFF;
      end
      for (i = 0; i < 8; i = i + 1) rr[i] <= 0;
    end else begin
      trig <= 1'b0;
      eng <= eng_nx;
      src <= src_nx;

      // Timers run on every clock, for both contexts.
      for (i = 0; i < 2; i = i + 1) begin
        if (ts_want[i] == 2'd3 && tp[i] != 0)
          tcnt[i] <= tcnt[i] <= 17'd1 ? {1'b0, tp[i]} : tcnt[i] - 17'd1;
        if (tick_now[i]) pending[i] <= 1'b1;
      end

      if (prim) begin
        prim <= 1'b0;
        last[0] <= lines_in;
        last[1] <= lines_in;
      end else if (running) begin
        last[c] <= lines_in;
        if (two_ctx) cur <= !cur;

        if (!halted[c]) begin
          if (stall[c] != 0) begin
            stall[c] <= stall[c] - 16'd1;
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
              if (is_msb) msb[c] <= ins[7];
              if (is_side) side_ln[c] <= ln_p;
              if (is_mov && ins[7:5] == 3'd6) to[c] <= mov_val;
              if (is_rand || (is_mov && ins[7:5] == 3'd7)) lfsr[c] <= is_rand ? lfsr_nx : mov_val;
              if (is_call) lr[c] <= pc_inc;
              if (is_irq) irq <= 1'b1;
              if (is_trig) trig <= 1'b1;
              if (is_halt) halted[c] <= 1'b1;
            end
          end
        end
      end

      // A missed tick is visible next cycle and beats any instruction writing F, so a deadline
      // miss can never be silently lost. This must stay after the instruction logic.
      for (i = 0; i < 2; i = i + 1)
        if (tick_now[i] && pending[i]) f[i] <= 1'b1;
    end
  end
endmodule
