#!/bin/bash
# usage: synth.sh <tag> <top> <file> [defines...]   Prints mapped area and the slow-corner critical path.
tag=$1; top=$2; file=$3; shift 3
LIB=${LIBDIR:?set LIBDIR to the filtered liberty directory}
OUT=${OUTDIR:?set OUTDIR}
defs=""; for d in "$@"; do defs="$defs -D$d"; done
cat > $OUT/$tag.ys <<Y
read_liberty -lib -ignore_miss_func $LIB/map_slow.lib
read_verilog -sv $defs $file
hierarchy -check -top $top
synth -top $top -flatten
opt -purge
dfflibmap -liberty $LIB/map_slow.lib
opt
abc -liberty $LIB/map_slow.lib -constr $OUT/abc.constr -D 20000 -showtmp
opt_clean -purge
tee -o $OUT/$tag.stat stat -liberty $LIB/map_slow.lib
Y
printf 'set_driving_cell sg13cmos5l_buf_4\nset_load 0.01\n' > $OUT/abc.constr
yosys -l $OUT/$tag.log $OUT/$tag.ys > /dev/null 2>&1 || { echo "FAIL $tag"; tail -4 $OUT/$tag.log; exit 1; }
python3 -I - "$tag" $OUT/$tag.stat <<'P'
import re,sys
t=open(sys.argv[2]).read()
c=int(re.search(r'Number of cells:\s+(\d+)',t).group(1)); a=float(re.search(r'Chip area for module.*?:\s*([\d.]+)',t).group(1))
ff=sum(int(n) for n in re.findall(r'sg13cmos5l_(?:df|sdf)\w+\s+(\d+)',t))
print(f"{sys.argv[1]:26s} cells={c:6d} area={a:9.0f} um2 flops={ff:5d}")
P
