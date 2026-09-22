#!/bin/bash
# Gate experiment for the SWPF early-retire patch.
#
# Question 1: does gem5 model a software prefetch the way real x86 does, i.e.
#             does it retire once issued instead of blocking at the ROB head?
# Question 2: with that fixed, is the L1 MSHR count actually the thing that
#             caps MLP -- does going 10 -> 512 MSHRs unlock a speedup?
#
# Runs Camel h0 (most memory-intensive) in five configurations and reports
# cycles/access, realized MLP, and MSHR-full stalls for each.
set -u

GEM5=${GEM5:-$HOME/gem5}
BIN=${BIN:-$HOME/camel_bins}
CFG=$(cd "$(dirname "$0")" && pwd)/camel_se.py
OUT=${OUT:-$HOME/camel_runs/gate}
MACHINE=${MACHINE:-skylake}
HN=${HN:-h0}

mkdir -p "$OUT"

# name | binary | l1d mshrs | l2/l3 mshrs | early-retire flag
run () {
  local name=$1 bin=$2 l1d=$3 lx=$4 erp=$5
  local args=(--machine "$MACHINE" --cmd "$BIN/$bin"
              --l1d-mshrs "$l1d" --l2-mshrs "$lx" --l3-mshrs "$lx")
  [ "$erp" = yes ] && args+=(--early-retire-pf)
  echo "[start] $name"
  "$GEM5/build/ALL/gem5.opt" --outdir="$OUT/$name" "$CFG" "${args[@]}" \
      > "$OUT/$name.log" 2>&1
  echo "[done ] $name (exit $?)"
}

echo "machine=$MACHINE workload=$HN  outdir=$OUT"
echo

#     name                  binary          l1d  l2/l3  early-retire
run base_mshr10          "camel_${HN}_base"  10    48   no  &
run t0_mshr10_nopatch    "camel_${HN}_t0"    10    48   no  &
run t0_mshr512_nopatch   "camel_${HN}_t0"   512   512   no  &
run t0_mshr10_patch      "camel_${HN}_t0"    10    48   yes &
run t0_mshr512_patch     "camel_${HN}_t0"   512   512   yes &
wait

echo
echo "all runs complete -> $OUT"
