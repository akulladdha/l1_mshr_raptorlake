# Provenance of the gem5 machine configurations

Every field in `MACHINES` in `camel_se.py`, and where it came from. Tiers:

- **[D]** Documented by the vendor.
- **[P]** Published third-party measurement.
- **[M]** Measured on this machine — the only tier fully trusted here.
- **[E]** Estimated / chosen. Flagged because it is a judgement call.

---

## `skylake` — Kwon 2022, Table 4.2

This is a reproduction target, not a model of anything we own. Its purpose is to
check that our methodology lands near the published numbers. Every field is **[D]**
in the sense that it is copied from the dissertation's own table.

| Field | Value | Note |
|---|---|---|
| width / ROB / LQ / SQ | 6 / 224 / 72 / 48 | Table 4.2 |
| clock | 4 GHz | Table 4.2 |
| L1D | 32 kB, 8-way, 4 cyc, **10 MSHRs** | Table 4.2 ("10 or 16 MSHRs"); 10 is the Skylake column of Table 2.1 |
| L2 | 256 kB, 4-way, 12 cyc, 48 MSHRs | Table 4.2 |
| L3 | 2 MB, 16-way, 40 cyc, 64 MSHRs | Table 4.2 |
| memory | DDR4-2400 ×2 | Table 4.2 |
| prefetchers | stride at L1 and L2 | Table 4.2 + §4.3.4 |
| IQ entries | 97 | **[E]** not in Table 4.2; Skylake's scheduler is 97 entries |
| phys int / fp regs | 180 / 168 | **[P]** standard published Skylake figures |

**Known deviation:** Kwon's default evaluation machine is Zen2 with 16 L1 MSHRs
(§4.4); the Skylake column is used for his Fig 3.3/3.4/3.10 real-machine
measurements and for the `gem5-Skylake` runs in Ch. 5. We use the Skylake column
throughout, so our left panel corresponds to his Fig 5.13-style Skylake runs
rather than to the Zen2 runs behind Fig 3.8/3.9. Numbers should be close in shape
but are not expected to match his Zen2 figures digit for digit.

---

## `raptorlake` — Raptor Cove P-core, calibrated to this machine

**Status: not yet validated. No run has used this config.** It is an
approximation labelled "Raptor-Cove-like", not a validated model.

Target part detected on this system: **Intel Core 7 240H**, 6 P + 4 E, 24 MB L3,
32 GB DDR5-5600, 2.5 GHz base.

| Field | Value | Tier | Source / reasoning |
|---|---|---|---|
| ROB | 512 | [D] | Intel Optimization Reference Manual, Golden/Raptor Cove |
| LQ / SQ | 192 / 114 | [D] | same |
| width | 6 | [D] | 6-wide allocate (front-end); execution is wider |
| IQ entries | 97 | [E] | unified scheduler approximation |
| phys int / fp | 280 / 332 | [P] | uops.info / Chips-and-Cheese measurements |
| clock | 4.9 GHz | [D] | P-core max turbo for this SKU. **[E] risk:** sustained clock on a 2.5 GHz-base mobile part will be lower; see Open Issue below |
| L1D | 48 kB, 12-way | [D] | Raptor Cove L1D |
| L1D latency | 5 cyc | [P] | standard measured Golden/Raptor Cove load-to-use |
| **L1D MSHRs** | **16** | **[M]** | `mshr_count/sweep_plugged.txt` — knee at N ≈ 14–16 |
| L2 | 1280 kB, 10-way, 16 cyc | [D]/[P] | WMI reports 7680 kB total L2 over 6 P-cores = 1.25 MB each; latency [P] |
| L2 MSHRs | 48 | [E] | **not measured.** Carried over from the Skylake config |
| L3 | **3 MB**, 12-way, 50 cyc | [E]/[P] | **Deliberately scaled down from the real 24 MB** so the working set can exceed it in simulation. Latency [P] |
| L3 MSHRs | 64 | [E] | **not measured** |
| memory | DDR5-6400 ×2 | [E] | real part is DDR5-5600; gem5 ships `DDR5_6400_4x8`. Closest available model, ~14 % optimistic on bandwidth |

### Deliberate deviations, and why

1. **L3 shrunk 24 MB → 3 MB.** A working set that comfortably exceeds 24 MB
   cannot be simulated in reasonable time. The working set is scaled with it to
   keep the working-set : LLC ratio comparable. Consequence: the simulated and
   real panels are *not* iso-configuration. Kwon carries the same caveat (his
   Fig 3.10 left panel is gem5-Zen2 while the right panel is a real Skylake).
2. **DDR5-6400 instead of 5600.** Model availability. Inflates achievable MLP
   slightly; matters most for the idealized 512-MSHR runs, which are closest to
   the bandwidth limit.
3. **E-cores not modelled.** Single-core study; irrelevant.

### Fields that are guesses and should be measured before being trusted
- L2 and L3 MSHR counts (currently inherited from the Skylake config).
- Sustained P-core clock under load on this mobile part.
- Real idle DRAM latency and achievable random-access bandwidth.

### Planned validation
`mshr_count/mlp_chase.c` is ground truth we already hold for this machine: an
N = 1…128 sweep with real ns/load. The same sweep run under this gem5 config
should reproduce the knee at N ≈ 16 and roughly track 102 → 6 ns/load. Until that
validation table exists, treat every `raptorlake` number as indicative only.
