/*
 * SPLICE — programmable bus interposer
 * Copyright (c) 2026 Prajesh-47
 * SPDX-License-Identifier: Apache-2.0
 *
 * Top level. Pin functions follow docs/architecture.md section 3.
 *
 *   ui[0] SPI SCK   ui[1] SPI MOSI   ui[2] SPI CS_n   ui[3] TRIG_IN
 *   ui[7:4] AUX_IN[3:0]
 *   uo[0] SPI MISO  uo[1] IRQ        uo[2] TRIG_OUT   uo[3] ACTIVE/heartbeat
 *   uo[7:4] AUX_OUT[3:0]
 *   uio[3:0] lines L0-L3 (A side)    uio[7:4] lines L4-L7 (B side)
 */

`default_nettype none

// Host FIFO depth, as log2 of words in each direction.
`ifndef SPLICE_FIFO_LOG2
`define SPLICE_FIFO_LOG2 3
`endif

module tt_um_prajesh47_splice (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    // verilator lint_off SYNCASYNCNET
    input  wire       rst_n     // reset_n - low to reset
    // verilator lint_on SYNCASYNCNET
);

  // ------------------------------------------------------------------ Inputs
  // Lines 0-7 are the uio pins and lines 8-11 are AUX_IN. Two flops, then the previous value.
  wire [11:0] pins_in = {ui_in[7:4], uio_in};
  reg  [11:0] sync1, sync2, sync_prev;
  always @(posedge clk) begin
    sync1 <= pins_in;
    sync2 <= sync1;
    sync_prev <= sync2;
  end

  // ------------------------------------------------------------------ Host
  wire        start, stop, mem_req, mem_we, mem_cap, reg_we;
  wire [8:0]  mem_addr;
  wire [15:0] mem_wdata, mem_rdata;
  wire [4:0]  reg_addr;
  wire [7:0]  reg_wdata;
  reg  [7:0]  reg_rdata;
  wire        host_push, host_pop, h2e_full, e2h_empty;
  wire [15:0] host_wdata, e2h_head;
  wire        spi_miso;

  wire        running, irq, trig;
  wire        engine_cap_on, engine_cap_off, engine_cap_mark;
  wire [1:0]  halted;
  wire        h2e_empty, e2h_full;
  wire [15:0] h2e_head, e2h_data;
  wire [7:0]  e2h_level, h2e_level;
  localparam [8:0] FIFO_WORDS = 9'd1 << `SPLICE_FIFO_LOG2;
  wire [8:0]  h2e_room = FIFO_WORDS - {1'b0, h2e_level};
  wire [7:0]  h2e_free = h2e_room > 9'd255 ? 8'd255 : h2e_room[7:0];
  wire [3:0]  halted_all;
  wire [1:0]  cur_ctx;
  assign halted = halted_all[1:0];

  wire [7:0] status = {2'd0, irq, e2h_empty, h2e_full, halted, running};

  splice_host host (
      .clk       (clk),
      .rst_n     (rst_n),
      .sck_pin   (ui_in[0]),
      .mosi_pin  (ui_in[1]),
      .cs_n_pin  (ui_in[2]),
      .miso      (spi_miso),
      .start     (start),
      .stop      (stop),
      .status    (status),
      .mem_req   (mem_req),
      .mem_we    (mem_we),
      .mem_cap   (mem_cap),
      .mem_addr  (mem_addr),
      .mem_wdata (mem_wdata),
      .mem_rdata (mem_rdata),
      .reg_we    (reg_we),
      .reg_addr  (reg_addr),
      .reg_wdata (reg_wdata),
      .reg_rdata (reg_rdata),
      .fifo_push (host_push),
      .fifo_wdata(host_wdata),
      .fifo_full (h2e_full),
      .fifo_pop  (host_pop),
      .fifo_rdata(e2h_head),
      .fifo_empty(e2h_empty),
      .fifo_level(e2h_level),
      .fifo_free (h2e_free)
  );

  // Host registers: 0 mode, 1 open-drain lines, 2 fast pass-through lines, 3 bridge holdoff,
  // 4 and 5 context 1's start address, 6 and 7 capture's 4 watched lines, 8 and 9 capture prescaler,
  // 10 capture mode and control, 11 and 12 entries kept after a trigger, 13 and 14 capture write
  // pointer (read only). 16 and 17 context 2's start address, 18 and 19 context 3's, 20 where each
  // context's PULL reads from and 21 where its PUSH goes (2 bits per context, context 0 lowest:
  // 0 host, 1 link 0, 2 link 1), 22 every context's halted bit and the links' state (read only).
  // Register 0 holds the number of contexts less one, so writing 1 still means two.
  reg [1:0] nctx_m1;
  reg [8:0] ctx2_pc, ctx3_pc;
  reg [7:0] pull_sel, push_sel;
  reg [7:0] odrain, fast, holdoff;
  reg [8:0] ctx1_pc;
  reg [15:0] cap_watch, cap_prescale;
  reg [2:0]  cap_mode;
  reg [8:0]  cap_post;
  wire       cap_active, cap_wrapped, cap_triggered;
  wire [8:0] cap_ptr;
  wire       host_cap_on = reg_we && reg_addr == 5'd10 && reg_wdata[7];
  wire       host_cap_off = reg_we && reg_addr == 5'd10 && reg_wdata[6];
  always @(posedge clk) begin
    if (!rst_n) begin
      nctx_m1 <= 2'd0;
      ctx2_pc <= 9'd0;
      ctx3_pc <= 9'd0;
      pull_sel <= 8'd0;
      push_sel <= 8'd0;
      odrain <= 8'd0;
      fast <= 8'd0;
      holdoff <= 8'd64;
      ctx1_pc <= 9'd0;
      cap_watch <= 16'h3210;
      cap_prescale <= 16'd0;
      cap_mode <= 3'd0;
      cap_post <= 9'd0;
    end else if (reg_we) begin
      case (reg_addr)
        5'd0: nctx_m1 <= reg_wdata[1:0];
        5'd1: odrain <= reg_wdata;
        5'd2: fast <= reg_wdata;
        5'd3: holdoff <= reg_wdata;
        5'd4: ctx1_pc[8] <= reg_wdata[0];
        5'd5: ctx1_pc[7:0] <= reg_wdata;
        5'd6: cap_watch[7:0] <= reg_wdata;
        5'd7: cap_watch[15:8] <= reg_wdata;
        5'd8: cap_prescale[7:0] <= reg_wdata;
        5'd9: cap_prescale[15:8] <= reg_wdata;
        5'd10: cap_mode <= reg_wdata[2:0];
        5'd11: cap_post[7:0] <= reg_wdata;
        5'd12: cap_post[8] <= reg_wdata[0];
        5'd16: ctx2_pc[8] <= reg_wdata[0];
        5'd17: ctx2_pc[7:0] <= reg_wdata;
        5'd18: ctx3_pc[8] <= reg_wdata[0];
        5'd19: ctx3_pc[7:0] <= reg_wdata;
        5'd20: pull_sel <= reg_wdata;
        5'd21: push_sel <= reg_wdata;
        default: ;
      endcase
    end
  end
  always @* begin
    case (reg_addr)
      5'd0: reg_rdata = {6'd0, nctx_m1};
      5'd1: reg_rdata = odrain;
      5'd2: reg_rdata = fast;
      5'd3: reg_rdata = holdoff;
      5'd4: reg_rdata = {7'd0, ctx1_pc[8]};
      5'd5: reg_rdata = ctx1_pc[7:0];
      5'd6: reg_rdata = cap_watch[7:0];
      5'd7: reg_rdata = cap_watch[15:8];
      5'd8: reg_rdata = cap_prescale[7:0];
      5'd9: reg_rdata = cap_prescale[15:8];
      5'd10: reg_rdata = {cap_active, cap_wrapped, cap_triggered, 2'd0, cap_mode};
      5'd11: reg_rdata = cap_post[7:0];
      5'd12: reg_rdata = {7'd0, cap_post[8]};
      5'd13: reg_rdata = cap_ptr[7:0];
      5'd14: reg_rdata = {7'd0, cap_ptr[8]};
      5'd16: reg_rdata = {7'd0, ctx2_pc[8]};
      5'd17: reg_rdata = ctx2_pc[7:0];
      5'd18: reg_rdata = {7'd0, ctx3_pc[8]};
      5'd19: reg_rdata = ctx3_pc[7:0];
      5'd20: reg_rdata = pull_sel;
      5'd21: reg_rdata = push_sel;
      5'd22: reg_rdata = {l1_full, l1_empty, l0_full, l0_empty, halted_all};
      default: reg_rdata = 8'd0;
    endcase
  end

  // ------------------------------------------------------------------ FIFOs, cleared with a run
  wire engine_rst = !rst_n || start || stop;

  // The engine sees one FIFO pair, routed per context to the host or to a link between contexts.
  wire        eng_empty, eng_full, eng_pop, eng_push;
  wire [15:0] eng_head;
  wire [1:0]  pull_from = pull_sel[2 * cur_ctx +: 2];
  wire [1:0]  push_to = push_sel[2 * cur_ctx +: 2];
  wire        l0_full, l0_empty, l1_full, l1_empty;
  wire [15:0] l0_head, l1_head;

  splice_fifo #(.DEPTH_LOG2(`SPLICE_FIFO_LOG2)) h2e (
      .clk(clk), .rst_n(rst_n), .clear(!rst_n || stop), .push(host_push), .din(host_wdata), .full(h2e_full),
      .pop(eng_pop && pull_from != 2'd1 && pull_from != 2'd2), .head(h2e_head), .empty(h2e_empty), .level(h2e_level));

  splice_fifo #(.DEPTH_LOG2(`SPLICE_FIFO_LOG2)) e2h (
      .clk(clk), .rst_n(rst_n), .clear(engine_rst), .push(eng_push && push_to != 2'd1 && push_to != 2'd2),
      .din(e2h_data), .full(e2h_full), .pop(host_pop), .head(e2h_head), .empty(e2h_empty), .level(e2h_level));

  // Links: one context's PUSH feeds another's PULL, with no host in between. Their words are latches.
  splice_fifo #(.LATCH(1)) link0 (
      .clk(clk), .rst_n(rst_n), .clear(engine_rst), .push(eng_push && push_to == 2'd1), .din(e2h_data),
      .full(l0_full), .pop(eng_pop && pull_from == 2'd1), .head(l0_head), .empty(l0_empty), .level());

  splice_fifo #(.LATCH(1)) link1 (
      .clk(clk), .rst_n(rst_n), .clear(engine_rst), .push(eng_push && push_to == 2'd2), .din(e2h_data),
      .full(l1_full), .pop(eng_pop && pull_from == 2'd2), .head(l1_head), .empty(l1_empty), .level());

  assign eng_head  = pull_from == 2'd1 ? l0_head : pull_from == 2'd2 ? l1_head : h2e_head;
  assign eng_empty = pull_from == 2'd1 ? l0_empty : pull_from == 2'd2 ? l1_empty : h2e_empty;
  assign eng_full  = push_to == 2'd1 ? l0_full : push_to == 2'd2 ? l1_full : e2h_full;

  // ------------------------------------------------------------------ Engine
  wire [8:0]  fetch_addr;
  wire [15:0] prog_dout;
  wire [11:0] eng_nx;
  wire [23:0] src_nx;

  splice_engine engine (
      .clk       (clk),
      .rst       (engine_rst),
      .start     (start && rst_n),
      .nctx_m1   (nctx_m1),
      .ctx_pc    ({ctx3_pc, ctx2_pc, ctx1_pc}),
      .cur_ctx   (cur_ctx),
      .mem_data  (prog_dout),
      .fetch_addr(fetch_addr),
      .lines_in  (sync2),
      .lines_prev(sync_prev),
      .eng_nx    (eng_nx),
      .src_nx    (src_nx),
      .h2e_head  (eng_head),
      .h2e_empty (eng_empty),
      .h2e_pop   (eng_pop),
      .e2h_data  (e2h_data),
      .e2h_full  (eng_full),
      .e2h_push  (eng_push),
      .running   (running),
      .halted    (halted_all),
      .irq       (irq),
      .trig      (trig),
      .cap_on    (engine_cap_on),
      .cap_off   (engine_cap_off),
      .cap_mark  (engine_cap_mark)
  );

  // ------------------------------------------------------------------ Capture
  // STOP ends capture but keeps its results, so the host can read them. A new START clears them.
  reg  [1:0] trig_sync;
  reg        trig_prev;
  always @(posedge clk) begin
    trig_sync <= {trig_sync[0], ui_in[3]};
    trig_prev <= trig_sync[1];
  end

  wire        cap_we;
  wire [8:0]  cap_waddr;
  wire [15:0] cap_wdata;

  splice_capture capture (
      .clk      (clk),
      .rst      (!rst_n || start),
      .lines    (sync2),
      .trig_rise(trig_sync[1] && !trig_prev && running),
      .watch    (cap_watch),
      .prescale (cap_prescale),
      .mode     (cap_mode),
      .post     (cap_post),
      .on       (engine_cap_on || host_cap_on),
      .off      (engine_cap_off || host_cap_off || stop),
      .mark     (engine_cap_mark),
      .we       (cap_we),
      .waddr    (cap_waddr),
      .wdata    (cap_wdata),
      .active   (cap_active),
      .wrapped  (cap_wrapped),
      .triggered(cap_triggered),
      .ptr      (cap_ptr)
  );

  // ------------------------------------------------------------------ Line fabric
  // Reset state: every line is released, so SPLICE is invisible on a bus until it's configured.
  reg [7:0] line_out, line_oe;
  wire [7:0] partner = {sync2[3:0], sync2[7:4]};
  wire [7:0] partner_fast = {sync1[3:0], sync1[7:4]};
  wire [7:0] level = (fast & partner_fast) | (~fast & partner);

  // One open-drain bridge per pair, active when either side's source selects it.
  wire [3:0] bridge_low_a, bridge_low_b;
  genvar p;
  generate
    for (p = 0; p < 4; p = p + 1) begin : pair
      splice_bridge bridge (
          .clk     (clk),
          .rst     (engine_rst),
          .enable  (src_nx[3 * p +: 3] == 3'd6 || src_nx[3 * (p + 4) +: 3] == 3'd6),
          .line_a  (sync2[p]),
          .line_b  (sync2[p + 4]),
          .holdoff (holdoff),
          .low_a_nx(bridge_low_a[p]),
          .low_b_nx(bridge_low_b[p])
      );
    end
  endgenerate
  wire [7:0] bridge_low = {bridge_low_b, bridge_low_a};

  integer x;
  always @(posedge clk) begin
    if (engine_rst) begin
      line_out <= 8'd0;
      line_oe <= 8'd0;
    end else begin
      for (x = 0; x < 8; x = x + 1) begin
        case (src_nx[3 * x +: 3])
          3'd1: {line_oe[x], line_out[x]} <= {!(eng_nx[x] && odrain[x]), eng_nx[x] && !odrain[x]};
          3'd2: {line_oe[x], line_out[x]} <= {!(level[x] && odrain[x]), level[x] && !odrain[x]};
          3'd3: {line_oe[x], line_out[x]} <= {!(!level[x] && odrain[x]), !level[x] && !odrain[x]};
          3'd4: {line_oe[x], line_out[x]} <= 2'b10;
          3'd5: {line_oe[x], line_out[x]} <= {!odrain[x], !odrain[x]};
          3'd6: {line_oe[x], line_out[x]} <= {bridge_low[x], 1'b0};
          default: {line_oe[x], line_out[x]} <= 2'b00;  // HIZ
        endcase
      end
    end
  end

  assign uio_out = line_out;
  assign uio_oe  = line_oe;

  // ------------------------------------------------------------------ Memories
  // Each macro has its own registers, so no long bus runs between them.
  wire host_prog = !running && mem_req && !mem_cap;
  wire host_cap  = !cap_active && !cap_we && mem_req && mem_cap;

  wire [15:0] cap_dout;

  RM_IHPSG13_1P_256x16_c2_bm_bist prog_sram (
      .A_CLK      (clk),
      .A_MEN      (running || host_prog),
      .A_WEN      (host_prog && mem_we),
      .A_REN      (running || (host_prog && !mem_we)),
      .A_ADDR     (running ? fetch_addr[7:0] : mem_addr[7:0]),
      .A_DIN      (mem_wdata),
      .A_DLY      (1'b1),
      .A_DOUT     (prog_dout),
      .A_BM       (16'hFFFF),
      .A_BIST_CLK (1'b0),
      .A_BIST_EN  (1'b0),
      .A_BIST_MEN (1'b0),
      .A_BIST_WEN (1'b0),
      .A_BIST_REN (1'b0),
      .A_BIST_ADDR(8'd0),
      .A_BIST_DIN (16'd0),
      .A_BIST_BM  (16'd0)
  );

  // The capture unit has priority. The host can read results once capture has stopped.
  RM_IHPSG13_1P_512x16_c2_bm_bist cap_sram (
      .A_CLK      (clk),
      .A_MEN      (cap_we || host_cap),
      .A_WEN      (cap_we || (host_cap && mem_we)),
      .A_REN      (host_cap && !mem_we),
      .A_ADDR     (cap_we ? cap_waddr : mem_addr),
      .A_DIN      (cap_we ? cap_wdata : mem_wdata),
      .A_DLY      (1'b1),
      .A_DOUT     (cap_dout),
      .A_BM       (16'hFFFF),
      .A_BIST_CLK (1'b0),
      .A_BIST_EN  (1'b0),
      .A_BIST_MEN (1'b0),
      .A_BIST_WEN (1'b0),
      .A_BIST_REN (1'b0),
      .A_BIST_ADDR(9'd0),
      .A_BIST_DIN (16'd0),
      .A_BIST_BM  (16'd0)
  );

  assign mem_rdata = mem_cap ? cap_dout : prog_dout;

  // ------------------------------------------------------------------ Status pins
  reg [3:0] aux_q;
  always @(posedge clk) aux_q <= engine_rst ? 4'd0 : eng_nx[11:8];

  assign uo_out = {aux_q, running, trig, irq, spi_miso};

  wire _unused = &{ena, ui_in[3], 1'b0};

endmodule
