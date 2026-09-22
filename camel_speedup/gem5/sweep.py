#!/usr/bin/env python3
"""Build and run a Camel sweep under gem5, resumably.

Compiles exactly the (hash_n, swpf, pfdist) binaries a sweep needs, then runs
the gem5 matrix with a bounded job pool. Runs that already produced a region-of-
interest stats dump are skipped, so an interrupted sweep can be restarted
without redoing work.

Example -- the Fig 3.10 matrix on the dissertation's Skylake config:
  ./sweep.py --machine skylake --hn 0 1 2 3 4 5 \
             --config base:10 t0:10 t0:512 --dist 16 32 64 128 \
             --outroot ~/camel_runs/skylake_fig310 --jobs 8
"""
import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src", "camel.c")
GEM5 = os.environ.get("GEM5", os.path.expanduser("~/gem5"))
GEM5_BIN = os.path.join(GEM5, "build", "ALL", "gem5.opt")
LIBM5 = os.path.join(GEM5, "util", "m5", "build", "x86", "out", "libm5.a")
CFG = os.path.join(HERE, "camel_se.py")


def build(bindir, hash_n, swpf, pfdist, log2_m, naccess):
    """Compile one variant; return its path. Cached on the filename."""
    name = (f"camel_h{hash_n}_{'t0' if swpf else 'base'}"
            f"_d{pfdist}_m{log2_m}_n{naccess}")
    out = os.path.join(bindir, name)
    if os.path.exists(out):
        return out
    cmd = ["gcc", "-O2", "-fno-tree-vectorize", "-static", "-DGEM5",
           f"-DHASH_N={hash_n}", f"-DPFDIST={pfdist}",
           f"-DLOG2_M={log2_m}", f"-DNACCESS={naccess}",
           f"-I{os.path.join(GEM5, 'include')}"]
    if swpf:
        cmd.append("-DSWPF")
    cmd += [SRC, LIBM5, "-o", out]
    subprocess.run(cmd, check=True)
    return out


def roi_complete(outdir):
    """True if this run already produced the ROI dump we care about."""
    st = os.path.join(outdir, "stats.txt")
    if not os.path.exists(st):
        return False
    try:
        with open(st) as f:
            return sum(1 for ln in f
                       if ln.startswith("---------- Begin Simulation")) >= 1
    except OSError:
        return False


def run_one(job):
    name, outdir, argv = job
    if roi_complete(outdir):
        return name, "skipped (already complete)"
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "run.log"), "w") as log:
        p = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT)
    ok = roi_complete(outdir)
    return name, ("ok" if ok else f"FAILED (exit {p.returncode}, no ROI dump)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", default="skylake")
    ap.add_argument("--hn", type=int, nargs="+", default=[0, 1, 2, 3, 4, 5])
    ap.add_argument("--config", nargs="+", default=["base:10", "t0:10", "t0:512"],
                    help="kind:l1d_mshrs pairs, kind is base or t0")
    ap.add_argument("--dist", type=int, nargs="+", default=[32],
                    help="prefetch distances to sweep (t0 configs only)")
    ap.add_argument("--log2-m", type=int, default=21)
    ap.add_argument("--naccess", type=int, default=1000000)
    ap.add_argument("--outroot", required=True)
    ap.add_argument("--bindir", default=os.path.expanduser("~/camel_bins"))
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--early-retire-pf", action="store_true")
    ap.add_argument("--extra", nargs="*", default=[],
                    help="extra args passed through to camel_se.py")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.bindir, exist_ok=True)
    os.makedirs(args.outroot, exist_ok=True)

    jobs = []
    for hn in args.hn:
        for spec in args.config:
            kind, _, mshrs = spec.partition(":")
            mshrs = int(mshrs)
            swpf = (kind == "t0")
            # The baseline has no prefetch, so distance is meaningless for it;
            # emit it once rather than once per distance.
            dists = args.dist if swpf else [args.dist[0]]
            for d in dists:
                binpath = build(args.bindir, hn, swpf, d,
                                args.log2_m, args.naccess)
                tail = f"_d{d}" if swpf else ""
                name = f"h{hn}_{kind}_mshr{mshrs}{tail}"
                outdir = os.path.join(args.outroot, name)
                # L2/L3 are lifted alongside L1 in the idealized runs, matching
                # the dissertation's "512 MSHRs at each level".
                lx = 512 if mshrs >= 512 else None
                argv = [GEM5_BIN, f"--outdir={outdir}", CFG,
                        "--machine", args.machine, "--cmd", binpath,
                        "--l1d-mshrs", str(mshrs)]
                if lx:
                    argv += ["--l2-mshrs", str(lx), "--l3-mshrs", str(lx)]
                if args.early_retire_pf:
                    argv.append("--early-retire-pf")
                argv += args.extra
                jobs.append((name, outdir, argv))

    todo = [j for j in jobs if not roi_complete(j[1])]
    print(f"{len(jobs)} runs in matrix, {len(todo)} to execute, "
          f"{len(jobs) - len(todo)} already complete")
    if args.dry_run:
        for n, o, a in jobs:
            print(" ", n, "\n    ", " ".join(a))
        return

    failures = []
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for name, status in ex.map(run_one, jobs):
            print(f"  [{status}] {name}", flush=True)
            if status.startswith("FAILED"):
                failures.append(name)

    print(f"\ndone. {len(failures)} failures.")
    if failures:
        print("failed runs:", ", ".join(failures))
        sys.exit(1)


main()
