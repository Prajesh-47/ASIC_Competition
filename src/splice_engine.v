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
  reg [12:0] tp      [0:1];
  reg [13:0] tcnt    [0:1];
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
          : (tp[g] != 0 && tcnt[g] <= 14'd1);
    end
  endgenerate

  // ------------------------------------------------------------------ Decode
  localparam [5:0]
      O_NOP = 0, O_TSET = 1, O_JMP = 2, O_CALL = 3, O_DLY = 4, O_LDI = 5, O_SRI = 6, O_SET = 7,
      O_MODE = 8, O_OUT = 9, O_IN = 10, O_OUTB = 11, O_INB = 12, O_DJNZ = 13, O_WAIT = 14, O_ADD = 15,
      O_CMPI = 16, O_AND = 17, O_BCI = 18, O_CMP = 19, O_DLYR = 20, O_HALT = 21, O_IRQ = 22, O_MOV = 23,
      O_MSB = 24, O_PROBE = 25, O_PULL = 26, O_PULLW = 27, O_PUSH = 28, O_RET = 29, O_SIDE = 30,
      O_TPHASE = 31, O_TRIG = 32, O_TSRC = 33, O_TSTB = 34, O_WAITT = 35, O_XOR = 36, O_RAND = 37,
      O_SHL = 38, O_SHR = 39, O_CAPMARK = 40, O_CAPOFF = 41, O_CAPON = 42;

  reg [5:0]  op;
  reg        tick_q;
  reg [1:0]  side;
  reg [3:0]  dly_field;

  always @* begin
    op = O_NOP;
    tick_q = 1'b0;
    side = 2'b00;
    dly_field = 4'd0;
    casez (ins)
      16'b000?????????????: op = O_TSET;
      16'b0010????????????: op = O_JMP;
      16'b0011????????????: op = O_CALL;
      16'b0100????????????: op = O_DLY;
      16'b0101????????????: op = O_LDI;
      16'b0110????????????: op = O_SRI;
      16'b01110???????????: begin
        op = O_SET;
        tick_q = ins[5];
        side = ins[4:3];
        dly_field = {1'b0, ins[2:0]};
      end
      16'b01111???????????: begin
        op = O_MODE;
        tick_q = ins[4];
        dly_field = ins[3:0];
      end
      16'b10000???????????: begin
        op = O_OUT;
        tick_q = ins[5];
        side = ins[4:3];
        dly_field = {1'b0, ins[2:0]};
      end
      16'b10001???????????: begin
        op = O_IN;
        tick_q = ins[6];
        side = ins[5:4];
        dly_field = ins[3:0];
      end
      16'b10010???????????: begin
        op = O_OUTB;
        tick_q = ins[6];
        side = ins[5:4];
      end
      16'b10011???????????: begin
        op = O_INB;
        tick_q = ins[6];
        side = ins[5:4];
      end
      16'b10100???????????: op = O_DJNZ;
      16'b101110??????????: op = O_WAIT;
      16'b101111??????????: op = O_ADD;
      16'b110000??????????: op = O_CMPI;
      16'b11001101????????: op = O_AND;
      16'b11001110????????: op = O_BCI;
      16'b11001111????????: op = O_CAPMARK;
      16'b11010000????????: op = O_CAPOFF;
      16'b11010001????????: op = O_CAPON;
      16'b11010010????????: op = O_CMP;
      16'b11010011????????: op = O_DLYR;
      16'b11010100????????: op = O_HALT;
      16'b11010101????????: op = O_IRQ;
      16'b11010110????????: op = O_MOV;
      16'b11010111????????: op = O_MSB;
      16'b11011000????????: begin
        op = O_NOP;
        tick_q = ins[7];
        side = ins[6:5];
      end
      16'b11011001????????: begin
        op = O_PROBE;
        tick_q = ins[3];
      end
      16'b11011010????????: op = O_PULL;
      16'b11011011????????: op = O_PULLW;
      16'b11011100????????: op = O_PUSH;
      16'b11011101????????: op = O_RET;
      16'b11011110????????: op = O_SIDE;
      16'b11011111????????: op = O_TPHASE;
      16'b11100000????????: op = O_TRIG;
      16'b11100001????????: op = O_TSRC;
      16'b11100010????????: op = O_TSTB;
      16'b11100011????????: op = O_WAITT;
      16'b11100100????????: op = O_XOR;
      16'b1110010100??????: op = O_RAND;
      16'b1110010101??????: op = O_SHL;
      16'b1110010110??????: op = O_SHR;
      default: op = O_NOP;  // reserved encodings do nothing
    endcase
  end

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

  wire [15:0] opnd_alu = operand(ins[5:3], r0_c, r1_c, r2_c, r3_c, sr_c, bc_c, to_c);  // AND, XOR
  wire [15:0] opnd_mov = operand(ins[4:2], r0_c, r1_c, r2_c, r3_c, sr_c, bc_c, to_c);  // MOV, CMP b
  wire [15:0] opnd_cmp = operand(ins[7:5], r0_c, r1_c, r2_c, r3_c, sr_c, bc_c, to_c);  // CMP a

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
  wire        wait_block = op == O_WAIT && !wait_met && !to_expired;

  wire blocked = wait_block
              || (op == O_WAITT && !pend_eff)
              || (op == O_PULLW && h2e_empty);
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
  wire [8:0]  pc_back = pc[c] - {5'd0, ins[3:0]};

  reg [8:0] pc_next;
  always @* begin
    pc_next = pc_inc;
    case (op)
      O_JMP:  if (take_jmp) pc_next = ins[8:0];
      O_CALL: pc_next = ins[11:3];
      O_RET:  pc_next = lr[c];
      O_DJNZ: if (djnz_dec != 0) pc_next = ins[8:0];
      O_OUTB, O_INB: if (bc_dec != 0) pc_next = pc_back;
      default: ;
    endcase
    if (!ex) pc_next = pc[c];
  end

  assign fetch_addr = prim ? pc[0] : (two_ctx ? pc[!c] : pc_next);

  // ------------------------------------------------------------------ Lines
  always @* begin
    eng_nx = eng;
    src_nx = src;
    if (active && side[1] && side_ln[c] < 4'd12) eng_nx[side_ln[c][3:0]] = side[0];
    if (ex) begin
      if (op == O_SET && ln_a < 4'd12) eng_nx[ln_a] = ins[6];
      if ((op == O_OUT || op == O_OUTB) && ln_a < 4'd12) eng_nx[ln_a] = sbit;
      if (op == O_MODE) src_nx[3 * ins[10:8] +: 3] = ins[7:5];
    end
  end

  // ------------------------------------------------------------------ FIFOs
  assign h2e_pop = ex && (op == O_PULLW || (op == O_PULL && !h2e_empty));
  assign e2h_push = ex && op == O_PUSH;
  assign e2h_data = sr[c];
  assign cap_on = ex && op == O_CAPON;
  assign cap_off = ex && op == O_CAPOFF;
  assign cap_mark = ex && op == O_CAPMARK;

  // ------------------------------------------------------------------ Clocked state
  wire [15:0] alu_add = rr[{c, rn_add}] + {8'd0, ins[7:0]};
  wire [15:0] alu_xor = rr[{c, rn_alu}] ^ opnd_alu;
  wire [15:0] alu_and = rr[{c, rn_alu}] & opnd_alu;
  wire [15:0] lfsr_nx = {1'b0, lfsr[c][15:1]} ^ (lfsr[c][0] ? 16'hB400 : 16'h0000);
  wire [15:0] mov_val = opnd_mov;
  wire [13:0] phase_half = {2'd0, tp[c][12:1]};

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
          tcnt[i] <= tcnt[i] <= 14'd1 ? {1'b0, tp[i]} : tcnt[i] - 14'd1;
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
            if (op == O_WAIT && !halted[c]) begin
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
              stall[c] <= {12'd0, dly_field};
              case (op)
                O_OUT: if (!ins[6]) begin
                  sr[c] <= sr_shifted_out;
                  bc[c] <= bc_dec;
                end
                O_OUTB: begin
                  sr[c] <= sr_shifted_out;
                  bc[c] <= bc_dec;
                end
                O_IN, O_INB: begin
                  sr[c] <= sr_shifted_in;
                  bc[c] <= bc_dec;
                end
                O_WAITT: pending[c] <= 1'b0;
                O_TSET: begin
                  tp[c] <= ins[12:0];
                  tcnt[c] <= {1'b0, ins[12:0]};
                  pending[c] <= 1'b0;
                end
                O_TPHASE: begin
                  case (ins[7:6])
                    2'd0: tcnt[c] <= {1'b0, tp[c]} - 14'd1;
                    2'd1: tcnt[c] <= phase_half - 14'd1;
                    2'd2: tcnt[c] <= {1'b0, tp[c]} + phase_half - 14'd1;
                    default: tcnt[c] <= rr[{c, 2'd1}][13:0];
                  endcase
                  pending[c] <= 1'b0;
                end
                O_TSRC: begin
                  ts_line[c] <= ln_p;
                  ts_want[c] <= ins[3:2];
                  pending[c] <= 1'b0;
                end
                O_DLY: stall[c] <= {4'd0, ins[11:0]};
                O_DLYR: stall[c] <= rr[{c, rn_alu}];
                O_PULL: begin
                  if (!h2e_empty) begin
                    sr[c] <= h2e_head;
                    f[c] <= 1'b0;
                  end else f[c] <= 1'b1;
                end
                O_PULLW: begin
                  sr[c] <= h2e_head;
                  bc[c] <= ins[7:3];
                end
                O_PUSH: if (e2h_full) f[c] <= 1'b1;
                O_BCI: bc[c] <= ins[7:3];
                O_SRI: sr[c] <= {8'd0, ins[11:4]} << ins[3:0];
                O_MSB: msb[c] <= ins[7];
                O_SIDE: side_ln[c] <= ln_p;
                O_LDI: rr[{c, rn_ldi}] <= ins[9] ? {ins[8:1], rr[{c, rn_ldi}][7:0]} : {8'd0, ins[8:1]};
                O_MOV: begin
                  case (ins[7:5])
                    3'd4: sr[c] <= mov_val;
                    3'd5: bc[c] <= mov_val[4:0];
                    3'd6: to[c] <= mov_val;
                    3'd7: lfsr[c] <= mov_val;
                    default: rr[{c, ins[6:5]}] <= mov_val;
                  endcase
                end
                O_ADD: begin
                  rr[{c, rn_add}] <= alu_add;
                  z[c] <= alu_add == 0;
                end
                O_XOR: begin
                  rr[{c, rn_alu}] <= alu_xor;
                  z[c] <= alu_xor == 0;
                end
                O_AND: begin
                  rr[{c, rn_alu}] <= alu_and;
                  z[c] <= alu_and == 0;
                end
                O_CMP: z[c] <= opnd_cmp == opnd_mov;
                O_CMPI: z[c] <= rr[{c, rn_add}] == {8'd0, ins[7:0]};
                O_SHR: begin
                  f[c] <= rr[{c, rn_sft}][0];
                  rr[{c, rn_sft}] <= {1'b0, rr[{c, rn_sft}][15:1]};
                end
                O_SHL: begin
                  f[c] <= rr[{c, rn_sft}][15];
                  rr[{c, rn_sft}] <= {rr[{c, rn_sft}][14:0], 1'b0};
                end
                O_RAND: begin
                  lfsr[c] <= lfsr_nx;
                  rr[{c, rn_sft}] <= lfsr_nx;
                end
                O_TSTB: z[c] <= sbit;
                O_PROBE: z[c] <= lines[ln_p];
                O_DJNZ: rr[{c, rn_djnz}] <= djnz_dec;
                O_CALL: lr[c] <= pc_inc;
                O_IRQ: irq <= 1'b1;
                O_TRIG: trig <= 1'b1;
                O_HALT: halted[c] <= 1'b1;
                default: ;
              endcase
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
