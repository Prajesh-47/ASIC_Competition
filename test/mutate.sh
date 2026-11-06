#!/bin/bash
# Mutation check: deliberately break the RTL, and confirm the tests notice. Run from test/.
# A mutant that survives points at a gap in the tests. ONLY=regex runs a subset.
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
  make SRC_DIR="$dir" >/dev/null 2>&1
  if python -m cocotb_tools.check_results results.xml >/dev/null 2>&1; then
    echo "SURVIVED     $name" | tee -a "$OUT"
  else
    fails=$(grep -c '<failure' results.xml)
    echo "caught($fails)   $name" | tee -a "$OUT"
  fi
  rm -rf "$dir"
}
E=splice_engine.v; T=tt_um_prajesh47_splice.v; H=splice_host.v
run "OUT always shifts (ignores ns)" $E "O_OUT: if (!ins[6]) begin" "O_OUT: begin"
run "taken jump goes one word late" $E "O_JMP:  if (take_jmp) pc_next = ins[8:0];" "O_JMP:  if (take_jmp) pc_next = ins[8:0] + 9'd1;"
run "JMP BC!=0 tests BC==0" $E "3'd6: take_jmp = bc[c] != 0;" "3'd6: take_jmp = bc[c] == 0;"
run "delay one cycle short" $E "stall[c] <= {12'd0, dly_field};" "stall[c] <= dly_field == 0 ? 16'd0 : {12'd0, dly_field} - 16'd1;"
run "timer period off by one" $E "{1'b0, tp[i]} : tcnt[i] - 14'd1" "{1'b0, tp[i]} + 14'd1 : tcnt[i] - 14'd1"
run "half phase wrong" $E "2'd1: tcnt[c] <= phase_half - 14'd1;" "2'd1: tcnt[c] <= phase_half;"
run "missed tick doesn't set F" $E "if (tick_now[i] && pending[i]) f[i] <= 1'b1;" ";"
run "tick not consumed" $E "if (active && tick_q) pending[c] <= 1'b0;" ";"
run "side-set applies before its tick" $E "wire active = gated && (!tick_q || pend_eff);" "wire active = gated;"
run "falling edge wait acts as rising" $E "default: wait_met = wait_was && !wait_bit;" "default: wait_met = !wait_was && wait_bit;"
run "WAIT timeout never expires" $E "wire        to_expired = to_on && to_dec == 0;" "wire        to_expired = 1'b0;"
run "SHR doesn't set F" $E "f[c] <= rr[{c, rn_sft}][0];" ";"
run "MSB-first IN shifts wrong way" $E "msb[c] ? {sr[c][14:0], in_bit} : {in_bit, sr[c][15:1]}" "{in_bit, sr[c][15:1]}"
run "RET returns to the wrong place" $E "O_CALL: lr[c] <= pc_inc;" "O_CALL: lr[c] <= pc[c];"
run "contexts share a link register" $E "O_RET:  pc_next = lr[c];" "O_RET:  pc_next = lr[0];"
run "context 1 return address not saved" $E "O_CALL: lr[c]" "O_CALL: lr[0]"
run "edge memory shared by contexts" $E "last[c] <= lines_in;" "begin last[0] <= lines_in; last[1] <= lines_in; end"
run "LFSR taps wrong" $E "16'hB400" "16'hB401"
run "PUSH full doesn't set F" $E "O_PUSH: if (e2h_full) f[c] <= 1'b1;" "O_PUSH: ;"
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
run "empty FIFO returns stale data" splice_fifo.v "empty ? 16'd0 : mem[rp[DEPTH_LOG2-1:0]]" "mem[rp[DEPTH_LOG2-1:0]]"
run "CMP ignores its second operand" $E "O_CMP: z[c] <= opnd_cmp == opnd_mov;" "O_CMP: z[c] <= opnd_cmp == 16'd0;"
run "AND acts as XOR" $E "rr[{c, rn_alu}] <= alu_and;" "rr[{c, rn_alu}] <= alu_xor;"
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
run "RAND seed ignored" $E "3'd7: lfsr[c] <= mov_val;" "3'd7: ;"
rm -rf sim_build results.xml
echo done | tee -a "$OUT"
