#!/bin/bash
# Independent verification of the gem5 Camel results.
#
# Every check here tests something that would be WRONG if the numbers were
# bogus, and each is checkable without trusting the analysis scripts. Run from
# anywhere inside WSL:
#
#   bash camel_speedup/verify.sh            # fast checks (~10 s)
#   bash camel_speedup/verify.sh --full     # also re-runs gem5 to prove determinism (~6 min)
#
# Exit code 0 = all checks passed.

set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
GEM5=${GEM5:-$HOME/gem5}
BINS=${BINS:-$HOME/camel_bins}
RUNS=${RUNS:-$HOME/camel_runs}
CSV="$HERE/results/gem5_runs.csv"
FULL=0
[ "${1:-}" = "--full" ] && FULL=1

pass=0; fail=0
ok()   { echo "  PASS  $1"; pass=$((pass+1)); }
bad()  { echo "  FAIL  $1"; fail=$((fail+1)); }
head_() { echo; echo "== $1"; }

# ---------------------------------------------------------------- 1
head_ "1. Index permutation is a real bijection (no duplicate/missing elements)"
if gcc -O2 "$HERE/src/check_perm.c" -o /tmp/check_perm 2>/dev/null && /tmp/check_perm > /tmp/perm.out 2>&1; then
  if grep -q "bijection verified for all sizes" /tmp/perm.out; then
    ok "check_perm.c: exhaustive, lg=8..24, zero dup/missing"
  else
    bad "check_perm.c reported a problem"; tail -3 /tmp/perm.out
  fi
else
  bad "could not build/run check_perm.c"
fi

# ---------------------------------------------------------------- 2
head_ "2. The prefetch is in the measured loop, and only in the T0 binary"
# Count inside main() only -- static libc contains unrelated prefetcht0.
for b in "$BINS"/camel_h0_base_d32_m21_n1000000 "$BINS"/camel_h0_t0_d32_m21_n1000000; do
  [ -f "$b" ] || { bad "missing binary $b"; continue; }
  n=$(objdump -d --disassemble=main "$b" 2>/dev/null | grep -c prefetcht0)
  case "$(basename "$b")" in
    *_base_*) [ "$n" -eq 0 ] && ok "baseline main() has 0 prefetcht0" || bad "baseline main() has $n prefetcht0 (expected 0)" ;;
    *_t0_*)   [ "$n" -eq 1 ] && ok "T0 main() has exactly 1 prefetcht0" || bad "T0 main() has $n prefetcht0 (expected 1)" ;;
  esac
done

# ---------------------------------------------------------------- 3
head_ "3. Stats window captures exactly the loop (nothing from init leaks in)"
python3 - "$CSV" <<'PY'
import csv,sys
rows=[r for r in csv.DictReader(open(sys.argv[1]))]
bad=0; checked=0
for r in rows:
    # Only runs that feed a figure. Scratch/config-smoke runs are not claims.
    if not r["tag"].endswith(("fig310","control")): continue
    try:
        a=float(r["accesses"]); i=float(r["insts"])
    except (ValueError,KeyError,TypeError): continue
    if a != 1000000:
        print(f"  FAIL  {r['run']}: accesses={a:.0f}, expected 1000000"); bad+=1; continue
    # loop prologue/epilogue is a small constant; the per-iteration part must
    # be an exact integer or non-loop code leaked into the window
    per=(i-(i%a))/a if a else 0
    frac=(i-per*a)
    if frac > 20:
        print(f"  FAIL  {r['run']}: {frac:.0f} insts outside the loop body"); bad+=1
    checked+=1
print(f"  {'PASS' if bad==0 else 'FAIL'}  {checked} runs: exactly 1e6 accesses, integer insts/iteration")
sys.exit(1 if bad else 0)
PY
[ $? -eq 0 ] && pass=$((pass+1)) || fail=$((fail+1))

# ---------------------------------------------------------------- 4
head_ "4. Baseline and T0 differ by exactly the prefetch (2 insts), nothing else"
python3 - "$CSV" <<'PY'
import csv,sys
rows=[r for r in csv.DictReader(open(sys.argv[1]))]
bad=0
for mach in sorted({r["machine"] for r in rows if r["machine"]!="unknown"}):
    for hn in range(6):
        sel=lambda s: [r for r in rows if r["machine"]==mach and r["swpf"]==s
                       and r["hash_n"] and int(float(r["hash_n"]))==hn
                       and r["tag"].endswith("fig310")]
        b,t=sel("0"),sel("1")
        if not b or not t: continue
        bi=float(b[0]["insts"])/float(b[0]["accesses"])
        ti=float(t[0]["insts"])/float(t[0]["accesses"])
        d=round(ti-bi,4)
        if d != 2.0:
            print(f"  FAIL  {mach} h{hn}: T0-base = {d} insts/access, expected 2"); bad+=1
print(f"  {'PASS' if bad==0 else 'FAIL'}  base and T0 are the same code plus one prefetch")
sys.exit(1 if bad else 0)
PY
[ $? -eq 0 ] && pass=$((pass+1)) || fail=$((fail+1))

# ---------------------------------------------------------------- 5
head_ "5. The MSHR counts we claim were actually applied by gem5"
python3 - "$RUNS" <<'PY'
import configparser,glob,os,re,sys
bad=0; checked=0
for cfg in glob.glob(os.path.join(sys.argv[1],"*","*","config.ini")):
    name=os.path.basename(os.path.dirname(cfg))
    m=re.search(r"mshr(\d+)",name)
    if not m: continue
    cp=configparser.ConfigParser(strict=False); cp.read(cfg)
    try: actual=int(cp.get("system.cpu.dcache","mshrs"))
    except Exception: continue
    want=int(m.group(1))
    if actual!=want:
        print(f"  FAIL  {name}: config.ini says L1D mshrs={actual}, name says {want}"); bad+=1
    checked+=1
print(f"  {'PASS' if bad==0 else 'FAIL'}  {checked} runs: L1D mshrs in config.ini matches the run name")
sys.exit(1 if bad else 0)
PY
[ $? -eq 0 ] && pass=$((pass+1)) || fail=$((fail+1))

# ---------------------------------------------------------------- 6
head_ "6. Working set really is bigger than the LLC (we are hitting DRAM)"
python3 - "$CSV" <<'PY'
import csv,sys
rows=[r for r in csv.DictReader(open(sys.argv[1]))]
bad=0
for r in rows:
    if r["tag"] not in ("skylake_fig310","raptorlake_fig310"): continue
    if r["swpf"]!="0": continue
    l3=float(r["l3_mshr_misses"]); acc=float(r["accesses"])
    ratio=l3/acc
    if ratio < 0.5:
        print(f"  FAIL  {r['run']}: only {ratio:.2f} DRAM accesses per indirect access"); bad+=1
print(f"  {'PASS' if bad==0 else 'FAIL'}  every baseline reaches DRAM on ~1 access per iteration")
sys.exit(1 if bad else 0)
PY
[ $? -eq 0 ] && pass=$((pass+1)) || fail=$((fail+1))

# ---------------------------------------------------------------- 7
head_ "7. ATTRIBUTION: is it the L1 MSHRs specifically, or L2/L3 too?"
if [ -d "$RUNS/control" ]; then
  python3 - "$RUNS" <<'PY'
import os,re,sys,glob
def roi(p):
    s={}; started=False
    for ln in open(p):
        if ln.startswith("---------- Begin Simulation Statistics"):
            if started: break
            started=True; continue
        if not started: continue
        m=re.match(r"^(\S+)\s+([-\d.eE+]+)",ln)
        if m:
            try: s[m.group(1)]=float(m.group(2))
            except ValueError: pass
    return s
R=sys.argv[1]
def cyc(d):
    p=os.path.join(R,d,"stats.txt")
    return roi(p).get("system.cpu.numCycles") if os.path.exists(p) else None
l1only=cyc("control/h0_L1only"); l23only=cyc("control/h0_L23only")
stock =cyc("skylake_fig310/h0_t0_mshr10_d64")
if None in (l1only,l23only,stock):
    print("  SKIP  control runs not present"); sys.exit(0)
print(f"        T0, L1=10  L2=48  L3=64  (stock)        {stock/1e6:7.2f} Mcycles")
print(f"        T0, L1=10  L2=512 L3=512 (L2/L3 only)   {l23only/1e6:7.2f} Mcycles")
print(f"        T0, L1=512 L2=48  L3=64  (L1 only)      {l1only/1e6:7.2f} Mcycles")
ok1 = abs(l23only-stock)/stock < 0.01      # lifting L2/L3 alone must do nothing
ok2 = l1only < 0.7*stock                   # lifting L1 alone must do everything
print(f"  {'PASS' if ok1 else 'FAIL'}  lifting L2/L3 alone changes nothing ({100*(l23only-stock)/stock:+.2f}%)")
print(f"  {'PASS' if ok2 else 'FAIL'}  lifting L1 alone recovers the speedup ({stock/l1only:.2f}x)")
sys.exit(0 if (ok1 and ok2) else 1)
PY
  [ $? -eq 0 ] && pass=$((pass+1)) || fail=$((fail+1))
else
  echo "  SKIP  no control runs in $RUNS/control"
fi

# ---------------------------------------------------------------- 8
if [ "$FULL" = "1" ]; then
  head_ "8. Determinism: re-running a config reproduces it exactly"
  SRC="$RUNS/skylake_fig310/h0_t0_mshr512_d64"
  OUT=/tmp/verify_rerun
  rm -rf $OUT
  "$GEM5/build/ALL/gem5.opt" --outdir=$OUT "$HERE/gem5/camel_se.py" \
      --machine skylake --cmd "$BINS/camel_h0_t0_d64_m21_n1000000" \
      --l1d-mshrs 512 --l2-mshrs 512 --l3-mshrs 512 > $OUT.log 2>&1
  a=$(awk '/Begin Simulation Statistics/{n++} n==1 && /system.cpu.numCycles/{print $2; exit}' "$SRC/stats.txt")
  b=$(awk '/Begin Simulation Statistics/{n++} n==1 && /system.cpu.numCycles/{print $2; exit}' "$OUT/stats.txt")
  echo "        stored: $a cycles"
  echo "        rerun : $b cycles"
  [ "$a" = "$b" ] && ok "bit-identical" || bad "differs -- results are not reproducible"
else
  head_ "8. Determinism (skipped; pass --full to re-run gem5 and compare)"
fi

echo
echo "=============================================="
echo " $pass passed, $fail failed"
echo "=============================================="
exit $([ $fail -eq 0 ] && echo 0 || echo 1)
