/*
 * SPLICE — programmable bus interposer
 * Copyright (c) 2026 Prajesh-47
 * SPDX-License-Identifier: Apache-2.0
 *
 * Top level. Stub: proves the RTL -> GDS flow on IHP CMOS5L, with both SRAM macros.
 * Pin functions follow docs/architecture.md section 3.
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

  // Heartbeat: bit 23 of a free-running counter (~3 Hz at 50 MHz).
  // Gives the flow some real flops + a clock tree until the engine lands.
  reg [23:0] hb_cnt;
  always @(posedge clk) begin
    if (!rst_n) hb_cnt <= 24'd0;
    else        hb_cnt <= hb_cnt + 24'd1;
  end

  // SRAM macros: both macros from docs/architecture.md section 7,
  // exercised by a self-test. Until the engine lands, IRQ = test done and
  // TRIG_OUT = test passed.
  wire        prog_men, prog_wen, prog_ren;
  wire        cap_men, cap_wen, cap_ren;
  wire [7:0]  prog_addr;
  wire [8:0]  cap_addr;
  wire [15:0] prog_din, cap_din, prog_dout, cap_dout;
  wire        test_done, test_pass;

  splice_sram_selftest selftest (
      .clk      (clk),
      .rst_n    (rst_n),
      .prog_men (prog_men),
      .prog_wen (prog_wen),
      .prog_ren (prog_ren),
      .prog_dout(prog_dout),
      .cap_men  (cap_men),
      .cap_wen  (cap_wen),
      .cap_ren  (cap_ren),
      .cap_dout (cap_dout),
      .prog_addr(prog_addr),
      .prog_din (prog_din),
      .cap_addr (cap_addr),
      .cap_din  (cap_din),
      .done     (test_done),
      .pass     (test_pass)
  );

  RM_IHPSG13_1P_256x16_c2_bm_bist prog_sram (
      .A_CLK      (clk),
      .A_MEN      (prog_men),
      .A_WEN      (prog_wen),
      .A_REN      (prog_ren),
      .A_ADDR     (prog_addr),
      .A_DIN      (prog_din),
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

  RM_IHPSG13_1P_512x16_c2_bm_bist cap_sram (
      .A_CLK      (clk),
      .A_MEN      (cap_men),
      .A_WEN      (cap_wen),
      .A_REN      (cap_ren),
      .A_ADDR     (cap_addr),
      .A_DIN      (cap_din),
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

  wire spi_miso = 1'b0;   // TODO: host SPI slave
  wire irq      = test_done;  // Self-test, see above. TODO: engine/host event
  wire trig_out = test_pass;  // Self-test, see above. TODO: match/injection trigger
  wire active   = hb_cnt[23];
  wire [3:0] aux_out = 4'b0000;  // TODO: engine AUX_OUT

  assign uo_out = {aux_out, active, trig_out, irq, spi_miso};

  // Line fabric reset state: every line is HIZ (spec section 4) so SPLICE
  // is electrically invisible on a bus until it is configured.
  assign uio_out = 8'h00;
  assign uio_oe  = 8'h00;

  // List all unused inputs to prevent warnings
  wire _unused = &{ena, ui_in, uio_in, 1'b0};

endmodule
