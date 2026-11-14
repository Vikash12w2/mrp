#!/bin/bash
# Pristine repo one-shot (C++ generator + generated z3py script). usage: REPO=... ./run_one.sh W R HLEN cap_seconds
# The generated script is Python 2; there is no python2 here, so ONLY its print statements are converted
# (py2print.py) in a scratch copy. HLEN is the hypercycle to solve for (the paper uses the two-shot T').
# Build once: (cd $REPO/approach_one-shot_z3py/src && make -f Makefile_safety tool_z3)
: "${REPO:?set REPO}"; OUT=${OUT:-./baseline_out}; mkdir -p "$OUT"; HERE=$(cd "$(dirname "$0")" && pwd)
W=$(mktemp -d); cp -r $REPO/approach_one-shot_z3py $W/os && cd $W/os/examples
cp input-warehouse/obstacle.txt input-warehouse/2d_template.txt . && cp input-warehouse/rob*_traj.txt .
printf "18\n18\n$1\n$3\n10\n$2\n" > workspace.txt; rm -f constraints_z3py.py; mkdir -p my_output
../src/tool_z3 > /dev/null 2>&1
python3 $HERE/py2print.py constraints_z3py.py run3.py
S=$(date +%s.%N); timeout $4 python3 run3.py > "$OUT/one_$1w_$2r_H$3.txt" 2>&1; RC=$?; E=$(date +%s.%N)
echo "exit_code $RC wall_seconds $(echo "$E-$S"|bc)" >> "$OUT/one_$1w_$2r_H$3.txt"
