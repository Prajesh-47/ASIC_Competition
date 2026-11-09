/*
 * SPLICE: host interface, SPI target in mode 0, oversampled in the core clock domain
 * Copyright (c) 2026 Prajesh-47
 * SPDX-License-Identifier: Apache-2.0
 *
 * SCK must be at most core/8. Bytes are MSB first, and 16-bit words are big-endian.
 *   0x01 WR_PROG  aH aL  word...        0x02 RD_PROG  aH aL  word...
 *   0x03 WR_CAP   aH aL  word...        0x04 RD_CAP   aH aL  word...
 *   0x05 WR_REG   reg val               0x06 RD_REG   reg    val
 *   0x10 START                          0x11 STOP
 *   0x12 STATUS   status                0x20 FIFO_WR  word...
 *   0x21 FIFO_RD  word...                0x22 XFER     nw pad  word...
 *
 * STATUS then sends the engine-to-host level and the host-to-engine free space, one byte each, so a
 * host can size a burst. XFER is full duplex. While the host sends nw and a pad byte, SPLICE sends that
 * level and free space, both taken when the command byte ends. Then each word slot pushes the host's
 * word for the first nw slots, if there is room, and pops the engine's word for the first level slots.
 * Later slots read as 0 without popping, so a word that arrives during the burst waits for the next one.
 * Memory is only reachable while the engine is stopped.
 */
`default_nettype none

module splice_host (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        sck_pin,
    input  wire        mosi_pin,
    input  wire        cs_n_pin,
    output reg         miso,

    output reg         start,
    output reg         stop,
    input  wire [7:0]  status,

    output reg         mem_req,
    output reg         mem_we,
    output reg         mem_cap,     // 0 program, 1 capture
    output reg  [8:0]  mem_addr,
    output reg  [15:0] mem_wdata,
    input  wire [15:0] mem_rdata,

    output reg         reg_we,
    output reg  [3:0]  reg_addr,
    output reg  [7:0]  reg_wdata,
    input  wire [7:0]  reg_rdata,

    output reg         fifo_push,
    output reg  [15:0] fifo_wdata,
    input  wire        fifo_full,
    output reg         fifo_pop,
    input  wire [15:0] fifo_rdata,
    input  wire        fifo_empty,
    input  wire [7:0]  fifo_level,  // engine-to-host words waiting
    input  wire [7:0]  fifo_free    // host-to-engine words of room
);
  localparam [7:0] WR_PROG = 8'h01, RD_PROG = 8'h02, WR_CAP = 8'h03, RD_CAP = 8'h04,
                   WR_REG = 8'h05, RD_REG = 8'h06, START = 8'h10, STOP = 8'h11,
                   STATUS = 8'h12, FIFO_WR = 8'h20, FIFO_RD = 8'h21, XFER = 8'h22;

  reg [1:0] sck_s, mosi_s, cs_s;
  reg       sck_p;
  always @(posedge clk) begin
    sck_s <= {sck_s[0], sck_pin};
    mosi_s <= {mosi_s[0], mosi_pin};
    cs_s <= {cs_s[0], cs_n_pin};
    sck_p <= sck_s[1];
  end
  wire cs_active = !cs_s[1];
  wire sck_rise = sck_s[1] && !sck_p;
  wire sck_fall = !sck_s[1] && sck_p;

  reg [6:0] rx;
  reg [2:0] bitn;
  reg [7:0] tx;
  reg [7:0] cmd;
  reg [2:0] phase;     // bytes after the command, saturating
  reg       word_lo;   // next data byte is the low half of a word
  reg [7:0] hi_byte;
  reg [15:0] rd_word;
  reg       rd_wait;   // a memory read is on its way
  reg       rd_capture;
  reg       reg_load;  // register read data is ready
  reg       pop_next;  // FIFO head is loaded in tx, and goes once the host clocks it
  reg [7:0] xf_free;   // XFER: free space taken with the level, sent second
  reg [7:0] xf_rd;     // XFER: words still to pop
  reg [7:0] xf_wr;     // XFER: words still to push

  wire       byte_done = cs_active && sck_rise && bitn == 3'd7;
  wire [7:0] byte_in = {rx, mosi_s[1]};
  wire       is_mem = cmd == WR_PROG || cmd == RD_PROG || cmd == WR_CAP || cmd == RD_CAP;
  wire       is_rd_mem = cmd == RD_PROG || cmd == RD_CAP;

  always @(posedge clk) begin
    start <= 1'b0;
    stop <= 1'b0;
    mem_req <= 1'b0;
    reg_we <= 1'b0;
    fifo_push <= 1'b0;
    fifo_pop <= 1'b0;
    rd_wait <= 1'b0;
    rd_capture <= rd_wait;
    reg_load <= 1'b0;

    if (!rst_n) begin
      miso <= 1'b0;
      rx <= 0;
      bitn <= 0;
      tx <= 0;
      cmd <= 0;
      phase <= 0;
      word_lo <= 0;
      hi_byte <= 0;
      rd_word <= 0;
      mem_we <= 0;
      mem_cap <= 0;
      mem_addr <= 0;
      mem_wdata <= 0;
      reg_addr <= 0;
      reg_wdata <= 0;
      fifo_wdata <= 0;
      pop_next <= 0;
      xf_free <= 0;
      xf_rd <= 0;
      xf_wr <= 0;
    end else if (!cs_active) begin
      pop_next <= 1'b0;
      bitn <= 0;
      phase <= 0;
      word_lo <= 0;
      tx <= 0;
      miso <= 1'b0;
    end else begin
      if (rd_capture) begin  // read data is ready: the high byte goes out next
        rd_word <= mem_rdata;
        tx <= mem_rdata[15:8];
      end
      if (reg_load) tx <= reg_rdata;

      if (sck_rise) begin
        if (pop_next) begin
          fifo_pop <= 1'b1;
          pop_next <= 1'b0;
        end
        rx <= byte_in[6:0];
        bitn <= bitn + 3'd1;
      end
      if (sck_fall) begin
        miso <= tx[7];
        tx <= {tx[6:0], 1'b0};
      end

      if (byte_done) begin
        if (phase != 3'd7) phase <= phase + 3'd1;
        if (phase == 0) begin
          cmd <= byte_in;
          case (byte_in)
            START: start <= 1'b1;
            STOP: stop <= 1'b1;
            STATUS: tx <= status;
            XFER: begin
              tx <= fifo_level;
              xf_rd <= fifo_level;
              xf_free <= fifo_free;
            end
            FIFO_RD: begin
              tx <= fifo_rdata[15:8];
              rd_word <= fifo_rdata;
              pop_next <= !fifo_empty;
            end
            default: ;
          endcase
          mem_cap <= byte_in == WR_CAP || byte_in == RD_CAP;
        end else if (is_mem && phase == 1) begin
          mem_addr[8] <= byte_in[0];
        end else if (is_mem && phase == 2) begin
          mem_addr[7:0] <= byte_in;
          if (is_rd_mem) begin
            mem_req <= 1'b1;
            mem_we <= 1'b0;
            rd_wait <= 1'b1;
          end
        end else if (is_rd_mem) begin
          // Host is clocking out data
          word_lo <= !word_lo;
          if (!word_lo) begin
            tx <= rd_word[7:0];
          end else begin
            mem_addr <= mem_addr + 9'd1;
            mem_req <= 1'b1;
            mem_we <= 1'b0;
            rd_wait <= 1'b1;
          end
        end else if (cmd == WR_PROG || cmd == WR_CAP) begin
          word_lo <= !word_lo;
          if (!word_lo) begin
            hi_byte <= byte_in;
          end else begin
            mem_wdata <= {hi_byte, byte_in};
            mem_req <= 1'b1;
            mem_we <= 1'b1;
          end
        end else if (cmd == WR_REG) begin
          if (phase == 1) reg_addr <= byte_in[3:0];
          else begin
            reg_wdata <= byte_in;
            reg_we <= 1'b1;
          end
        end else if (cmd == RD_REG) begin
          if (phase == 1) begin
            reg_addr <= byte_in[3:0];
            reg_load <= 1'b1;
          end
        end else if (cmd == FIFO_WR) begin
          word_lo <= !word_lo;
          if (!word_lo) begin
            hi_byte <= byte_in;
          end else begin
            fifo_wdata <= {hi_byte, byte_in};
            fifo_push <= !fifo_full;
          end
        end else if (cmd == STATUS) begin
          if (phase == 1) tx <= fifo_level;
          else if (phase == 2) tx <= fifo_free;
        end else if (cmd == XFER && phase == 1) begin
          xf_wr <= byte_in;
          tx <= xf_free;
        end else if (cmd == XFER) begin
          // phase 2 ends the pad byte, and every later pair is one word slot
          if (phase != 3'd2) word_lo <= !word_lo;
          if (phase != 3'd2 && !word_lo) begin
            hi_byte <= byte_in;
            tx <= rd_word[7:0];
          end else begin
            if (phase != 3'd2) begin
              fifo_wdata <= {hi_byte, byte_in};
              fifo_push <= xf_wr != 0 && !fifo_full;
              if (xf_wr != 0) xf_wr <= xf_wr - 8'd1;
            end
            tx <= xf_rd != 0 ? fifo_rdata[15:8] : 8'd0;
            rd_word <= xf_rd != 0 ? fifo_rdata : 16'd0;
            pop_next <= xf_rd != 0;
            if (xf_rd != 0) xf_rd <= xf_rd - 8'd1;
          end
        end else if (cmd == FIFO_RD) begin
          word_lo <= !word_lo;
          if (!word_lo) begin
            tx <= rd_word[7:0];
          end else begin
            tx <= fifo_rdata[15:8];
            rd_word <= fifo_rdata;
            pop_next <= !fifo_empty;
          end
        end
      end

      // A write advances the address once it has been issued.
      if (mem_req && mem_we) mem_addr <= mem_addr + 9'd1;
    end
  end
endmodule
