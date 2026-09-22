#!/usr/bin/env python3
"""Collect gem5 Camel runs into one tidy CSV for spreadsheet analysis.

Every row is one simulation. Metadata is taken from what gem5 actually ran --
config.ini for the machine, and the benchmark's own stdout banner for the
workload -- rather than from directory names, so a mislabelled run directory
cannot silently corrupt the dataset.

Only the FIRST stats dump is read: that is the region of interest, delimited by
m5_reset_stats()/m5_dump_stats() around the hot loop.

Usage:
  collect.py --out ../results/gem5_runs.csv ~/camel_runs
  collect.py --out ... --tag gate ~/camel_runs/gate     # tag a subset
"""
import argparse
import configparser
import csv
import os
import re
import sys

WORKLOAD_RE = re.compile(
    r"hash_n=(\d+)\s+swpf=(\d+)\s+pfdist=(\d+)\s+log2_m=(\d+)\s+"
    r"accesses=(\d+)\s+([\d.]+)\s+ns/access")

# camel_se.py announces which machine model it instantiated. config.ini only
# records the generic C++ type ("DRAMInterface"), so it cannot tell skylake and
# raptorlake apart -- this can.
MACHINE_RE = re.compile(r"\[camel_se\]\s+machine=(\S+)")


# --------------------------------------------------------------- stats.txt
def read_roi_stats(path):
    """Return {stat: float} for the first dump window (the ROI)."""
    stats, started = {}, False
    with open(path) as f:
        for line in f:
            if line.startswith("---------- Begin Simulation Statistics"):
                if started:
                    break
                started = True
                continue
            if line.startswith("---------- End Simulation Statistics"):
                if started:
                    break
                continue
            if not started:
                continue
            m = re.match(r"^(\S+)\s+([-\d.eE+]+)", line)
            if m:
                try:
                    stats[m.group(1)] = float(m.group(2))
                except ValueError:
                    pass
    return stats


# -------------------------------------------------------------- config.ini
def read_config(path):
    cp = configparser.ConfigParser(strict=False)
    cp.read(path)

    def g(section, key, cast=str, default=None):
        try:
            return cast(cp.get(section, key))
        except Exception:
            return default

    # The DRAM class name is the section's "type".
    mem_type = None
    for s in cp.sections():
        if re.match(r"^system\.mem_ctrls\d*\.dram$", s):
            mem_type = g(s, "type")
            break

    return dict(
        rob=g("system.cpu", "numROBEntries", int),
        lq=g("system.cpu", "LQEntries", int),
        sq=g("system.cpu", "SQEntries", int),
        width=g("system.cpu", "fetchWidth", int),
        early_retire_pf=(g("system.cpu", "earlyRetirePrefetch") == "true"),
        l1d_mshrs=g("system.cpu.dcache", "mshrs", int),
        l1d_size=g("system.cpu.dcache", "size", int),
        l2_mshrs=g("system.l2", "mshrs", int),
        l2_size=g("system.l2", "size", int),
        l3_mshrs=g("system.l3", "mshrs", int),
        l3_size=g("system.l3", "size", int),
        ticks_per_cycle=g("system.clk_domain", "clock", int),
        mem_type=mem_type,
        binary=os.path.basename(
            (g("system.cpu.workload", "cmd") or
             g("system.cpu.workload0", "cmd") or "").strip("[]' ").split()[0]
            if (g("system.cpu.workload", "cmd") or
                g("system.cpu.workload0", "cmd")) else ""),
        hwp_l1=("system.cpu.dcache.prefetcher" in cp.sections()),
    )


# ------------------------------------------------------------------- log
def find_log(rundir):
    cands = [os.path.join(rundir, "run.log"),
             os.path.join(rundir, "simout.txt"),
             rundir.rstrip("/") + ".log"]
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def read_workload(rundir):
    log = find_log(rundir)
    if not log:
        return {}
    with open(log, errors="replace") as f:
        txt = f.read()
    out = {}
    mm = MACHINE_RE.search(txt)
    if mm:
        out["machine"] = mm.group(1)
    m = WORKLOAD_RE.search(txt)
    if not m:
        return out
    out.update(
        hash_n=int(m.group(1)),
        swpf=int(m.group(2)),
        pfdist=int(m.group(3)),
        log2_m=int(m.group(4)),
        accesses=int(m.group(5)),
        host_ns_per_access=float(m.group(6)),
    )
    return out


# ------------------------------------------------------------------- row
def build_row(rundir, tag):
    stats_path = os.path.join(rundir, "stats.txt")
    cfg_path = os.path.join(rundir, "config.ini")
    if not (os.path.exists(stats_path) and os.path.exists(cfg_path)):
        return None
    s = read_roi_stats(stats_path)
    if not s:
        return None
    cfg = read_config(cfg_path)
    wl = read_workload(rundir)

    cycles = s.get("system.cpu.numCycles", 0.0)
    if not cycles:
        return None
    tpc = cfg["ticks_per_cycle"] or 1

    def val(*names):
        for n in names:
            if n in s:
                return s[n]
        return 0.0

    insts = val("system.cpu.commitStats0.numInsts", "system.cpu.committedInsts")
    uops = val("system.cpu.commitStats0.numOps") or insts
    l1d_mshr_miss = val("system.cpu.dcache.overallMshrMisses::total")
    l2_mshr_miss = val("system.l2.overallMshrMisses::total")
    l3_mshr_miss = val("system.l3.overallMshrMisses::total")

    # Realized MLP by Little's law. gem5 reports latency in ticks.
    def mlp(misses, lat_ticks):
        return misses * (lat_ticks / tpc) / cycles if cycles else 0.0

    mlp_l1 = mlp(l1d_mshr_miss,
                 val("system.cpu.dcache.overallAvgMshrMissLatency::total"))
    mlp_l2 = mlp(l2_mshr_miss,
                 val("system.l2.overallAvgMshrMissLatency::total"))
    mlp_l3 = mlp(l3_mshr_miss,
                 val("system.l3.overallAvgMshrMissLatency::total"))

    dem_hits = val("system.cpu.dcache.demandHits::total")
    dem_miss = val("system.cpu.dcache.demandMisses::total")
    dem_tot = dem_hits + dem_miss

    # Indirect accesses actually performed in the ROI. Prefer the benchmark's
    # own count; fall back to L1D MSHR misses.
    accesses = wl.get("accesses") or l1d_mshr_miss

    sim_seconds = val("simSeconds")
    dram_bytes = l3_mshr_miss * 64.0
    dram_gbs = (dram_bytes / sim_seconds / 1e9) if sim_seconds else 0.0

    # Memory intensity as Kwon defines it (§3.3): DRAM-reaching loads per
    # committed MICRO-op, not per instruction. On x86 the two differ by ~1.8x
    # here, which shifts the whole hN ladder and breaks comparison with the
    # paper. L3 MSHR misses stand in for "loads that reach DRAM".
    mem_intensity = (l3_mshr_miss / uops) if uops else 0.0

    return dict(
        tag=tag,
        run=os.path.basename(rundir.rstrip("/")),
        machine=wl.get("machine", "unknown"),
        machine_l1d_mshrs=cfg["l1d_mshrs"],
        l2_mshrs=cfg["l2_mshrs"],
        l3_mshrs=cfg["l3_mshrs"],
        rob=cfg["rob"], lq=cfg["lq"], sq=cfg["sq"], width=cfg["width"],
        l1d_size=cfg["l1d_size"], l2_size=cfg["l2_size"],
        l3_size=cfg["l3_size"], mem_type=cfg["mem_type"],
        hwp_l1=cfg["hwp_l1"],
        early_retire_pf=cfg["early_retire_pf"],
        binary=cfg["binary"],
        hash_n=wl.get("hash_n"), swpf=wl.get("swpf"),
        pfdist=wl.get("pfdist"), log2_m=wl.get("log2_m"),
        accesses=accesses,
        cycles=cycles,
        insts=insts,
        uops=uops,
        ipc=insts / cycles,
        upc=uops / cycles,
        cyc_per_access=cycles / accesses if accesses else 0.0,
        mlp_l1=mlp_l1, mlp_l2=mlp_l2, mlp_l3=mlp_l3,
        mem_intensity=mem_intensity,
        demand_miss_rate=(dem_miss / dem_tot) if dem_tot else 0.0,
        l1d_mshr_misses=l1d_mshr_miss,
        l3_mshr_misses=l3_mshr_miss,
        dram_gb_per_s=dram_gbs,
        lq_occupancy=val("system.cpu.lsq0.lqAvgOccupancy"),
        rob_full_events=val("system.cpu.rename.ROBFullEvents"),
        blocked_no_mshrs=val("system.cpu.dcache.blockedCycles::no_mshrs"),
        blocked_no_targets=val("system.cpu.dcache.blockedCycles::no_targets"),
        blocked_by_cache=val("system.cpu.lsq0.blockedByCache"),
        early_retired_pf=val("system.cpu.lsq0.earlyRetiredPrefetches"),
        sim_seconds=sim_seconds,
    )


def walk_runs(roots):
    for root in roots:
        for dirpath, _dirnames, filenames in os.walk(root):
            if "stats.txt" in filenames and "config.ini" in filenames:
                yield dirpath


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+", help="directories to scan")
    ap.add_argument("--out", required=True, help="CSV to write")
    ap.add_argument("--tag", default=None,
                    help="label for these runs (default: parent dir name)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    rows = []
    for d in sorted(walk_runs(args.roots)):
        tag = args.tag or os.path.basename(os.path.dirname(d.rstrip("/")))
        r = build_row(d, tag)
        if r:
            rows.append(r)
        elif not args.quiet:
            print(f"  [skip] {d} (no ROI dump)", file=sys.stderr)

    if not rows:
        sys.exit("no parseable runs found")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    if not args.quiet:
        cols = ["tag", "run", "hash_n", "swpf", "pfdist", "machine_l1d_mshrs",
                "early_retire_pf", "cyc_per_access", "mlp_l1",
                "demand_miss_rate", "blocked_no_mshrs"]
        widths = [max(len(c), max(len(str(r.get(c, ""))) for r in rows)) + 2
                  for c in cols]
        print("".join(c.ljust(w) for c, w in zip(cols, widths)))
        print("".join("-" * (w - 1) + " " for w in widths))
        for r in sorted(rows, key=lambda x: (x["tag"], str(x["hash_n"]),
                                             x["machine_l1d_mshrs"],
                                             str(x["pfdist"]))):
            cells = []
            for c, w in zip(cols, widths):
                v = r.get(c, "")
                if isinstance(v, float):
                    v = f"{v:.3f}" if abs(v) < 1e5 else f"{v:.0f}"
                cells.append(str(v).ljust(w))
            print("".join(cells))
        print(f"\n{len(rows)} runs -> {args.out}")


main()
