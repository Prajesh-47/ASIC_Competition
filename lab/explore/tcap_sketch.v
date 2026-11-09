// Area sketch for the TCAP/CMPU prototype: what it adds per chip, not an RTL proposal.
// Two contexts, each with a saturating 16-bit "cycles since the last tick" counter and a 16-bit interval latch,
// plus one 16-bit unsigned comparator shared by the engine (only one context executes per cycle).
// SHARE_TIMER: the context's period counter is idle when the tick comes from an edge, so it can count instead.
// Then only the latch is new; the counter is modelled as an input.
module tcap_sketch (
    input  wire        clk,
    input  wire [1:0]  tick,      // per context
`ifdef SHARE_TIMER
    input  wire [31:0] since_in,  // the existing timer counters, reused
`endif
    input  wire        sel,       // which context is executing
    input  wire [15:0] a, b,      // CMPU operands from the register read mux
    output wire [15:0] tint_out,  // TCAP result, into the register write mux
    output wire        lt, eq
);
    reg [15:0] tint [0:1];
`ifdef SHARE_TIMER
    wire [15:0] since [0:1];
    assign since[0] = since_in[15:0];
    assign since[1] = since_in[31:16];
`else
    reg [15:0] since [0:1];
`endif
    genvar i;
    generate for (i = 0; i < 2; i = i + 1) begin : ctx
        wire [15:0] next = (since[i] == 16'hFFFF) ? 16'hFFFF : since[i] + 16'd1;
        always @(posedge clk) begin
            if (tick[i]) tint[i] <= next;
`ifndef SHARE_TIMER
            since[i] <= tick[i] ? 16'd0 : next;
`endif
        end
    end endgenerate
    assign tint_out = tint[sel];
    assign lt = a < b;
    assign eq = a == b;
endmodule
