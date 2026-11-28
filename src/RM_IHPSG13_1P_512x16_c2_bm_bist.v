// Blackbox for synthesis. Simulation uses the PDK model in test/models instead.
// The FPGA build is the one exception: an iCE40 has no such macro, so it gets an inferred block RAM.
`ifndef SPLICE_SRAM_MODELS
module RM_IHPSG13_1P_512x16_c2_bm_bist (
    input A_CLK,
    input A_MEN,
    input A_WEN,
    input A_REN,
    input [8:0] A_ADDR,
    input [15:0] A_DIN,
    input A_DLY,
    output [15:0] A_DOUT,
    input [15:0] A_BM,
    input A_BIST_CLK,
    input A_BIST_EN,
    input A_BIST_MEN,
    input A_BIST_WEN,
    input A_BIST_REN,
    input [8:0] A_BIST_ADDR,
    input [15:0] A_BIST_DIN,
    input [15:0] A_BIST_BM
);
// tt-support-tools' tt_fpga.py runs yosys with -DSYNTH. LibreLane defines __librelane__ and never SYNTH,
// so the ASIC flow still sees an empty blackbox. The behaviour is the PDK model's, without BIST and
// without write-through: SPLICE never asserts A_WEN and A_REN in the same cycle.
`ifdef SYNTH
`ifndef __librelane__
  reg [15:0] mem [0:511];
  reg [15:0] dout;
  integer i;
  always @(posedge A_CLK) begin
    if (A_MEN && A_WEN) begin
      for (i = 0; i < 16; i = i + 1)
        if (A_BM[i]) mem[A_ADDR][i] <= A_DIN[i];
    end else if (A_MEN && A_REN) begin
      dout <= mem[A_ADDR];
    end
  end
  assign A_DOUT = dout;
`endif
`endif
endmodule
`endif
