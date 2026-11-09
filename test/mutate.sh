#!/bin/bash
# Mutation check: deliberately break the RTL, and confirm the tests notice. Run from test/.
# A mutant that survives points at a gap in the tests. ONLY=regex runs a subset. MAKE_ARGS is passed to
# make, for example MAKE_ARGS=COCOTB_TEST_MODULES=test to run one module.
SRC=$(cd ../src && pwd)
OUT=${1:-mutation_results.txt}
: > "$OUT"
run() {
  name=$1; file=$2; from=$3; to=$4
  if [ -n "$ONLY" ] && ! [[ "$name" =~ $ONLY ]]; then return; fi
  dir=$(mktemp -d)
  cp "$SRC"/*.v "$dir"/
  if ! python3 - "$dir/$file" "$from" "$to" <<'PY'
import sys
path, a, b = sys.argv[1:4]
s = open(path).read()
if a not in s:
    sys.exit(1)
open(path, "w").write(s.replace(a, b, 1))
PY
  then echo "NOT APPLIED  $name" | tee -a "$OUT"; rm -rf "$dir"; return; fi
  rm -rf sim_build results.xml
  # A mutant can make a test loop forever. Never finishing counts as caught, since the real suite finishes.
  timeout "${MUTANT_LIMIT:-1200}" make SRC_DIR="$dir" $MAKE_ARGS >/dev/null 2>&1
  if [ $? -eq 124 ]; then
    echo "caught(hang) $name" | tee -a "$OUT"
    rm -rf "$dir"
    return
  fi
  if python -m cocotb_tools.check_results results.xml >/dev/null 2>&1; then
    echo "SURVIVED     $name" | tee -a "$OUT"
  else
    fails=$(grep -c '<failure' results.xml)
    echo "caught($fails)   $name" | tee -a "$OUT"
  fi
  rm -rf "$dir"
}
E=splice_engine.v; T=tt_um_prajesh47_splice.v; H=splice_host.v
run "OUT always shifts (ignores ns)" $E "wire sr_out = (is_out && !ins[6]) || is_outb;" "wire sr_out = is_out || is_outb;"
run "taken jump goes one word late" $E "wire [8:0] pc_taken = to_target ? ins[8:0]" "wire [8:0] pc_taken = to_target ? ins[8:0] + 9'd1"
run "JMP BC!=0 tests BC==0" $E "3'd6: take_jmp = bc[c] != 0;" "3'd6: take_jmp = bc[c] == 0;"
run "delay one cycle short" $E "is_ldp ? 16'd1 : {12'd0, dly_field};" "is_ldp ? 16'd1 : ({12'd0, dly_field} == 0 ? 16'd0 : {12'd0, dly_field} - 16'd1);"
run "timer period off by one" $E "{1'b0, tp[i]} : tcnt[i] - 17'd1" "{1'b0, tp[i]} + 17'd1 : tcnt[i] - 17'd1"
run "half phase wrong" $E "2'd1: tcnt[c] <= phase_half - 17'd1;" "2'd1: tcnt[c] <= phase_half;"
run "ADDR adds only the low byte" $E "wire [15:0] alu_addr = rr[{c, rn_alu}] + opnd_alu;" "wire [15:0] alu_addr = rr[{c, rn_alu}] + {8'd0, opnd_alu[7:0]};"
run "ADDR doesn't set Z" $E "(is_addr && alu_addr == 0) ||" ""
run "TSETR cut to 13 bits" $E "tp[c] <= opnd_cmp;" "tp[c] <= {3'd0, opnd_cmp[12:0]};"
run "TSETR keeps the old tick pending" $E "tcnt[c] <= {1'b0, opnd_cmp};
                pending[c] <= 1'b0;" "tcnt[c] <= {1'b0, opnd_cmp};"
run "CRC always shifts left" $E "wire        crc_dir  = ins[5];" "wire        crc_dir  = 1'b1;"
run "CRC ignores the shifted bit" $E "ins[4] ? crc_bits[3] : lastb_c[crc_lane]" "ins[4] ? crc_bits[3] : 1'b0"
run "IN doesn't record the bit for CRC" $E "if (is_in || is_inb) lastb[c] <= grp_in;" ";"
run "OUT doesn't record the bit for CRC" $E "if (is_out || is_outb) lastb[c] <= grp_out;" ";"
run "contexts share a polynomial" $E "wire [15:0] poly_c   = poly[c];" "wire [15:0] poly_c   = poly[0];"
run "TPHASE R1 cut to 14 bits" $E "default: tcnt[c] <= {1'b0, rr[{c, 2'd1}]};" "default: tcnt[c] <= {3'd0, rr[{c, 2'd1}][13:0]};"
run "tcap: TCAP.t reads the interval before its tick" $E "wire [15:0] tint_now = tick_now[c] ? since_inc : tint[c];" "wire [15:0] tint_now = tint[c];"
run "tcap: interval one short" $E "wire [15:0] since_inc = since[c] == 16'hFFFF ? 16'hFFFF : since[c] + 16'd1;" "wire [15:0] since_inc = since[c];"
run "tcap: interval wraps instead of saturating" $E "end else if (since[i] != 16'hFFFF) begin" "end else begin"
run "tcap: contexts share an interval" $E "wire [15:0] tint_now = tick_now[c] ? since_inc : tint[c];" "wire [15:0] tint_now = tick_now[c] ? since_inc : tint[0];"
run "tcap: CMPU compares the wrong way" $E "(is_cmpu && opnd_cmp < opnd_mov)" "(is_cmpu && opnd_cmp > opnd_mov)"
run "tcap: CMPU doesn't set Z" $E "((is_cmp || is_cmpu) && opnd_cmp == opnd_mov)" "(is_cmp && opnd_cmp == opnd_mov)"
run "missed tick doesn't set M" $E "if (tick_now[i] && pending[i]) m[i] <= 1'b1;" ";"
run "missed tick sets F as well" $E "if (tick_now[i] && pending[i]) m[i] <= 1'b1;" "if (tick_now[i] && pending[i]) begin m[i] <= 1'b1; f[i] <= 1'b1; end"
run "JMP M doesn't clear M" $E "if (is_jmp && ins[11:9] == 3'd7) m[c] <= 1'b0;" ";"
run "contexts share M" $E "default: take_jmp = m[c];" "default: take_jmp = m[0];"
run "tick not consumed" $E "if (active && tick_q) pending[c] <= 1'b0;" ";"
run "side-set applies before its tick" $E "wire active = gated && (!tick_q || pend_eff);" "wire active = gated;"
run "falling edge wait acts as rising" $E "default: wait_met = wait_was && !wait_bit;" "default: wait_met = !wait_was && wait_bit;"
run "WAIT timeout never expires" $E "wire        to_expired = to_on && to_dec == 0;" "wire        to_expired = 1'b0;"
run "SHR doesn't set F" $E "(is_shr && sft_reg[0]) ||" ""
run "MSB-first IN shifts wrong way" $E "sr_shifted_in = msb_c ? {sr_c[14:0], grp_in[0]} : {grp_in[0], sr_c[15:1]};" "sr_shifted_in = {grp_in[0], sr_c[15:1]};"
run "RET returns to the wrong place" $E "if (is_call) lr[c] <= pc_inc;" "if (is_call) lr[c] <= pc[c];"
run "contexts share a link register" $E ": is_ret    ? lr[c]" ": is_ret    ? lr[0]"
run "context 1 return address not saved" $E "if (is_call) lr[c]" "if (is_call) lr[0]"
run "edge memory shared by contexts" $E "last[c] <= lines_in;" "begin last[0] <= lines_in; last[1] <= lines_in; end"
run "LFSR taps wrong" $E "16'hB400" "16'hB401"
run "PUSH full doesn't set F" $E "(is_push && out_full) || is_shift" "is_shift"
run "INV acts as PASS" $T "3'd3: {line_oe[x], line_out[x]} <= {!(!level[x] && odrain[x]), !level[x] && !odrain[x]};" "3'd3: {line_oe[x], line_out[x]} <= {!(level[x] && odrain[x]), level[x] && !odrain[x]};"
run "open-drain drives high" $T "3'd1: {line_oe[x], line_out[x]} <= {!(eng_nx[x] && odrain[x]), eng_nx[x] && !odrain[x]};" "3'd1: {line_oe[x], line_out[x]} <= {1'b1, eng_nx[x]};"
run "fast pass-through ignored" $T "wire [7:0] level = (fast & partner_fast) | (~fast & partner);" "wire [7:0] level = partner;"
run "extra synchronizer stage missing" $T "sync2 <= sync1;" "sync2 <= pins_in;"
run "reset leaves a line driven" $T "      line_oe <= 8'd0;
    end else begin" "      line_oe <= 8'h01;
    end else begin"
run "host write skips a word" $H "if (mem_req && mem_we) mem_addr <= mem_addr + 9'd1;" "if (mem_req && mem_we) mem_addr <= mem_addr + 9'd2;"
run "FIFO read loses the low byte" $H "tx <= rd_word[7:0];" "tx <= 8'd0;"
run "STOP doesn't release lines" $T "wire engine_rst = !rst_n || start || stop;" "wire engine_rst = !rst_n || start;"
run "FIFO read pops the word after the last one read" $H "if (pop_next) begin" "if (pop_next || (byte_done && cmd == 8'h21)) begin"
run "hostlink: XFER pops past the level" $H "pop_next <= xf_rd != 0;" "pop_next <= !fifo_empty;"
run "hostlink: XFER pushes past nw" $H "fifo_push <= xf_wr != 0 && !fifo_full;" "fifo_push <= !fifo_full;"
run "hostlink: XFER sends level as free" $H "          tx <= xf_free;" "          tx <= fifo_level;"
run "hostlink: XFER takes the pad byte as a slot" $H "if (phase != 3'd2) word_lo <= !word_lo;" "word_lo <= !word_lo;"
run "hostlink: STATUS level byte is 0" $H "if (phase == 1) tx <= fifo_level;" "if (phase == 1) tx <= 8'd0;"
run "hostlink: free space one short" $T "FIFO_WORDS - {1'b0, h2e_level}" "FIFO_WORDS - {1'b0, h2e_level} - 9'd1"
run "hostlink: level one high" splice_fifo.v "8'd255 : count16[7:0];" "8'd255 : count16[7:0] + 8'd1;"
run "empty FIFO returns stale data" splice_fifo.v "empty ? 16'd0 : mem[rp[DEPTH_LOG2-1:0]]" "mem[rp[DEPTH_LOG2-1:0]]"
run "CMP ignores its second operand" $E "((is_cmp || is_cmpu) && opnd_cmp == opnd_mov)" "((is_cmp && opnd_cmp == 16'd0) || (is_cmpu && opnd_cmp == opnd_mov))"
run "AND acts as XOR" $E "({16{is_and}} & alu_and)" "({16{is_and}} & alu_xor)"
C=splice_capture.v
B=splice_bridge.v
run "post-trigger count ignored" $C "wire [8:0] remaining = triggered ? post_left : post;" "wire [8:0] remaining = 9'd0;"
run "capture dt starts wrong" $C "wire [11:0] entry_dt = first ? 12'd0 :" "wire [11:0] entry_dt = first ? 12'd1 :"
run "ring wraps without flag" $C "if (ptr == 9'd511) wrapped <= 1'b1;" ";"
run "full capture keeps running" $C "if (ptr == 9'd511 && !mode[0]) active <= 1'b0;" ";"
run "mark costs time" $C "wire clears_dt = first || change || (repeat_due && !mark);" "wire clears_dt = first || change || repeat_due || mark;"
run "prescaler off by one" $C "wire tick = pre == prescale;" "wire tick = pre == prescale + 16'd1;"
run "TRIG_IN can't start capture" $C "else if (on || (mode[1] && trig_rise)) begin" "else if (on) begin"
run "repeat entry late" $C "localparam [11:0] DT_REPEAT = 12'hFFF, DT_MARK = 12'hFFE, DT_LIMIT = 12'hFFD;" "localparam [11:0] DT_REPEAT = 12'hFFF, DT_MARK = 12'hFFE, DT_LIMIT = 12'hFFC;"
run "CAPOFF ignored" $T ".off      (engine_cap_off || host_cap_off || stop)," ".off      (host_cap_off || stop),"
run "bridge never holds off" $B "state_nx = HOLDOFF;
          cnt_nx = holdoff;
        end
        B_HOLDS" "state_nx = IDLE;
          cnt_nx = holdoff;
        end
        B_HOLDS"
run "bridge drives the wrong side" $B "assign low_a_nx = state_nx == B_HOLDS;" "assign low_a_nx = state_nx == A_HOLDS;"
run "bridge ignores B" $B "else if (!line_b) state_nx = B_HOLDS;" ";"
run "bridge holdoff ignores the register" $T ".holdoff (holdoff)," ".holdoff (8'd2),"
run "RAND seed ignored" $E "if (is_rand || (is_mov && ins[7:5] == 3'd7)) lfsr[c] <= is_rand ? lfsr_nx : mov_val;" "if (is_rand) lfsr[c] <= lfsr_nx;"
# explore/isa-lanes-int: LANES, CRCW, LDP and JMPR (ROUTE dropped for the links)
run "lanes: LANES ignored" $E "if (is_lanes) lw[c] <= ins[7:6];" ";"
run "lanes: contexts share a width" $E "wire [1:0]  lw_c = lw[c];" "wire [1:0]  lw_c = lw[0];"
run "lanes: 2-lane IN swaps its lines" $E "grp_in = {6'd0, lines[{ln_a[3:1], 1'b0} +: 2]};" "grp_in = {6'd0, lines[{ln_a[3:1], 1'b0}], lines[{ln_a[3:1], 1'b1}]};"
run "lanes: 4-lane shift moves 3 bits" $E "sr_shifted_out = msb_c ? {sr_c[11:0], 4'd0} : {4'd0, sr_c[15:4]};" "sr_shifted_out = msb_c ? {sr_c[12:0], 3'd0} : {3'd0, sr_c[15:3]};"
run "lanes: 4-lane LSB-first IN fills the wrong end" $E "sr_shifted_in = msb_c ? {sr_c[11:0], grp_in[3:0]} : {grp_in[3:0], sr_c[15:4]};" "sr_shifted_in = {sr_c[11:0], grp_in[3:0]};"
run "lanes: 8-lane IN always takes the low byte" $E "grp_in = ln_a[3] ? lines[15:8] : lines[7:0];" "grp_in = lines[7:0];"
run "lanes: OUT drives only the named line" $E "if ((l >> lw_c) == (ln_a >> lw_c))" "if (l == ln_a)"
run "lanes: CRCW at 8 lanes uses stride 4" $E "wire        crc_wide = lw_c == 2'd3;" "wire        crc_wide = 1'b0;"
run "lanes: CRCW takes bits newest first" $E "crc_bits = msb_c ? {sr_pad[12 + crc_lane]" "crc_bits = !msb_c ? {sr_pad[12 + crc_lane]"
run "lanes: CRCW does 3 steps" $E "wire [15:0] crc_s4   = crc_step(crc_step(crc_s2, crc_bits[1], crc_dir, poly_c), crc_bits[0], crc_dir, poly_c);" "wire [15:0] crc_s4   = crc_step(crc_s2, crc_bits[1], crc_dir, poly_c);"
run "lanes: CRC ignores its lane" $E "ins[4] ? crc_bits[3] : lastb_c[crc_lane]" "ins[4] ? crc_bits[3] : lastb_c[0]"
run "lanes: CRCW acts as one CRC step" $E "wire [15:0] alu_crc  = !ins[4] ? crc_s1" "wire [15:0] alu_crc  = 1'b1 ? crc_s1"
run "ldp: word lands in R0" $E "rr[{c, ldp_dst[c]}] <= mem_data;" "rr[{c, 2'd0}] <= mem_data;"
run "ldp: one context reads the next instruction" $E ": (ex && is_ldp) ? ldp_src : pc_next;" ": pc_next;"
run "ldp: several contexts read the next one's instruction" $E ": nctx_m1 != 0 ? (ldp_pend[cur_nx] ? ldp_addr[cur_nx] : pc[cur_nx])" ": nctx_m1 != 0 ? pc[cur_nx]"
run "ldp: the word goes to the wrong context" $E "ldp_pend[cur_nx] ? ldp_addr[cur_nx]" "ldp_pend[c] ? ldp_addr[c]"
run "ldp: address from the wrong register" $E "wire [8:0] ldp_src = rr[{c, ins[5:4]}][8:0];" "wire [8:0] ldp_src = rr[{c, ins[7:6]}][8:0];"
run "ldp: no second slot" $E "is_ldp ? 16'd1 :" "is_ldp ? 16'd0 :"
run "jmpr: lands one word late" $E ": is_jmpr   ? opnd_cmp[8:0]" ": is_jmpr   ? opnd_cmp[8:0] + 9'd1"
run "jmpr: jumps to the wrong operand" $E ": is_jmpr   ? opnd_cmp[8:0]" ": is_jmpr   ? opnd_mov[8:0]"
rm -rf sim_build results.xml
echo done | tee -a "$OUT"
