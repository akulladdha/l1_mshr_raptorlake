"""gem5 SE-mode config for the Camel MLP study.

Three-level cache hierarchy with independently settable MSHR counts, an O3 core
whose sizing is chosen by --machine, and stride prefetchers at L1D/L2 (the
dissertation keeps these on: they cover the sequential idx[] walk while software
prefetch handles the indirect access).

Example:
  gem5.opt camel_se.py --machine skylake --l1d-mshrs 10 --early-retire-pf \
      --cmd camel_h0_t0 --outdir m5out/h0_t0_10
"""

import argparse
import math
import m5
from m5.objects import *

# ---------------------------------------------------------------- machines
# Provenance of every field is tracked in camel_speedup/gem5/CONFIG_SOURCES.md.
MACHINES = {
    # Kwon 2022, Table 4.2 (Skylake column) -- the dissertation's own setup.
    "skylake": dict(
        width=6, rob=224, lq=72, sq=48, iq=97, phys_int=180, phys_fp=168,
        clock="4GHz",
        l1d_size="32kB", l1d_assoc=8, l1d_lat=4, l1d_mshrs=10,
        l1i_size="32kB", l1i_assoc=8, l1i_lat=4, l1i_mshrs=10,
        l2_size="256kB", l2_assoc=4, l2_lat=12, l2_mshrs=48,
        l3_size="2MB", l3_assoc=16, l3_lat=40, l3_mshrs=64,
        mem_type="DDR4_2400_8x8", mem_channels=2,
    ),
    # Raptor Cove P-core, calibrated against this machine (Core 7 240H).
    # L3 is deliberately scaled down from the real 24MB; see CONFIG_SOURCES.md.
    "raptorlake": dict(
        width=6, rob=512, lq=192, sq=114, iq=97, phys_int=280, phys_fp=332,
        clock="4.9GHz",
        l1d_size="48kB", l1d_assoc=12, l1d_lat=5, l1d_mshrs=16,
        l1i_size="32kB", l1i_assoc=8, l1i_lat=5, l1i_mshrs=16,
        l2_size="1280kB", l2_assoc=10, l2_lat=16, l2_mshrs=48,
        l3_size="3MB", l3_assoc=12, l3_lat=50, l3_mshrs=64,
        mem_type="DDR5_6400_4x8", mem_channels=2,
    ),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cmd", required=True, help="binary to run")
    ap.add_argument("--options", default="", help="args for the binary")
    ap.add_argument("--machine", default="skylake", choices=sorted(MACHINES))
    ap.add_argument("--l1d-mshrs", type=int, default=None,
                    help="override L1D MSHRs (the knob for this study)")
    ap.add_argument("--l2-mshrs", type=int, default=None)
    ap.add_argument("--l3-mshrs", type=int, default=None)
    ap.add_argument("--early-retire-pf", action="store_true",
                    help="retire SWPF once the L1 accepts it (models real x86)")
    ap.add_argument("--no-hwp", action="store_true",
                    help="disable the stride prefetchers")
    ap.add_argument("--lq", type=int, default=None, help="override LQ entries")
    ap.add_argument("--rob", type=int, default=None,
                    help="override ROB entries")
    ap.add_argument("--mem-size", default="8GB")
    args = ap.parse_args()

    cfg = dict(MACHINES[args.machine])
    if args.l1d_mshrs is not None:
        cfg["l1d_mshrs"] = args.l1d_mshrs
    if args.l2_mshrs is not None:
        cfg["l2_mshrs"] = args.l2_mshrs
    if args.l3_mshrs is not None:
        cfg["l3_mshrs"] = args.l3_mshrs
    if args.lq is not None:
        cfg["lq"] = args.lq
    if args.rob is not None:
        cfg["rob"] = args.rob

    system = System()
    system.clk_domain = SrcClockDomain(
        clock=cfg["clock"], voltage_domain=VoltageDomain())
    system.mem_mode = "timing"
    system.mem_ranges = [AddrRange(args.mem_size)]

    cpu = X86O3CPU()
    cpu.fetchWidth = cpu.decodeWidth = cpu.renameWidth = cfg["width"]
    cpu.dispatchWidth = cpu.issueWidth = cpu.commitWidth = cfg["width"]
    cpu.wbWidth = cfg["width"]
    cpu.numROBEntries = cfg["rob"]
    cpu.LQEntries = cfg["lq"]
    cpu.SQEntries = cfg["sq"]
    # gem5 25.1 moved IQ sizing into a vector of IQUnit SimObjects.
    cpu.instQueues = [IQUnit(numEntries=cfg["iq"])]
    cpu.numPhysIntRegs = cfg["phys_int"]
    cpu.numPhysFloatRegs = cfg["phys_fp"]
    # gem5 25.1: BranchPredictor is a container; TAGE plugs in as the
    # conditional predictor component.
    cpu.branchPred = BranchPredictor(
        conditionalBranchPred=TAGE(numThreads=1))
    cpu.earlyRetirePrefetch = args.early_retire_pf
    system.cpu = cpu

    # ------------------------------------------------------------- caches
    cpu.icache = Cache(
        size=cfg["l1i_size"], assoc=cfg["l1i_assoc"],
        tag_latency=cfg["l1i_lat"], data_latency=cfg["l1i_lat"],
        response_latency=cfg["l1i_lat"],
        mshrs=cfg["l1i_mshrs"], tgts_per_mshr=20,
        writeback_clean=False)
    cpu.dcache = Cache(
        size=cfg["l1d_size"], assoc=cfg["l1d_assoc"],
        tag_latency=cfg["l1d_lat"], data_latency=cfg["l1d_lat"],
        response_latency=cfg["l1d_lat"],
        mshrs=cfg["l1d_mshrs"], tgts_per_mshr=20,
        write_buffers=max(16, cfg["l1d_mshrs"]),
        writeback_clean=False)
    if not args.no_hwp:
        cpu.dcache.prefetcher = StridePrefetcher(degree=4, queue_size=32)

    cpu.icache.cpu_side = cpu.icache_port
    cpu.dcache.cpu_side = cpu.dcache_port

    system.l2bus = L2XBar()
    cpu.icache.mem_side = system.l2bus.cpu_side_ports
    cpu.dcache.mem_side = system.l2bus.cpu_side_ports

    system.l2 = Cache(
        size=cfg["l2_size"], assoc=cfg["l2_assoc"],
        tag_latency=cfg["l2_lat"], data_latency=cfg["l2_lat"],
        response_latency=cfg["l2_lat"],
        mshrs=cfg["l2_mshrs"], tgts_per_mshr=20,
        write_buffers=32, writeback_clean=True)
    if not args.no_hwp:
        system.l2.prefetcher = StridePrefetcher(degree=4, queue_size=32)
    system.l2.cpu_side = system.l2bus.mem_side_ports

    system.l3bus = L2XBar()
    system.l2.mem_side = system.l3bus.cpu_side_ports

    system.l3 = Cache(
        size=cfg["l3_size"], assoc=cfg["l3_assoc"],
        tag_latency=cfg["l3_lat"], data_latency=cfg["l3_lat"],
        response_latency=cfg["l3_lat"],
        mshrs=cfg["l3_mshrs"], tgts_per_mshr=24,
        write_buffers=64, writeback_clean=True)
    system.l3.cpu_side = system.l3bus.mem_side_ports

    system.membus = SystemXBar()
    system.l3.mem_side = system.membus.cpu_side_ports

    # x86 needs the interrupt ports wired to the membus.
    cpu.createInterruptController()
    cpu.interrupts[0].pio = system.membus.mem_side_ports
    cpu.interrupts[0].int_requestor = system.membus.cpu_side_ports
    cpu.interrupts[0].int_responder = system.membus.mem_side_ports

    # -------------------------------------------------------------- memory
    # Channel interleaving follows configs/common/MemConfig.py: interleave on
    # cache-line (64B) granularity, so intlv_low_bit = log2(64) = 6.
    cls = globals()[cfg["mem_type"]]
    nch = cfg["mem_channels"]
    intlv_bits = int(math.log(nch, 2))
    assert 2 ** intlv_bits == nch, "mem_channels must be a power of two"
    intlv_low_bit = 6

    ctrls = []
    for i in range(nch):
        iface = cls()
        iface.range = AddrRange(
            start=0,
            size=args.mem_size,
            intlvHighBit=intlv_low_bit + intlv_bits - 1,
            xorHighBit=0,
            intlvBits=intlv_bits,
            intlvMatch=i,
        )
        ctrl = MemCtrl(dram=iface)
        ctrl.port = system.membus.mem_side_ports
        ctrls.append(ctrl)
    system.mem_ctrls = ctrls

    system.system_port = system.membus.cpu_side_ports

    # ------------------------------------------------------------ workload
    binary = args.cmd
    system.workload = SEWorkload.init_compatible(binary)
    proc = Process()
    proc.cmd = [binary] + (args.options.split() if args.options else [])
    cpu.workload = proc
    cpu.createThreads()

    root = Root(full_system=False, system=system)
    m5.instantiate()
    print(f"[camel_se] machine={args.machine} l1d_mshrs={cfg['l1d_mshrs']} "
          f"early_retire_pf={args.early_retire_pf}")
    exit_event = m5.simulate()
    print(f"[camel_se] exiting @ tick {m5.curTick()} because "
          f"{exit_event.getCause()}")


main()
