#!/bin/bash
# A gate-level check without the PDK or CI: synthesize src/ with yosys into generic gates, then run the
# test suite on that netlist the way CI runs it on the hardened one (GATES=yes, no internal state checks).
# It catches synthesis-versus-simulation mismatches and gate-level-only test assumptions in minutes.
# It doesn't replace CI's gl_test: no PDK cells, no clock tree, no placed netlist. Run from test/.
# Extra make arguments pass through, e.g. ./gl_yosys.sh COCOTB_TEST_MODULES=test
set -euo pipefail
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
(cd ../src && yosys -q -p "read_verilog -lib RM_IHPSG13_1P_256x16_c2_bm_bist.v RM_IHPSG13_1P_512x16_c2_bm_bist.v;
  read_verilog -I. tt_um_prajesh47_splice.v splice_fifo.v splice_engine.v splice_host.v splice_bridge.v splice_capture.v;
  synth -flatten -top tt_um_prajesh47_splice; opt_clean; write_verilog -noattr $work/netlist.v")
# The Makefile's gate-level sources include three PDK model files. Generic gates need none of them.
lib=$work/pdk/ihp-sg13cmos5l/libs.ref
mkdir -p "$lib/sg13cmos5l_io/verilog" "$lib/sg13cmos5l_stdcell/verilog"
touch "$lib/sg13cmos5l_io/verilog/sg13cmos5l_io.v" "$lib/sg13cmos5l_stdcell/verilog/sg13cmos5l_udp.v" \
      "$lib/sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v"
saved=""
if [ -f gate_level_netlist.v ]; then saved=$work/saved.v; cp gate_level_netlist.v "$saved"; fi
cp "$work/netlist.v" gate_level_netlist.v
status=0
GATES=yes PDK_ROOT=$work/pdk make SIM_BUILD=sim_build/gl_yosys "$@" || status=$?
if [ -n "$saved" ]; then cp "$saved" gate_level_netlist.v; else rm -f gate_level_netlist.v; fi
exit $status
