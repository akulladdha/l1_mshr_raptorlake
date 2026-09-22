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
