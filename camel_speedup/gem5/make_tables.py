#!/usr/bin/env python3
"""Turn the raw run CSV into spreadsheet-ready tables shaped like Kwon's figures.

Inputs  : results/gem5_runs.csv (produced by collect.py)
Outputs : results/tables/*.csv

Tables produced
  fig310_<tag>.csv     Fig 3.10 left panel: per hN, the T0 speedup at stock
                       MSHRs and the additional headroom when the MSHR limit
                       is removed. Percentages are normalized so baseline=100%,
                       matching the dissertation's y-axis.
  fig34_<tag>.csv      Fig 3.4 shape: realized MLP with and without SWPF per hN.
  pfdist_<tag>.csv     Prefetch-distance sensitivity at each MSHR count.
  roofline_<tag>.csv   MLP vs memory intensity, for the Fig 3.8/3.9 roofline.

Speedups are computed against the baseline (no SWPF) at the SAME stock MSHR
count, which is what "speedup of software prefetching" means in the paper.
"""
import argparse
import csv
import os
from collections import defaultdict


def load(path):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k, v in list(r.items()):
            if v in ("", "None"):
                r[k] = None
                continue
            if k in ("tag", "run", "binary", "mem_type"):
                continue
            if v in ("True", "False"):
                r[k] = (v == "True")
                continue
            try:
                r[k] = float(v)
            except ValueError:
                pass
    return rows


def write(path, fieldnames, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {path} ({len(rows)} rows)")


def best(rows, key="cyc_per_access"):
    """Fastest run in a group -- i.e. the best prefetch distance."""
    return min(rows, key=lambda r: r[key]) if rows else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "results",
        "gem5_runs.csv"))
    ap.add_argument("--outdir", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "results", "tables"))
    ap.add_argument("--stock-mshrs", type=float, default=10,
                    help="the machine's real L1D MSHR count")
    ap.add_argument("--ideal-mshrs", type=float, default=512,
                    help="the idealized (unbottlenecked) L1D MSHR count")
    ap.add_argument("--tag", default=None,
                    help="only use runs with this tag")
    args = ap.parse_args()

    rows = load(args.runs)
    if args.tag:
        rows = [r for r in rows if r["tag"] == args.tag]
    if not rows:
        raise SystemExit("no rows selected")
    tag = args.tag or "all"

    # Only the default (unmodified ROB/LQ) runs belong in the figures; the
    # limiter-hunting runs used inflated structures and would distort them.
    rows = [r for r in rows if r["tag"] != "limiter"]

    by_hn = defaultdict(list)
    for r in rows:
        if r["hash_n"] is not None:
            by_hn[int(r["hash_n"])].append(r)

    # ------------------------------------------------- Fig 3.10 left panel
    fig310 = []
    for hn in sorted(by_hn):
        g = by_hn[hn]
        base = best([r for r in g if not r["swpf"]
                     and r["machine_l1d_mshrs"] == args.stock_mshrs])
        t0 = best([r for r in g if r["swpf"]
                   and r["machine_l1d_mshrs"] == args.stock_mshrs])
        t0i = best([r for r in g if r["swpf"]
                    and r["machine_l1d_mshrs"] == args.ideal_mshrs])
        if not base:
            continue
        row = dict(
            hash_n=hn,
            base_cyc_per_access=round(base["cyc_per_access"], 3),
            base_mlp_l1=round(base["mlp_l1"], 2),
            mem_intensity=round(base["mem_intensity"], 5),
        )
        if t0:
            row.update(
                t0_cyc_per_access=round(t0["cyc_per_access"], 3),
                t0_best_pfdist=t0["pfdist"],
                t0_mlp_l1=round(t0["mlp_l1"], 2),
                t0_speedup_pct=round(
                    100.0 * base["cyc_per_access"] / t0["cyc_per_access"], 1),
            )
        if t0i:
            row.update(
                t0_ideal_cyc_per_access=round(t0i["cyc_per_access"], 3),
                t0_ideal_best_pfdist=t0i["pfdist"],
                t0_ideal_mlp_l1=round(t0i["mlp_l1"], 2),
                t0_ideal_speedup_pct=round(
                    100.0 * base["cyc_per_access"] / t0i["cyc_per_access"], 1),
            )
        if t0 and t0i:
            # The light "headroom" band in Fig 3.10: what removing the MSHR
            # limit adds on top of what T0 already achieves.
            row["mshr_headroom_pct"] = round(
                row["t0_ideal_speedup_pct"] - row["t0_speedup_pct"], 1)
        fig310.append(row)

    cols = ["hash_n", "mem_intensity", "base_cyc_per_access", "base_mlp_l1",
            "t0_cyc_per_access", "t0_best_pfdist", "t0_mlp_l1",
            "t0_speedup_pct", "t0_ideal_cyc_per_access",
            "t0_ideal_best_pfdist", "t0_ideal_mlp_l1",
            "t0_ideal_speedup_pct", "mshr_headroom_pct"]
    write(os.path.join(args.outdir, f"fig310_{tag}.csv"), cols,
          [{c: r.get(c) for c in cols} for r in fig310])

    # ------------------------------------------------------ Fig 3.4 shape
    fig34 = []
    for hn in sorted(by_hn):
        g = by_hn[hn]
        base = best([r for r in g if not r["swpf"]
                     and r["machine_l1d_mshrs"] == args.stock_mshrs])
        t0 = best([r for r in g if r["swpf"]
                   and r["machine_l1d_mshrs"] == args.stock_mshrs])
        if base and t0:
            fig34.append(dict(
                hash_n=hn,
                mlp_base=round(base["mlp_l1"], 3),
                mlp_t0=round(t0["mlp_l1"], 3),
                mlp_gain=round(t0["mlp_l1"] - base["mlp_l1"], 3),
                mlp_l2_base=round(base["mlp_l2"], 3),
                mlp_l2_t0=round(t0["mlp_l2"], 3),
            ))
    write(os.path.join(args.outdir, f"fig34_{tag}.csv"),
          ["hash_n", "mlp_base", "mlp_t0", "mlp_gain",
           "mlp_l2_base", "mlp_l2_t0"], fig34)

    # ------------------------------------------------ prefetch distance
    dist = []
    for r in sorted(rows, key=lambda x: (x["hash_n"] or 0,
                                         x["machine_l1d_mshrs"],
                                         x["pfdist"] or 0)):
        if not r["swpf"]:
            continue
        dist.append(dict(
            hash_n=r["hash_n"], l1d_mshrs=r["machine_l1d_mshrs"],
            pfdist=r["pfdist"],
            early_retire_pf=r["early_retire_pf"],
            run=r["run"], tag=r["tag"],
            cyc_per_access=round(r["cyc_per_access"], 3),
            mlp_l1=round(r["mlp_l1"], 2),
            demand_miss_rate=round(r["demand_miss_rate"], 4),
            dram_gb_per_s=round(r["dram_gb_per_s"], 2),
        ))
    write(os.path.join(args.outdir, f"pfdist_{tag}.csv"),
          ["hash_n", "l1d_mshrs", "pfdist", "early_retire_pf", "tag", "run",
           "cyc_per_access", "mlp_l1", "demand_miss_rate", "dram_gb_per_s"],
          dist)

    # ------------------------------------------------------- roofline
    roof = []
    for r in sorted(rows, key=lambda x: x["mem_intensity"]):
        roof.append(dict(
            run=r["run"], hash_n=r["hash_n"], swpf=r["swpf"],
            l1d_mshrs=r["machine_l1d_mshrs"],
            mem_intensity=round(r["mem_intensity"], 5),
            mlp_l1=round(r["mlp_l1"], 2),
            mlp_l2=round(r["mlp_l2"], 2),
            mlp_rob_limit=round(r["rob"] * r["mem_intensity"], 2),
            mlp_l1_limit=r["machine_l1d_mshrs"],
            dram_gb_per_s=round(r["dram_gb_per_s"], 2),
        ))
    write(os.path.join(args.outdir, f"roofline_{tag}.csv"),
          ["run", "hash_n", "swpf", "l1d_mshrs", "mem_intensity", "mlp_l1",
           "mlp_l2", "mlp_rob_limit", "mlp_l1_limit", "dram_gb_per_s"], roof)


main()
