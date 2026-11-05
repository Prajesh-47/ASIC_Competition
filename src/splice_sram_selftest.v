/*
 * SPLICE: SRAM self-test (temporary, until the engine replaces it)
 * Copyright (c) 2026 Prajesh-47
 * SPDX-License-Identifier: Apache-2.0
 *
 * Runs once after reset and exercises every word of both macros through the
 * real macro ports, so simulation and the gate-level netlist can be checked
 * from top-level pins alone. Not part of the SPLICE architecture.
 *
 * For each macro: write all words, read and check them, then repeat with
 * inverted data. The address is in the low data bits so aliasing is detected,
 * and the inverted pass toggles every data bit.
 */

`default_nettype none

module splice_sram_selftest (
    input  wire        clk,
    input  wire        rst_n,

    output reg         prog_men,
    output reg         prog_wen,
    output reg         prog_ren,
    input  wire [15:0] prog_dout,

    output reg         cap_men,
    output reg         cap_wen,
    output reg         cap_ren,
    input  wire [15:0] cap_dout,

    // Each macro has its own address and data registers, loaded only when it is
    // selected, so no long wire runs between the macros.
    output reg  [7:0]  prog_addr,
    output reg  [15:0] prog_din,
    output reg  [8:0]  cap_addr,
    output reg  [15:0] cap_din,

    output reg         done,
    output reg         pass
);

  localparam [1:0] S_WRITE = 2'd0, S_READ = 2'd1, S_DRAIN = 2'd2, S_DONE = 2'd3;

  localparam [8:0] PROG_LAST = 9'd255;
  localparam [8:0] CAP_LAST  = 9'd511;

  reg [1:0] state;
  reg       sel;    // 0 = program macro, 1 = capture macro
  reg       inv;    // 1 = inverted data pass
  reg [8:0] cnt;

  // Read result is valid in the cycle after the macro samples the address.
  reg       ren_q;
  reg [8:0] addr_q;
  reg       fail;

  function automatic [15:0] pattern(input [8:0] a, input invert);
    pattern = {~a[6:0], a} ^ {16{invert}};
  endfunction

  wire [8:0]  last     = sel ? CAP_LAST : PROG_LAST;
  wire [15:0] dout     = sel ? cap_dout : prog_dout;
  wire [15:0] expected = pattern(addr_q, inv);

  always @(posedge clk) begin
    if (!rst_n) begin
      state    <= S_WRITE;
      sel      <= 1'b0;
      inv      <= 1'b0;
      cnt      <= 9'd0;
      ren_q    <= 1'b0;
      addr_q   <= 9'd0;
      fail     <= 1'b0;
      prog_addr <= 8'd0;
      prog_din  <= 16'd0;
      cap_addr  <= 9'd0;
      cap_din   <= 16'd0;
      prog_men <= 1'b0;
      prog_wen <= 1'b0;
      prog_ren <= 1'b0;
      cap_men  <= 1'b0;
      cap_wen  <= 1'b0;
      cap_ren  <= 1'b0;
      done     <= 1'b0;
      pass     <= 1'b0;
    end else begin
      ren_q  <= prog_ren | cap_ren;
      addr_q <= sel ? cap_addr : {1'b0, prog_addr};

      // Written as if/else so an X on the read data counts as a failure.
      // `if (dout != expected) fail` would treat X as a pass.
      if (ren_q) begin
        if (dout == expected) fail <= fail;
        else                  fail <= 1'b1;
      end

      case (state)
        S_WRITE: begin
          prog_men <= !sel;
          cap_men  <= sel;
          prog_wen <= !sel;
          cap_wen  <= sel;
          if (sel) begin
            cap_addr <= cnt;
            cap_din  <= pattern(cnt, inv);
          end else begin
            prog_addr <= cnt[7:0];
            prog_din  <= pattern(cnt, inv);
          end
          if (cnt == last) begin
            cnt   <= 9'd0;
            state <= S_READ;
          end else begin
            cnt <= cnt + 9'd1;
          end
        end

        S_READ: begin
          prog_wen <= 1'b0;
          cap_wen  <= 1'b0;
          prog_ren <= !sel;
          cap_ren  <= sel;
          if (sel) cap_addr  <= cnt;
          else     prog_addr <= cnt[7:0];
          if (cnt == last) begin
            cnt   <= 9'd0;
            state <= S_DRAIN;
          end else begin
            cnt <= cnt + 9'd1;
          end
        end

        S_DRAIN: begin
          prog_ren <= 1'b0;
          cap_ren  <= 1'b0;
          // Wait for the last read to be checked before moving on.
          if (!prog_ren && !cap_ren && !ren_q) begin
            prog_men <= 1'b0;
            cap_men  <= 1'b0;
            inv      <= !inv;
            if (inv) sel <= 1'b1;
            state <= (inv && sel) ? S_DONE : S_WRITE;
          end
        end

        S_DONE: begin
          done <= 1'b1;
          if (fail) pass <= 1'b0;
          else      pass <= 1'b1;
        end

        default: state <= S_DONE;
      endcase
    end
  end

endmodule
