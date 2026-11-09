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
  # A mutant can make a test loop forever. Never finishing counts as caught, since the real suite finishes.
  timeout "${MUTANT_LIMIT:-1200}" make SRC_DIR="$dir" >/dev/null 2>&1
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
run "delay one cycle short" $E "is_dlyr ? alu_reg : {12'd0, dly_field};" "is_dlyr ? alu_reg : ({12'd0, dly_field} == 0 ? 16'd0 : {12'd0, dly_field} - 16'd1);"
run "timer period off by one" $E "{1'b0, tp[i]} : tcnt[i] - 17'd1" "{1'b0, tp[i]} + 17'd1 : tcnt[i] - 17'd1"
run "half phase wrong" $E "2'd1: tcnt[c] <= phase_half - 17'd1;" "2'd1: tcnt[c] <= phase_half;"
run "ADDR adds only the low byte" $E "wire [15:0] alu_addr = rr[{c, rn_alu}] + opnd_alu;" "wire [15:0] alu_addr = rr[{c, rn_alu}] + {8'd0, opnd_alu[7:0]};"
run "ADDR doesn't set Z" $E "(is_addr && alu_addr == 0) ||" ""
run "TSETR cut to 13 bits" $E "tp[c] <= opnd_cmp;" "tp[c] <= {3'd0, opnd_cmp[12:0]};"
run "TSETR keeps the old tick pending" $E "tcnt[c] <= {1'b0, opnd_cmp};
                pending[c] <= 1'b0;" "tcnt[c] <= {1'b0, opnd_cmp};"
run "CRC always shifts left" $E "wire        crc_fb   = (ins[5] ? crc_reg[15] : crc_reg[0]) ^ lastb[c];
  wire [15:0] alu_crc  = (ins[5] ?" "wire        crc_fb   = crc_reg[15] ^ lastb[c];
  wire [15:0] alu_crc  = (1'b1 ?"
run "CRC ignores the shifted bit" $E "crc_reg[0]) ^ lastb[c];" "crc_reg[0]);"
run "IN doesn't record the bit for CRC" $E "if (is_in || is_inb) lastb[c] <= in_bit;" ";"
run "OUT doesn't record the bit for CRC" $E "if (is_out || is_outb) lastb[c] <= sbit;" ";"
run "contexts share a polynomial" $E "({16{crc_fb}} & poly[c])" "({16{crc_fb}} & poly[0])"
run "TPHASE R1 cut to 14 bits" $E "default: tcnt[c] <= {1'b0, rr[{c, 2'd1}]};" "default: tcnt[c] <= {3'd0, rr[{c, 2'd1}][13:0]};"
run "missed tick doesn't set M" $E "if (tick_now[i] && pending[i]) m[i] <= 1'b1;" ";"
run "missed tick sets F as well" $E "if (tick_now[i] && pending[i]) m[i] <= 1'b1;" "if (tick_now[i] && pending[i]) begin m[i] <= 1'b1; f[i] <= 1'b1; end"
run "JMP M doesn't clear M" $E "if (is_jmp && ins[11:9] == 3'd7) m[c] <= 1'b0;" ";"
run "contexts share M" $E "default: take_jmp = m[c];" "default: take_jmp = m[0];"
run "tick not consumed" $E "if (active && tick_q) pending[c] <= 1'b0;" ";"
run "side-set applies before its tick" $E "wire active = gated && (!tick_q || pend_eff);" "wire active = gated;"
run "falling edge wait acts as rising" $E "default: wait_met = wait_was && !wait_bit;" "default: wait_met = !wait_was && wait_bit;"
run "WAIT timeout never expires" $E "wire        to_expired = to_on && to_dec == 0;" "wire        to_expired = 1'b0;"
run "SHR doesn't set F" $E "(is_shr && sft_reg[0]) ||" ""
run "MSB-first IN shifts wrong way" $E "msb[c] ? {sr[c][14:0], in_bit} : {in_bit, sr[c][15:1]}" "{in_bit, sr[c][15:1]}"
run "RET returns to the wrong place" $E "if (is_call) lr[c] <= pc_inc;" "if (is_call) lr[c] <= pc[c];"
run "contexts share a link register" $E ": is_ret    ? lr[c]" ": is_ret    ? lr[0]"
run "context 1 return address not saved" $E "if (is_call) lr[c]" "if (is_call) lr[0]"
run "edge memory shared by contexts" $E "last[c] <= lines_in;" "begin last[0] <= lines_in; last[1] <= lines_in; end"
run "LFSR taps wrong" $E "16'hB400" "16'hB401"
run "PUSH full doesn't set F" $E "(is_push && e2h_full) || is_shift" "is_shift"
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
run "CMP ignores its second operand" $E "(is_cmp && opnd_cmp == opnd_mov)" "(is_cmp && opnd_cmp == 16'd0)"
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
rm -rf sim_build results.xml
echo done | tee -a "$OUT"
