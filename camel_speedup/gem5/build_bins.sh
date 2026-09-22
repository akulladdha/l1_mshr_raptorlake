#!/bin/bash
# Build Camel binaries for gem5 SE mode (static x86-64 + m5 ops).
# Usage: ./build_bins.sh [outdir] [log2_m] [naccess]
set -euo pipefail

GEM5=${GEM5:-$HOME/gem5}
SRC=$(cd "$(dirname "$0")/../src" && pwd)
OUT=${1:-$HOME/camel_bins}
LOG2_M=${2:-22}
NACCESS=${3:-2000000}
PFDIST=${PFDIST:-32}

mkdir -p "$OUT"

CFLAGS="-O2 -fno-tree-vectorize -static -DGEM5 -DLOG2_M=$LOG2_M -DNACCESS=$NACCESS -DPFDIST=$PFDIST"
INC="-I$GEM5/include"
LIB="$GEM5/util/m5/build/x86/out/libm5.a"

for n in "$@"; do :; done   # keep shellcheck quiet about unused positional

for hn in 0 1 2 3 4 5; do
  gcc $CFLAGS $INC -DHASH_N=$hn "$SRC/camel.c" "$LIB" -o "$OUT/camel_h${hn}_base"
  gcc $CFLAGS $INC -DHASH_N=$hn -DSWPF "$SRC/camel.c" "$LIB" -o "$OUT/camel_h${hn}_t0"
done

echo "built into $OUT (log2_m=$LOG2_M naccess=$NACCESS pfdist=$PFDIST):"
ls -la "$OUT"

echo
echo "--- prefetcht0 present in the T0 build? ---"
objdump -d "$OUT/camel_h0_t0" | grep -c prefetcht0 || true
echo "--- and absent from the baseline? ---"
objdump -d "$OUT/camel_h0_base" | grep -c prefetcht0 || true
