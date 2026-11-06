#!/bin/bash
# Formal proofs with Yosys's built-in SAT solver: temporal induction from reset. Run from formal/.
# Every case has a required outcome, and there are three possible results, kept apart on purpose:
#   proved          induction closed: holds for all time, for every input sequence
#   counterexample  the solver found a real sequence of inputs that breaks a property
#   inconclusive    no result, or a tool error. This never counts as a pass or as a required failure
set -u
SRC=../src
status=0
OUT=$(mktemp -d)

check() {  # required name top files chparams steps
  required=$1; name=$2; top=$3; files=$4; params=$5; steps=$6
  cat > "$OUT/$name.ys" <<Y
read_verilog -sv -formal -DFORMAL $files
hierarchy -check -top $top $params
proc; flatten; memory; async2sync; opt -keepdc; opt_clean
sat -tempinduct -prove-asserts -seq 2 -set-at 1 rst 1 -set-at 2 rst 0 -maxsteps $steps
Y
  yosys -q -l "$OUT/$name.log" "$OUT/$name.ys" >/dev/null 2>&1
  rc=$?
  if grep -q "induction proof finished - model found for base case: FAIL" "$OUT/$name.log"; then got=counterexample
  elif [ $rc -eq 0 ] && grep -q "Induction step proven: SUCCESS" "$OUT/$name.log"; then got=proved
  else got=inconclusive; fi
  length=$(grep -oE "induction length [0-9]+|length [0-9]+ \*\*" "$OUT/$name.log" | tail -1 | grep -oE "[0-9]+")
  if [ "$got" = "$required" ]; then
    echo "OK     $name: $got (depth ${length:-?})"
  else
    echo "WRONG  $name: $got, required $required"
    grep -E "ERROR|Reached maximum" "$OUT/$name.log" | tail -2
    status=1
  fi
}

B="bridge_closed_loop.v $SRC/splice_bridge.v"
check proved         bridge_rise5_hold12    bridge_closed_loop "$B" "-chparam HOLDOFF 12 -chparam RISE 5 -chparam BOUND 32" 45
check proved         bridge_rise10_hold20   bridge_closed_loop "$B" "-chparam HOLDOFF 20 -chparam RISE 10 -chparam BOUND 50" 65
check proved         bridge_rise15_hold30   bridge_closed_loop "$B" "-chparam HOLDOFF 30 -chparam RISE 15 -chparam BOUND 75" 90
check proved         fifo_safety            fifo_props "fifo_props.v $SRC/splice_fifo.v" "" 30
# Controls: these must produce a real counterexample, or the proofs above show nothing.
check counterexample holdoff_too_short      bridge_closed_loop "$B" "-chparam HOLDOFF 2 -chparam RISE 10 -chparam BOUND 50" 65
check counterexample release_bound_too_tight bridge_closed_loop "$B" "-chparam HOLDOFF 20 -chparam RISE 10 -chparam BOUND 5" 30
rm -rf "$OUT"
exit $status
