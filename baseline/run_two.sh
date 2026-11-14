#!/bin/bash
# Pristine repo two-shot, as shipped. usage: REPO=<.../mobile-recharger-path-planning-scheduling> ./run_two.sh W R T [cap_seconds]
# Build once: (cd $REPO/approach_two-shot/src && make -f Makefile_safety tool_z3)
# Needs a `time` executable on PATH (the repo shells out to `time`); on minimal systems: printf '#!/bin/bash\nexec "$@"\n' > ~/bin/time
: "${REPO:?set REPO}"; OUT=${OUT:-./baseline_out}; mkdir -p "$OUT"
B=$REPO/approach_two-shot
cd $B/examples && cp input-warehouse/obstacle.txt input-warehouse/2d_template.txt . && cp input-warehouse/rob*_traj.txt .
printf "18\n18\n$1\n$3\n10\n$2\n" > workspace.txt       # input config only; the repo code is untouched
rm -rf my_output && mkdir my_output && rm -f constraints*.smt2
S=$(date +%s.%N); timeout ${4:-3000} ../src/tool_z3 > "$OUT/two_$1w_$2r_T$3.txt" 2>&1; E=$(date +%s.%N)
echo "wall_seconds $(echo "$E-$S"|bc)" >> "$OUT/two_$1w_$2r_T$3.txt"
