# L1 Fill-Buffer (MSHR) Count on a Raptor Lake P-core

**Question:** How many L1D misses can one P-core keep in flight at once, and is the L1 line fill buffer (LFB) what limits it?

**Answer so far:** Demand-load misses in flight plateau at **~13–14**, with the fill buffer full on 83–89% of cycles. Miss latency stays flat (~85 ns), so the limit is at the L1, not downstream.

Machine: Intel Core 7 240H (6P + 4E, 24 MB L3), Windows 11 Home, MSVC, VTune.

> **This repo has two parts.** This file covers `mshr_count/` — measuring the
> fill-buffer limit on real hardware. `camel_speedup/` builds on that result to
> recreate Fig. 3.10 of Kwon's *Software Prefetching for Memory-level
> Parallelism*: does software prefetch actually help once the fill buffer is the
> binding constraint? See [`camel_speedup/README.md`](camel_speedup/README.md)
> and its [lab notebook](camel_speedup/docs/LAB_NOTEBOOK.md). First result there
> reproduces the premise directly — in gem5, with a 10-MSHR L1, prefetch buys
> 0.1 % and MSHR-full stalls occupy 93 % of cycles; lifting the cap gives 2.1×.

---

## Method

`mlp_chase.c` does interleaved pointer chasing:

- **Pool:** 2^25 nodes × 16 B ≈ 512 MB (≫ 24 MB L3, so every miss goes to DRAM), linked into one random cycle so the prefetchers can't predict addresses.
- **N chains:** N cursors spaced evenly around the cycle. Each step does `cur[i] = cur[i]->next` for every i. Loads within a chain are dependent; chains are independent, so up to N misses can be in flight.
- **Constant work:** ~200M loads per run regardless of N (`steps = 200M / N`).
- **2 MB large pages** (`VirtualAlloc` + `MEM_LARGE_PAGES`). With 4 KB pages every load needs a page walk, and the few page walkers cap MLP before the LFB does. Needs "Lock pages in memory" granted to Administrators (set via `secedit` on Windows Home) and a program running **elevated**.
- **Pinned to logical CPU 2** (P-core) via `SetProcessAffinityMask` in the code.
- **ITT markers** (`__itt_resume` / `__itt_pause`) around the timed loop so VTune records only the loop.
- Output: `N=… large_pages=yes/no loads=… ns/load check=…` (`check` only prevents dead-code elimination).

## How it was run

All commands run from an **admin** *x64 Native Tools Command Prompt* in `C:\Users\akull\Downloads\l1_mshr_raptorlake`, after:

```
"C:\Program Files (x86)\Intel\oneAPI\vtune\latest\env\vars.bat"
```

**Build (ITT version):**
```
cl /O2 /Zi /DUSE_ITT /I "C:\Program Files (x86)\Intel\oneAPI\vtune\latest\sdk\include" mlp_chase.c advapi32.lib "C:\Program Files (x86)\Intel\oneAPI\vtune\latest\sdk\lib64\libittnotify.lib" /Fe:mlp_chase_itt.exe /link /DEBUG /INCREMENTAL:NO
```

**1. Timing sweep (no VTune).** AC power, Best performance mode, one warm-up run, 3 interleaved passes, 10 s rest between runs, median of 3:
```
for /L %r in (1,1,3) do for %n in (1 2 4 8 10 12 13 14 15 16 20 24 32 48 64 96 128) do (start /wait /b /affinity 4 mlp_chase.exe %n >> sweep_plugged.txt & timeout /t 10 /nobreak >nul)
```

**2. VTune counter sweep.** Six programmable events (no multiplexing), no PEBS events (unavailable with the Windows hypervisor on):
```
for %n in (1 8 12 14 16 20 32 128) do (vtune -collect-with runsa -start-paused -knob event-config=CPU_CLK_UNHALTED.THREAD,CPU_CLK_UNHALTED.REF_TSC,INST_RETIRED.ANY,L1D_PEND_MISS.PENDING,MEMORY_ACTIVITY.CYCLES_L1D_MISS,L1D_PEND_MISS.FB_FULL,MEM_LOAD_COMPLETED.L1_MISS_ANY,DTLB_LOAD_MISSES.WALK_COMPLETED_4K,DTLB_LOAD_MISSES.WALK_COMPLETED_2M_4M -r ev_n%n -- mlp_chase_itt.exe %n & timeout /t 10 /nobreak >nul)
```
View a result: `vtune -report summary -r ev_n16`, or GUI → File → Open → Result.

**Key metrics:**
- Misses in flight (Little's law *L*) = `PENDING ÷ CYCLES_L1D_MISS`
- Fill-buffer stall fraction = `FB_FULL ÷ CPU_CLK_UNHALTED.THREAD`
- Miss latency *W* = `PENDING ÷ L1_MISS_ANY` cycles

## Results

| N | Timing ns/load (median) | Misses in flight | Cycles blocked on full FB | Miss latency |
|---|---|---|---|---|
| 1 | 101.9 | 1.00 | ≈0% | 87 ns |
| 8 | 11.45 | 7.46 | ≈0% | 82 ns |
| 12 | 8.16 | 10.69 | 12% | 88 ns |
| 14 | 7.09 | 12.08 | 34% | 86 ns |
| 16 | 6.68 | 13.01 | 61% | 86 ns |
| 20 | 6.20 | 13.90 | 83% | 83 ns |
| 32 | 6.01 | 13.90 | 85% | 88 ns |
| 128 | 6.47 | 13.78 | 89% | 85 ns |

Full data, formulas and charts: `mlp_sweep.xlsx` (tabs: Findings, VTune counters, Plugged in, First pass, Method).

**Takeaways**
1. Misses in flight saturate at ~13–14 from N=20 to N=128.
2. `FB_FULL` rises from ≈0 to ~89% as the plateau forms: the hardware reports waiting on the fill buffer.
3. Little's law closes: 85 ns ÷ 13.9 ≈ 6.1 ns/load, matching the measured plateau.
4. Latency is flat and bandwidth is only ~10 GB/s, so DRAM isn't the limit. 0 page walks, so the TLB isn't either.
5. Speedup > N at small N is real: N=1 spends ~5% of cycles on serial on-core work that parallel chains hide, and each miss is slightly faster at N=8 (82 vs 88 ns).
6. Agrees with the earlier WSL harness (~12.8–13).

**Pitfalls hit:** battery power (~30% slower), long N=1 runs outlasting turbo, 4 KB fallback when VTune wasn't elevated, setup phase recorded without ITT, PEBS errors, and a non-default analysis type (Performance Snapshot) in the GUI.

## Open questions

- **Why ~14 and not the reported 16 LFBs?** At N=128 the buffer is full 93% of miss cycles, yet demand loads average 13.8, so demand never holds more than ~14–15 entries. Candidates: hardware prefetch requests, or entries held after data returns until the L1 fill completes.
- **Is the LFB the *only* binding limit?** ROB, load buffer and store buffer are ruled out by arithmetic. The L2 miss queue (~48) and stall counters haven't been confirmed directly.
- **Why is each miss slower at N=1 (88 ns) than at N=8 (82 ns)?** Possibly memory or uncore power states under light traffic. Unconfirmed.

## Next tests

1. **Working-set invariance (decisive LFB test).** Repeat the sweep at `lg=19` (8 MB, L3-resident) and `lg=15` (512 KB, L2-resident), e.g. `mlp_chase_itt.exe 32 19`. If the LFB is the limit, misses in flight plateau at ~14 at every size and only ns/load changes. For L2-resident, check IPC: a high IPC with low `FB_FULL` means issue rate is the limit (fix: unroll, keep cursors in registers).
2. **Who uses the other ~2 entries?** At N=32, add `L1D.REPLACEMENT` and L2 prefetch-request events. Lines filled > demand misses means non-demand requests occupy the fill buffer, which ties to Kwon's premise that prefetches consume L1 MSHRs.
3. **Confirm downstream queues are idle.** Add `OFFCORE_REQUESTS_OUTSTANDING` events and compare occupancy with ~14.
4. **Frequency and N=1 latency.** Compare average frequency and miss latency at N=1 vs N=8 across repeated runs.
5. **Repeat VTune runs 3× per N** to get medians like the timing sweep.
