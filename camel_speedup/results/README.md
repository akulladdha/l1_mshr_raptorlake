# Results index — what each file is and what to plot from it

Everything here is generated. To rebuild from the raw gem5 output:

```bash
gem5/collect.py ~/camel_runs --out results/gem5_runs.csv       # raw -> tidy
gem5/make_tables.py --machine skylake    --stock-mshrs 10      # -> tables/
gem5/make_tables.py --machine raptorlake --stock-mshrs 16
# add --dist-limit 32 --suffix _kwondist for the paper-comparable variant
```

Raw gem5 output (stats.txt, config.ini, run.log per run; 135 MB) lives in
`~/camel_runs/` inside WSL and is **not** in git. The CSVs here are.

---

## `gem5_runs.csv` — the source of truth

One row per simulation, 156 rows, 40 columns. Everything else is derived from
this. Metadata comes from gem5's own `config.ini` and the benchmark's stdout
banner, not from directory names.

Key columns for pivoting:

| column | meaning |
|---|---|
| `tag` | which batch: `skylake_fig310`, `raptorlake_fig310`, `control`, `gate`, `dist`, `hn`, `limiter` |
| `machine` | `skylake` or `raptorlake` — **always filter on this**, the two must never be mixed |
| `hash_n` | Camel variant h0–h5; h0 is the most memory-intensive |
| `swpf` | 0 = baseline, 1 = T0 software prefetch |
| `pfdist` | prefetch distance in iterations |
| `machine_l1d_mshrs`, `l2_mshrs`, `l3_mshrs` | the knob under test |
| `cyc_per_access` | **the performance metric** — cycles per indirect access |
| `mlp_l1`, `mlp_l2`, `mlp_l3` | realized memory-level parallelism (Little's law) |
| `mem_intensity` | DRAM-reaching loads per committed micro-op (Kwon's definition) |
| `blocked_no_mshrs` | cycles the L1 was blocked with no free MSHR |
| `demand_miss_rate` | fraction of demand loads still missing L1 (prefetch timeliness) |
| `dram_gb_per_s` | achieved DRAM bandwidth |

Speedup is always `base_cyc_per_access / this_cyc_per_access`, comparing against
the no-prefetch baseline **at the same stock MSHR count**.

### Which tags are claims, and which are scratch

| tag | runs | status |
|---|---|---|
| `skylake_fig310` | 66 | **figure data** — the Table 4.2 Skylake sweep |
| `raptorlake_fig310` | 66 | **figure data** — the Raptor-Cove-like sweep |
| `control` | 4 | **figure data** — the L1-vs-L2/L3 attribution control |
| `gate`, `dist`, `hn`, `limiter` | 20 | exploratory runs from Phases 1–2; superseded by the sweeps. Kept for the record, not for plotting |

---

## `tables/` — pre-pivoted, one chart each

Each exists in four variants: `_skylake`, `_raptorlake`, and `_kwondist` versions
of both. `_kwondist` restricts prefetch distance to ≤32, which is the range Kwon
reports as optimal (§4.4) — **use `_kwondist` when comparing to the paper**, and
the unrestricted version when asking what the hardware can actually do.

### `fig310_*.csv` → the main figure
Kwon Fig 3.10. One row per hN. Plot a **stacked bar per hN**:

- dark segment = `t0_speedup_pct` (what prefetch achieves at stock MSHRs)
- light segment on top = `mshr_headroom_pct` (what removing the MSHR limit adds)
- total height = `t0_ideal_speedup_pct`
- y-axis in %, baseline = 100%

Also carries `base_mlp_l1` / `t0_mlp_l1` / `t0_ideal_mlp_l1` and the winning
distance per point, so you can annotate bars.

### `fig34_*.csv` → MLP with and without prefetch
Kwon Fig 3.4. Stacked bar per hN: `mlp_base` at the bottom, `mlp_gain` on top.
Shows prefetch raising MLP until it hits the MSHR ceiling.

### `attribution_*.csv` → is it really the L1?
The control. Five bars per hN, all at prefetch distance 64:
`baseline_no_swpf`, `t0_stock`, `t0_l2l3_only`, `t0_l1_only`, `t0_all_levels`,
each with `_cyc_per_access`, `_mlp_l1`, `_speedup`.

**This is where the "L2/L3 only = +0.00%, L1 only = 2.32×" table comes from.**
Only h0 and h1 have the control columns filled — the control was not run for
h2–h5, and blank cells mean "not measured", not "zero".

### `pfdist_*.csv` → prefetch distance sensitivity
One row per (hN, MSHR count, distance). Plot `cyc_per_access` or `mlp_l1`
against `pfdist`, one line per MSHR count. Shows MLP tracking distance almost
1:1 when MSHRs are free, and being completely flat when they are not.
`demand_miss_rate` on the same axes shows L1 thrashing appearing at distance 256.

### `roofline_*.csv` → Kwon Fig 3.8 / 3.9
Scatter `mlp_l1` (y) against `mem_intensity` (x), one point per run. Overlay two
limit lines, both already computed per row:

- `mlp_rob_limit` = ROB depth x memory intensity (the diagonal)
- `mlp_l1_limit` = the L1 MSHR count (the horizontal ceiling)

Points below the diagonal are ROB-bound; points pinned to the horizontal are
MSHR-bound. `dram_gb_per_s` lets you add the bandwidth roof.

---

## Caveats that affect how you read these

- **Comparisons to Kwon's numbers were read off his figure by eye**, not from a
  table. Treat "matches within a few percent" as "same shape, right ballpark".
- **No real-hardware data yet.** Everything here is simulation.
- **The hN ladder is not evenly spaced** — 5, 17, 22, 46, 59, 72 instructions per
  iteration, because GCC unrolls the hash chain differently at different N. This
  does not contaminate any speedup (base and T0 are the same binary plus one
  prefetch, verified), but plot against `mem_intensity`, not against hN, when
  the spacing matters.
- **gem5 reaches only ~14.8 GB/s**, 38% of the modelled peak, on this random
  access stream. Plausible, but unvalidated against real memory.
- `verify.sh` in the parent directory re-checks all of the above claims.
