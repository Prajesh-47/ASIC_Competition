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

module tt_um_prajesh47_splice (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
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
  wire [2:0]  reg_addr;
  wire [7:0]  reg_wdata;
  reg  [7:0]  reg_rdata;
  wire        host_push, host_pop, h2e_full, e2h_empty;
  wire [15:0] host_wdata, e2h_head;
  wire        spi_miso;

  wire        running, irq, trig;
  wire [1:0]  halted;
  wire        h2e_empty, h2e_pop, e2h_full, e2h_push;
  wire [15:0] h2e_head, e2h_data;

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
      .fifo_empty(e2h_empty)
  );

  // Host registers: 0 mode, 1 open-drain lines, 2 fast pass-through lines, 3 bridge holdoff,
  // 4 and 5 context 1's start address.
  reg       two_ctx;
  reg [7:0] odrain, fast, holdoff;
  reg [8:0] ctx1_pc;
  always @(posedge clk) begin
    if (!rst_n) begin
      two_ctx <= 1'b0;
      odrain <= 8'd0;
      fast <= 8'd0;
      holdoff <= 8'd64;
      ctx1_pc <= 9'd0;
    end else if (reg_we) begin
      case (reg_addr)
        3'd0: two_ctx <= reg_wdata[0];
        3'd1: odrain <= reg_wdata;
        3'd2: fast <= reg_wdata;
        3'd3: holdoff <= reg_wdata;
        3'd4: ctx1_pc[8] <= reg_wdata[0];
        3'd5: ctx1_pc[7:0] <= reg_wdata;
        default: ;
      endcase
    end
  end
  always @* begin
    case (reg_addr)
      3'd0: reg_rdata = {7'd0, two_ctx};
      3'd1: reg_rdata = odrain;
      3'd2: reg_rdata = fast;
      3'd3: reg_rdata = holdoff;
      3'd4: reg_rdata = {7'd0, ctx1_pc[8]};
      3'd5: reg_rdata = ctx1_pc[7:0];
      default: reg_rdata = 8'd0;
    endcase
  end

  // ------------------------------------------------------------------ FIFOs, cleared with a run
  wire engine_rst = !rst_n || start || stop;

  splice_fifo h2e (
      .clk(clk), .clear(!rst_n || stop), .push(host_push), .din(host_wdata), .full(h2e_full),
      .pop(h2e_pop), .head(h2e_head), .empty(h2e_empty));

  splice_fifo e2h (
      .clk(clk), .clear(engine_rst), .push(e2h_push), .din(e2h_data), .full(e2h_full),
      .pop(host_pop), .head(e2h_head), .empty(e2h_empty));

  // ------------------------------------------------------------------ Engine
  wire [8:0]  fetch_addr;
  wire [15:0] prog_dout;
  wire [11:0] eng_nx;
  wire [23:0] src_nx;

  splice_engine engine (
      .clk       (clk),
      .rst       (engine_rst),
      .start     (start && rst_n),
      .two_ctx   (two_ctx),
      .ctx1_pc   (ctx1_pc),
      .ins       (prog_dout),
      .fetch_addr(fetch_addr),
      .lines_in  (sync2),
      .lines_prev(sync_prev),
      .eng_nx    (eng_nx),
      .src_nx    (src_nx),
      .h2e_head  (h2e_head),
      .h2e_empty (h2e_empty),
      .h2e_pop   (h2e_pop),
      .e2h_data  (e2h_data),
      .e2h_full  (e2h_full),
      .e2h_push  (e2h_push),
      .running   (running),
      .halted    (halted),
      .irq       (irq),
      .trig      (trig)
  );

  // ------------------------------------------------------------------ Line fabric
  // Reset state: every line is released, so SPLICE is invisible on a bus until it's configured.
  reg [7:0] line_out, line_oe;
  wire [7:0] partner = {sync2[3:0], sync2[7:4]};
  wire [7:0] partner_fast = {sync1[3:0], sync1[7:4]};
  wire [7:0] level = (fast & partner_fast) | (~fast & partner);
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
          default: {line_oe[x], line_out[x]} <= 2'b00;  // HIZ, and the bridge until it's built
        endcase
      end
    end
  end

  assign uio_out = line_out;
  assign uio_oe  = line_oe;

  // ------------------------------------------------------------------ Memories
  // Each macro has its own registers, so no long bus runs between them.
  wire host_prog = !running && mem_req && !mem_cap;
  wire host_cap  = !running && mem_req && mem_cap;

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

  // Host access only, until the capture unit is built.
  RM_IHPSG13_1P_512x16_c2_bm_bist cap_sram (
      .A_CLK      (clk),
      .A_MEN      (host_cap),
      .A_WEN      (host_cap && mem_we),
      .A_REN      (host_cap && !mem_we),
      .A_ADDR     (mem_addr),
      .A_DIN      (mem_wdata),
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

  wire _unused = &{ena, ui_in[3], holdoff, 1'b0};

endmodule
