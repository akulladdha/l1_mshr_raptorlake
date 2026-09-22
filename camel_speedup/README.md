# camel_speedup — recreating Kwon Fig. 3.10

Software-prefetch speedup for the Camel micro-benchmark, on a real Raptor Lake
machine and under gem5 with and without the L1 MSHR bottleneck.

Reference: Yongkee Kwon, *Software Prefetching for Memory-level Parallelism*,
UT Austin 2022 — Fig. 3.10 (and 3.3 / 3.4 / 3.8 / 3.9 as supporting shapes).

## Layout

```
src/camel.c          benchmark; one source for MSVC (real machine) and gcc (gem5)
src/check_perm.c     proves the index permutation is a bijection
gem5/camel_se.py     SE-mode config; --machine skylake | raptorlake
gem5/sweep.py        build + run a matrix, resumable, logs into each outdir
gem5/collect.py      all runs -> results/gem5_runs.csv  (single source of truth)
gem5/make_tables.py  raw runs -> results/tables/*.csv, shaped like Kwon's figures
gem5/build_bins.sh   one-off binary builds
gem5/run_gate.sh     the Phase-2 gate experiment
gem5/CONFIG_SOURCES.md  provenance of every machine-config number
docs/LAB_NOTEBOOK.md    methods, problems, resolutions, results  <- read this
results/             CSVs for the spreadsheet
```

## Workflow

```bash
# 1. run a matrix (resumable; re-running skips completed runs)
gem5/sweep.py --machine skylake --hn 0 1 2 3 4 5 \
    --config base:10 t0:10 t0:512 --dist 16 32 64 128 \
    --outroot ~/camel_runs/skylake_fig310 --jobs 8

# 2. fold every run ever done into one tidy CSV
gem5/collect.py ~/camel_runs --out results/gem5_runs.csv

# 3. derive the figure-shaped tables
gem5/make_tables.py --stock-mshrs 10 --ideal-mshrs 512
```

`results/gem5_runs.csv` is one row per simulation with full machine and workload
metadata, read from gem5's own `config.ini` and the benchmark's stdout banner —
never from directory names. Import it into a spreadsheet and pivot freely.

## Results files

| file | what it holds |
|---|---|
| `results/gem5_runs.csv` | every run, raw + derived metrics |
| `results/tables/fig310_*.csv` | Fig 3.10 shape: T0 speedup % and MSHR headroom % per hN |
| `results/tables/fig34_*.csv` | Fig 3.4 shape: realized MLP with/without SWPF per hN |
| `results/tables/pfdist_*.csv` | prefetch-distance sensitivity per MSHR count |
| `results/tables/roofline_*.csv` | MLP vs memory intensity, with ROB and MSHR limit lines |

Speedups are normalized so baseline = 100 %, matching the dissertation's y-axis.

## Status

- gem5 skylake config: **working**, h0 validated against the paper's h0 shape.
- gem5 raptorlake config: **written, never run**.
- Real-machine panel: **not started** — needs an elevated prompt (large pages +
  VTune PMU access).

See `docs/LAB_NOTEBOOK.md` for the full record, including the gate experiment
that disproved the assumption that gem5 needed a prefetch early-retire patch.

## gem5 patch

`earlyRetirePrefetch` (default **off**) was added to gem5's O3 CPU during this
study. It is **not used by any figure** — the gate showed it changes runtime by
< 0.15 %. Applied idempotently by `gem5/patch_gem5.py`; see the notebook.
Note the gem5 tree also carries unrelated pre-existing SRRIP/BRRIP work that this
study does not touch.
