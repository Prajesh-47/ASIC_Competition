// Area sketch for the match reflex (ARM/MATCH): what it adds per chip, not an RTL proposal.
// Two contexts, each with a match rule (value, mask, optional BC position) checked on every shift into SR,
// an action (line, source, when, once, trig), the half-period detector on its timer, and the pulse state.
// The fabric gets a second write port on its 8 x 3-bit source registers (the reflex, beside MODE).
// LIVE_REGS: compare against the context's R2/R3 as they are instead of MATCH copies, saving 64 flops,
// at the cost of tying up two registers while armed (CAN and USB firmware can't spare them).
module reflex_sketch (
    input  wire        clk,
    input  wire        sel,            // context executing this cycle
    input  wire        shift_in,       // IN/INB shifts SR this cycle (for context sel)
    input  wire [15:0] sr_next,        // SR after the shift
    input  wire [4:0]  bc_next,
    input  wire        op_match, op_arm,
    input  wire [10:0] imm,            // instruction payload
    input  wire [15:0] rv, rm,         // register read ports, as MATCH operands
`ifdef LIVE_REGS
    input  wire [31:0] r2_live, r3_live, // both contexts' R2 and R3, straight from the register flops
`endif
    input  wire [33:0] tcnt, tp_half,  // both contexts' timer counter and period >> 1 (17 bits each)
    input  wire [1:0]  edge_mode,      // TSRC edge ticks: the half point is the tick itself
    input  wire [1:0]  tick,
    input  wire        mode_we,        // the existing MODE write
    input  wire [2:0]  mode_line, mode_src,
    output reg  [23:0] src,            // 8 lines x 3 bits, the fabric's source registers
    output wire        trig_hit
);
    reg  [1:0] when [0:1];
    reg        once [0:1], trg [0:1], armed [0:1], pe [0:1];
    reg  [2:0] line [0:1], msrc [0:1], saved [0:1], rline [0:1];
    reg  [4:0] pos [0:1];
    reg  [1:0] st [0:1];               // 0 idle, 1 switch at half, 2 pulse at half, 3 restore at half
`ifndef LIVE_REGS
    reg [15:0] mv [0:1], mm [0:1];
`endif
    wire [15:0] v_sel, m_sel;
`ifdef LIVE_REGS
    assign v_sel = sel ? r2_live[31:16] : r2_live[15:0];
    assign m_sel = sel ? r3_live[31:16] : r3_live[15:0];
`else
    assign v_sel = mv[sel];
    assign m_sel = mm[sel];
`endif
`ifdef BASELINE   // only the existing MODE write: yosys removes everything the reflex adds
    wire hit = 1'b0;
`else
    wire hit = shift_in && armed[sel] && st[sel] == 2'd0 && (((sr_next ^ v_sel) & m_sel) == 16'd0)
               && (!pe[sel] || bc_next == pos[sel]);
`endif
    assign trig_hit = hit && trg[sel];

    wire [1:0] half;
`ifdef BASELINE
    assign half = 2'b00;
`else
    assign half[0] = edge_mode[0] ? tick[0] : (tcnt[16:0] == tp_half[16:0]);
    assign half[1] = edge_mode[1] ? tick[1] : (tcnt[33:17] == tp_half[33:17]);
`endif

    // Fabric source write: MODE first, then the reflex now, then each context's half-point action.
    reg [23:0] nsrc;
    integer i;
    always @* begin
        nsrc = src;
        if (mode_we) nsrc[mode_line*3 +: 3] = mode_src;
        if (hit && when[sel] == 2'd0) nsrc[line[sel]*3 +: 3] = msrc[sel];
        for (i = 0; i < 2; i = i + 1)
            if (half[i]) begin
                if (st[i] == 2'd1 || st[i] == 2'd2) nsrc[line[i]*3 +: 3] = msrc[i];
                else if (st[i] == 2'd3) nsrc[rline[i]*3 +: 3] = saved[i];
            end
    end

    genvar c;
    generate for (c = 0; c < 2; c = c + 1) begin : ctx
        always @(posedge clk) begin
            src <= nsrc;   // both contexts write the same vector; written once below in practice
`ifndef LIVE_REGS
            if (op_match && sel == c) begin mv[c] <= rv; mm[c] <= rm; end
`endif
            if (op_match && sel == c) begin pe[c] <= imm[5]; pos[c] <= imm[4:0]; end
            if (op_arm && sel == c) begin
                when[c] <= imm[10:9]; once[c] <= imm[8]; trg[c] <= imm[7];
                line[c] <= imm[6:4]; msrc[c] <= imm[3:1];
                armed[c] <= imm[10:9] != 2'd3;
                if (st[c] != 2'd3) st[c] <= 2'd0;
            end else if (hit && sel == c) begin
                if (once[c]) armed[c] <= 1'b0;
                if (when[c] != 2'd0) st[c] <= when[c];
            end else if (half[c]) begin
                if (st[c] == 2'd2) begin saved[c] <= src[line[c]*3 +: 3]; rline[c] <= line[c]; st[c] <= 2'd3; end
                else if (st[c] != 2'd0) st[c] <= 2'd0;
            end
        end
    end endgenerate
endmodule
