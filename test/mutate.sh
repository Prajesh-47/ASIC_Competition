#!/bin/bash
# Mutation check: deliberately break the RTL, and confirm the tests notice. Run from test/.
# A mutant that survives points at a gap in the tests.
SRC=$(cd ../src && pwd)
OUT=${1:-mutation_results.txt}
: > "$OUT"
run() {
  name=$1; file=$2; from=$3; to=$4
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
rm -rf sim_build results.xml
echo done | tee -a "$OUT"
