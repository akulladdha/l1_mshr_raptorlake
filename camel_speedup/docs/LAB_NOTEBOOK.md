# Camel / MLP study — lab notebook

Chronological record of what was run, what broke, how it was fixed, and what the
numbers were. Newest phase at the bottom. Every claim here should be traceable to
a row in `results/gem5_runs.csv`.

**Goal:** recreate Figure 3.10 of Kwon, *Software Prefetching for Memory-level
Parallelism* (UT Austin, 2022) — software-prefetch speedup for the Camel
micro-benchmark on a real machine (right panel) and on a simulated platform with
and without the L1 MSHR limit (left panel) — and extend it to this machine.

**Target machine (real):** Intel Core 7 240H (Raptor Lake-H refresh), 6 P-cores
(Raptor Cove, 1.25 MB L2 each) + 4 E-cores (2 MB cluster L2), 24 MB L3, 32 GB
DDR5-5600, 2.5 GHz base. Mobile part — power state materially affects results.

---

## Phase 0 — prior work in this repo

`mshr_count/` and the root `README.md` already establish the number this study
builds on. An interleaved pointer-chase (`mlp_chase.c`) over a 512 MB pool, with
2 MB large pages and VTune counters, shows:

- **Demand misses in flight plateau at 13.8–13.9** (N = 20 … 128), *not* 16.
- `L1D_PEND_MISS.FB_FULL` climbs from ≈0 to **83–89 % of cycles** as the plateau
  forms — the hardware says outright that it is waiting on the fill buffer.
- Miss latency is flat at ~85 ns and DRAM bandwidth only ~10 GB/s, so the limit
  is at the L1, not downstream. Zero page walks, so not the TLB either.
- Little's law closes: 85 ns ÷ 13.9 ≈ 6.1 ns/load = the measured plateau.

Earlier drafts of this notebook said "14–16 MSHRs" from the timing knee alone.
That was imprecise: **16** is the architectural LFB count, **~13.9** is what
demand loads actually achieve, and the ~2-entry gap is an open question in the
root README (its next-test 2 is designed to resolve it). See
`gem5/CONFIG_SOURCES.md` for which number the simulator config uses and why.

**Problem noted:** `sweep.txt` and `sweep_plugged.txt` disagree badly at N=1
(148.2 vs 101.9 ns/load) — battery vs AC. The root README quantifies it at ~30 %
and also flags long N=1 runs outlasting turbo. Any timing on this laptop must be
on AC in Best-performance mode, with achieved frequency recorded. Not yet
enforced here; see Open Issues.

---

## Phase 1 — gem5 software-prefetch semantics

### Question
Kwon's left panel depends on software prefetch behaving the way real x86 does:
a SWPF retires as soon as it is issued (§3.4.3 — "prefetch instructions are
eligible to retire once issued, which increases the effective ROB depth in terms
of MLP"), while still *stalling* when the L1 has no free MSHR (Intel waits; AMD
drops). If gem5 models this differently the whole panel is meaningless.

### What the source says (gem5 25.1.0.1, commit cbf0eae213)
| Behaviour | Where | Verdict |
|---|---|---|
| `PREFETCH_T0` → `ld t0, …, dataSize=1, prefetch=True` | `two_byte_opcodes.isa:277`, `cache_and_memory_management.py` | real memory op |
| sets `Request::PREFETCH` + `StaticInst::IsDataPrefetch` | `ldstop.isa:310-312` | allocates an L1 MSHR on miss |
| MSHR-full → `setBlocked(Blocked_NoMSHRs)`, cache refuses, LSQ retries | `base.cc`, `lsq_unit.cc:1222` | **Intel-like: waits, does not drop.** No patch needed |
| commit requires `head_inst->isExecuted()`, and a load is only executed on data return | `commit.cc:1119` | *predicted* a ROB-head stall — see Phase 2 |

### Patch written (`earlyRetirePrefetch`, default **off**)
Six hunks, applied idempotently by `gem5/patch_gem5.py`:
1. `BaseO3CPU.py` — new `earlyRetirePrefetch` param.
2/3. `lsq_unit.hh` — member + `earlyRetiredPrefetches` stat.
4/5. `lsq_unit.cc` — init the member, register the stat.
6. `lsq_unit.cc::trySendPacket` — on the **success** path only, mark a data
   prefetch executed and send it to commit. Success-path-only is deliberate: if
   the L1 refuses the request for want of an MSHR, the prefetch still stalls,
   preserving Intel behaviour.

**Problem:** first run tripped
`MemDepUnit::completed(): Assertion 'hash_it != memDepHash.end()' failed`.
**Cause:** when the fill returns *before* commit retires the prefetch,
`LSQUnit::writeback()` guards `setExecuted()`/`completeAcc()` behind
`!isExecuted()` but calls `instToCommit()` unconditionally — so the instruction
reached commit twice and the dependence-unit entry was erased twice.
**Fix:** hunk 7 — early-return from `writeback()` for an already-executed data
prefetch when the flag is on.

**Non-problem, verified rather than assumed:** a late response for a retired
prefetch does *not* leak or use-after-free. gem5 already handles it —
`release()` keeps the request alive while packets are outstanding
(`lsq.hh:329`), `LSQUnit::recvTimingResp` skips released requests
(`lsq_unit.cc:99`), and `packetReplied()` deletes unconditionally
(`lsq.cc:477`).

---

## Phase 2 — the gate experiment

Config: gem5 `skylake` (Kwon Table 4.2 — 6-wide, ROB 224, LQ 72, SQ 48, 4 GHz,
L1D 32 KB/4 cyc/**10 MSHRs**, L2 256 KB/12 cyc, L3 2 MB/40 cyc, DDR4-2400 ×2,
stride prefetchers at L1/L2). Workload: Camel **h0**, 16 MB data array vs 2 MB L3
(8×), 1 M indirect accesses, prefetch distance 32.

### Gate Q2 — is the L1 MSHR count the MLP bottleneck? **PASS, decisively**

| config | cyc/access | MLP@L1 | MSHR-full stall cycles | speedup |
|---|---|---|---|---|
| baseline, 10 MSHRs | 42.34 | 9.92 | 39.5 M of 42.3 M (93 %) | 1.00× |
| T0, 10 MSHRs | 42.29 | 9.94 | 39.8 M | **1.001×** |
| T0, 512 MSHRs | 20.02 | 30.53 | **0** | **2.12×** |

MLP pinned at 9.92 against a 10-MSHR ceiling. Software prefetch buys nothing
until the ceiling is lifted, then 2.1×. This is the h0 shape of Fig 3.10, where
Kwon shows ~218 % for h0 with essentially all of it in the 512-MSHR band.

### Gate Q1 — is the early-retire patch needed? **NO — prediction was wrong**

The patch fires (1,000,000 early retirements, confirmed by the new stat) but
changes runtime by **< 0.15 % in every configuration tested**: h0/h3/h5, 10 and
512 MSHRs, and with LQ raised to 512 and ROB to 1024.

| run | cyc/access | MLP | LQ occ | ROB-full events |
|---|---|---|---|---|
| t0 512 MSHRs, no patch | 20.022 | 30.53 | 0.96 | 50 |
| t0 512 MSHRs, patch | 19.998 | 30.53 | 0.96 | 53 |
| + LQ 192, no patch | 20.019 | 30.53 | 0.36 | 50 |
| + LQ 512 / ROB 1024, no patch | 20.017 | 30.50 | 0.13 | **0** |
| + LQ 512 / ROB 1024, patch | 20.017 | 30.53 | 0.13 | **0** |

With ROB-full events at zero and the ROB quadrupled, nothing moves. The ROB was
never the binding constraint, so early retirement has nothing to relieve. The
prediction in the plan that this patch was a *prerequisite* was a guess, and the
gate disproved it.

**Disposition:** patch stays in the tree, default **off**, not used for any
figure. It is correct and cheap to keep; it is not load-bearing. Mechanism note:
it remains unexplained *why* a prefetch waiting at the ROB head does not throttle
MLP given `commit.cc:1119`. Not pursued further because the empirical answer is
robust across three memory intensities and four ROB/LQ sizes.

### Unplanned finding — prefetch distance sets MLP once MSHRs are free

| distance | @10 MSHRs | @512 MSHRs |
|---|---|---|
| 32 | 42.29 cyc, MLP 9.94 | 20.02 cyc, MLP 30.5 |
| 64 | 42.29 cyc, MLP 9.94 | 18.35 cyc, MLP 42.7 |
| 128 | 42.38 cyc, MLP 9.94 | 17.11 cyc, MLP 68.8 |
| 256 | — | 16.60 cyc, MLP 113.1 |

MLP tracks distance nearly 1:1 when MSHRs are free and is completely insensitive
to it when they are not. **Consequence for the figure:** the headroom bar's
height depends on distance tuning — h0 headroom is 2.12× at d32 but 2.55× at
d256. Distance must therefore be swept per point and the best taken, which is
what Kwon does (§4.4: "I choose the best prefetch distances for each
application").

### Low-memory-intensity spot checks (patch on vs off, 512 MSHRs)
| hN | baseline @10 | T0 @512 | speedup | patch delta |
|---|---|---|---|---|
| h3 | 153.81 | 45.22 | 3.40× | 0.02 % |
| h5 | 228.26 | 78.29 | 2.92× | 0.01 % |

`t0 @10 MSHRs` for h3/h5 was **not** run, so their Fig 3.10 bars are still
incomplete.

---

## Phase 3 — full Skylake sweep (Fig 3.10 left panel)

Matrix: h0–h5 × {base@10 MSHRs, T0@10, T0@512} × prefetch distance
{16, 32, 64, 128, 256} = **66 runs, 12 parallel, 0 failures**, ~55 min wall.
`~/camel_runs/skylake_fig310`, tag `skylake_fig310` in `results/gem5_runs.csv`.

### Result — unlimited distance search (`fig310_skylake.csv`)

| hN | mem intensity | base cyc/acc | T0 % | T0 MLP | T0+512 % | T0+512 MLP | headroom % |
|---|---|---|---|---|---|---|---|
| h0 | 0.1065 | 42.34 | **100.1** | 9.94 | 255.1 | 113.1 | 155.0 |
| h1 | 0.0399 | 74.77 | 176.0 | 9.89 | 443.4 | 81.8 | 267.4 |
| h2 | 0.0291 | 107.32 | 251.8 | 9.84 | 495.2 | 26.0 | 243.4 |
| h3 | 0.0137 | 153.79 | 315.6 | 8.33 | 346.4 | 9.73 | 30.8 |
| h4 | 0.0107 | 211.81 | 333.4 | 6.17 | 345.4 | 6.66 | 12.0 |
| h5 | 0.0087 | 228.26 | 283.3 | 4.78 | 291.5 | 5.04 | 8.2 |

Both of Kwon's qualitative claims reproduce cleanly:

1. **At high memory intensity, prefetch alone does nothing.** h0 T0 = 100.1 % —
   literally zero benefit — because MLP is pinned at 9.94 against the 10-MSHR
   ceiling. All of h0's available gain sits behind the MSHR wall.
2. **Headroom collapses as memory intensity falls**: 155 % → 267 % → 243 % →
   30.8 % → 12.0 % → 8.2 %. By h3 the MSHR limit no longer binds (MLP 8.33 < 10)
   and extra MSHRs buy almost nothing — Kwon: *"Increasing MSHR entries has
   little benefit when the ROB limit dominates, as in the case of h3."*

### Result — Kwon's distance search space (`fig310_skylake_kwondist.csv`)

Kwon reports optimal T0 distances of **16 or 32** (§4.4). Restricting to that
range makes the comparison like-for-like:

| hN | T0 % (ours) | T0 % (Kwon, read off Fig 3.10) | T0+512 % (ours) | T0+512 % (Kwon) |
|---|---|---|---|---|
| h0 | **100.1** | ~100 | **211.7** | ~218 |
| h1 | **176.0** | ~180 | **344.2** | ~335 |
| h2 | 251.7 | ~265 | 445.9 | ~310 |
| h3 | 315.6 | ~280 | 340.1 | ~285 |

h0 and h1 land within a few percent on **both** bars. h2/h3 track in shape but
our idealized bars run high.

**Likely cause of the h2/h3 over-estimate:** we model Skylake with **10** L1
MSHRs, while Kwon's simulated figures are run on his default gem5-Zen2 with
**16** (§4.4). A 10-MSHR baseline hits the wall sooner, so lifting it yields
more. This is a config choice, not an error, but it means our left panel is
*not* numerically his. Worth a Zen2-style 16-MSHR variant later.

### Secondary observations

- **DRAM bandwidth saturates near 14.8 GB/s**, ~38 % of the 38.4 GB/s peak of
  2ch DDR4-2400. Expected for a random 64 B access stream (essentially zero
  row-buffer hits). Consequence: past MLP ≈ 70 extra concurrency buys almost
  nothing — h0 d128→d256 grows MLP 68.8 → 113 (+64 %) for +3 % bandwidth and
  +3 % performance. We are in the queueing regime, where added MLP inflates
  latency instead of throughput.
- **Distance 256 causes L1 thrashing.** Demand miss rate jumps at d256 —
  h3: 0.0012 (d64) → 0.204 (d256); h4: 0.0011 → 0.208 — because 256 in-flight
  prefetched lines do not fit in a 32 kB / 512-line L1 alongside the `idx[]`
  stream. The best-distance picker correctly avoids d256 for h3–h5. For h0/h1 it
  still picks d256, where the net is slightly faster despite 15 % of demand
  loads missing; that is a real but marginal win and is why the distance-limited
  table exists.
- **Best distance falls as memory intensity falls**: h0/h1 want 256, h2 128,
  h3/h4 64, h5 32. Longer loops need fewer iterations of lead time.

---

## Phase 4 — Raptor-Cove-like sweep (the machine we actually have)

Same matrix, `--machine raptorlake`, stock L1D MSHRs = **16** (the architectural
LFB count measured in `mshr_count/`): 66 runs, 12 parallel, **0 failures**,
~50 min. `~/camel_runs/raptorlake_fig310`.

Config smoke-tested first and parameters verified out of `config.ini` before the
sweep: ROB 512, LQ/SQ 192/114, clock 204 ticks (4.9 GHz), L1D 48 kB/12-way/16
MSHRs/5 cyc, L2 1280 kB/16 cyc, L3 3 MB/50 cyc, stride prefetchers on.

### Skylake vs Raptor Lake, side by side (unlimited distance)

| hN | intensity | SKY baseMLP | SKY T0 % | SKY ideal % | SKY head % | RPL baseMLP | RPL T0 % | RPL ideal % | RPL head % |
|---|---|---|---|---|---|---|---|---|---|
| h0 | 0.1065 | 9.9 | 100.1 | 255.1 | 155.0 | **15.9** | **100.5** | 183.2 | 82.7 |
| h1 | 0.0399 | 5.2 | 176.0 | 443.4 | 267.4 | 5.0 | 250.9 | 463.1 | 212.2 |
| h2 | 0.0290 | 3.5 | 251.8 | 495.2 | 243.4 | 3.4 | 352.7 | 560.0 | 207.3 |
| h3 | 0.0137 | 2.4 | 315.6 | 346.4 | 30.8 | 2.3 | 368.1 | 393.4 | 25.3 |
| h4 | 0.0106 | 1.7 | 333.4 | 345.4 | 12.0 | 1.7 | 374.7 | 389.2 | 14.5 |
| h5 | 0.0087 | 1.6 | 283.3 | 291.5 | 8.2 | 1.6 | 320.5 | 327.9 | 7.4 |

### What this says

1. **The MSHR bottleneck survives the move to Raptor Lake.** h0 baseline MLP
   pins at **15.86** against the 16-MSHR ceiling, and T0 prefetch delivers
   **100.5 %** — i.e. nothing at all — despite a ROB more than twice Skylake's
   (512 vs 224) and DDR5 instead of DDR4. Lifting the MSHR cap still yields
   1.83×. The limiter is the fill buffer, not the out-of-order window. This
   corroborates the repo's original headline ("bottleneck still exists") from the
   hardware side, now with a mechanism attached.
2. **More MSHRs shrink the headroom but do not remove it.** h0 headroom falls
   155 % → 82.7 % going 10 → 16 MSHRs. h1/h2 still leave 207–212 % on the table.
3. **This explains the Phase-3 h2/h3 over-estimate against Kwon.** The hypothesis
   there was that our 10-MSHR Skylake exaggerated headroom relative to his
   16-MSHR Zen2. Re-running at 16 MSHRs drops every headroom figure, in the
   predicted direction and by a large margin — supporting that explanation.
4. **Raptor Lake gets more out of prefetch wherever it is not MSHR-bound**: h1
   250.9 % vs Skylake's 176.0 %, h2 352.7 % vs 251.8 %. The bigger ROB and faster
   memory pay off — but only once MLP is below the MSHR ceiling.

### First validation datapoint for the raptorlake config

Simulated h0 baseline MLP = **15.86**; the real machine measures demand misses in
flight plateauing at **13.8–13.9** (root README, VTune). The simulator reaches
slightly higher because nothing else competes for the fill buffer, which is
exactly the ~2-entry gap the root README flags as an open question. Directionally
consistent, and the right order — but this is one point, not the promised
`mlp_chase` N-sweep validation, which remains outstanding.

---

## Problems encountered and resolutions

| # | Problem | Resolution |
|---|---|---|
| 1 | Quoting broke through the `wsl.exe -e bash -lc` bridge | Write scripts to files, invoke by path |
| 2 | WSL terminated between tool calls and wiped `/tmp`, destroying run output | All persistent output under `~/camel_runs`; long matrices run inside one invocation |
| 3 | `numIQEntries` rejected — gem5 25.1 moved IQ sizing to a vector of `IQUnit` objects | `cpu.instQueues = [IQUnit(numEntries=…)]` |
| 4 | `branchPred = TAGE()` rejected — `BranchPredictor` is now a container | `BranchPredictor(conditionalBranchPred=TAGE(numThreads=1))` |
| 5 | Hand-rolled memory-channel interleaving was wrong | Copied the idiom from `configs/common/MemConfig.py` (`intlvHighBit = 6 + intlv_bits - 1`) |
| 6 | Double `instToCommit()` for early-retired prefetches | Patch hunk 7 (above) |
| 7 | **Init dominated simulated time** — 15 min without reaching the ROI. Fisher-Yates with a 64-bit modulo per element cost more than the measured region | Replaced with a closed-form bijection on `[0,2^lg)` (odd-multiply + `x ^= x>>s`, both invertible mod 2^k). One division-free pass. Init now < 150 s |
| 8 | Is that bijection actually a permutation? | `src/check_perm.c` verifies exhaustively for lg = 8…24: zero duplicates, zero missing, ≤ 5 fixed points. Verified before use |
| 9 | MLP figures came out ~2480 — nonsense against a 10-MSHR cap | gem5 reports MSHR latency in **ticks**, not cycles. Collector now divides by this run's own `simTicks/numCycles` rather than assuming a clock |
| 10 | `grep -c prefetcht0` showed 132 hits in the *baseline* binary | Static libc contains prefetches. Disassemble `main` only: baseline 0, T0 exactly 1, inside the ROI loop |
| 11 | Two parsers (`parse_stats.py`, ad-hoc greps) risked drifting | Deleted `parse_stats.py`; `collect.py` is the single source of truth and reads gem5's own `config.ini`, not directory names |
| 12 | **Memory intensity was ~1.8× too high**, shifting our whole hN ladder off Kwon's x-axis and making h2 look like his h1 | Kwon defines intensity per committed **micro-op** (§3.3); we were dividing by *instructions*. gem5 reports both (`numOps` 9.0 M vs `numInsts` 5.0 M for h0). Switched to `numOps`: h0 0.192 → **0.107** vs Kwon's ~0.10, and the whole ladder now aligns with his Fig 3.9 x-axis |
| 13 | `config.ini` records the DRAM type as the generic C++ class `DRAMInterface`, so skylake and raptorlake runs were indistinguishable in the CSV — they would have silently mixed once both existed | `collect.py` now takes the model name from the `[camel_se] machine=…` line the config prints, adding a `machine` column; `make_tables.py` refuses to emit a table spanning two machines unless `--machine` picks one |
| 14 | Taking the best of distances up to 256 is **not** the experiment Kwon ran (he reports 16 or 32 as optimal), so our headroom bars were inflated relative to his | Added `--dist-limit` / `--suffix`; we now publish both `fig310_skylake.csv` (unlimited) and `fig310_skylake_kwondist.csv` (≤32). The paper comparison uses the latter |

---

## Open issues / not yet done

- **Real-machine panel not started.** Needs an elevated x64 Native Tools prompt
  (large pages via `SeLockMemoryPrivilege`, and VTune's PMU driver).
- **AC power / frequency pinning not yet enforced**, despite Phase 0 showing a
  45 % swing between battery and AC.
- **`raptorlake` gem5 config has never been executed** — written only.
- **`t0 @stock MSHRs` missing for h1–h5**, so only h0 has a complete Fig 3.10 triple.
- **Single sample per point**, no run-to-run variance yet.
- **Working set is 16 MB vs a 2 MB modelled L3 (8×)**; Kwon uses far larger sets.
  Real machine will use a set sized against its real 24 MB L3.
- Memory intensity of our hN ladder does not exactly match Kwon's (ours: h0 0.192,
  h3 0.021, h5 0.013; his h0 ≈ 0.17, h3 ≈ 0.03). Our hash is slightly heavier.
  The ladder spans the same regime, but points are not 1:1 comparable by name.
