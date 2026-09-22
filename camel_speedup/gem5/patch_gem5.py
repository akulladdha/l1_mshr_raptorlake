#!/usr/bin/env python3
"""Apply the software-prefetch early-retire patch to gem5.

Adds an O3CPU param `earlyRetirePrefetch`. When enabled, a data-prefetch
instruction is marked executed as soon as the L1 accepts its request, rather
than when the data returns. This models real x86 behaviour (a SWPF retires
once issued, so MLP is not bounded by the ROB) while preserving the Intel
behaviour that a SWPF *does* stall when the L1 refuses the request because
its MSHRs are full.

Idempotent: re-running is a no-op.
"""
import sys, os

ROOT = os.path.expanduser("~/gem5")


def patch(path, old, new, tag):
    p = os.path.join(ROOT, path)
    s = open(p).read()
    if new.strip() in s:
        print(f"  [skip] {tag}: already applied")
        return
    n = s.count(old)
    if n != 1:
        sys.exit(f"  [FAIL] {tag}: anchor found {n} times in {path}, expected 1")
    open(p, "w").write(s.replace(old, new))
    print(f"  [ok]   {tag}")


# ---------------------------------------------------------------- 1. param
patch(
    "src/cpu/o3/BaseO3CPU.py",
    '    needsTSO = Param.Bool(False, "Enable TSO Memory model")\n',
    '    needsTSO = Param.Bool(False, "Enable TSO Memory model")\n'
    '\n'
    '    earlyRetirePrefetch = Param.Bool(\n'
    '        False,\n'
    '        "Allow software prefetch instructions to complete (and therefore "\n'
    '        "retire) as soon as their request is accepted by the L1 cache, "\n'
    '        "instead of waiting for the data to return. Models real x86 "\n'
    '        "behaviour where a SWPF retires once issued and so is not bounded "\n'
    '        "by the ROB. A prefetch still stalls when the L1 refuses the "\n'
    '        "request (e.g. MSHRs full), which is the Intel behaviour.",\n'
    '    )\n',
    "BaseO3CPU.py: earlyRetirePrefetch param",
)

# ------------------------------------------------------- 2. member + stat
patch(
    "src/cpu/o3/lsq_unit.hh",
    "    /** Should loads be checked for dependency issues */\n"
    "    bool checkLoads;\n",
    "    /** Should loads be checked for dependency issues */\n"
    "    bool checkLoads;\n"
    "\n"
    "    /** Retire software prefetches as soon as the L1 accepts them. */\n"
    "    bool earlyRetirePrefetch;\n",
    "lsq_unit.hh: earlyRetirePrefetch member",
)

patch(
    "src/cpu/o3/lsq_unit.hh",
    "        statistics::Scalar blockedByCache;\n",
    "        statistics::Scalar blockedByCache;\n"
    "\n"
    "        /** Number of software prefetches retired early, i.e. as soon as\n"
    "         *  the L1 accepted the request rather than on data return. */\n"
    "        statistics::Scalar earlyRetiredPrefetches;\n",
    "lsq_unit.hh: earlyRetiredPrefetches stat decl",
)

# ---------------------------------------------------------------- 3. init
patch(
    "src/cpu/o3/lsq_unit.cc",
    "    checkLoads = params.LSQCheckLoads;\n",
    "    checkLoads = params.LSQCheckLoads;\n"
    "    earlyRetirePrefetch = params.earlyRetirePrefetch;\n",
    "lsq_unit.cc: init member",
)

patch(
    "src/cpu/o3/lsq_unit.cc",
    '      ADD_STAT(loadToUse, statistics::units::Cycle::get(),\n',
    '      ADD_STAT(earlyRetiredPrefetches, statistics::units::Count::get(),\n'
    '               "Number of software prefetches completed as soon as the "\n'
    '               "L1 accepted the request, rather than on data return"),\n'
    '      ADD_STAT(loadToUse, statistics::units::Cycle::get(),\n',
    "lsq_unit.cc: stat registration",
)

# ------------------------------------------------- 4. the actual behaviour
patch(
    "src/cpu/o3/lsq_unit.cc",
    "    if (ret) {\n"
    "        if (!isLoad) {\n"
    "            isStoreBlocked = false;\n"
    "        }\n"
    "        lsq->cachePortBusy(isLoad);\n"
    "        request->packetSent();\n"
    "    } else {\n",
    "    if (ret) {\n"
    "        if (!isLoad) {\n"
    "            isStoreBlocked = false;\n"
    "        }\n"
    "        lsq->cachePortBusy(isLoad);\n"
    "        request->packetSent();\n"
    "\n"
    "        // A software prefetch is architecturally complete once the L1\n"
    "        // has accepted its request: it binds to no register, so there is\n"
    "        // nothing to wait for. Completing it here lets it retire out of\n"
    "        // the ROB while the fill is still in flight, which is what allows\n"
    "        // SWPF to expose more MLP than the ROB alone can hold. Note this\n"
    "        // is deliberately on the *success* path only -- if the L1 refuses\n"
    "        // the request because its MSHRs are full, the prefetch stalls,\n"
    "        // matching Intel's behaviour of waiting rather than dropping.\n"
    "        if (earlyRetirePrefetch) {\n"
    "            const DynInstPtr &inst = request->instruction();\n"
    "            if (isLoad && inst->isDataPrefetch() &&\n"
    "                !inst->isExecuted() && !inst->isSquashed()) {\n"
    "                inst->setExecuted();\n"
    "                iewStage->instToCommit(inst);\n"
    "                iewStage->activityThisCycle();\n"
    "                ++stats.earlyRetiredPrefetches;\n"
    "                DPRINTF(LSQUnit, \"Early-retiring prefetch [sn:%llu]\\n\",\n"
    "                        inst->seqNum);\n"
    "            }\n"
    "        }\n"
    "    } else {\n",
    "lsq_unit.cc: early-retire on L1 accept",
)

# ------------------------------- 5. don't send an early-retired PF twice
# writeback() guards setExecuted()/completeAcc() behind !isExecuted(), but it
# calls instToCommit() unconditionally. If the fill returns before commit has
# retired the prefetch, the instruction is sent to commit a second time and
# MemDepUnit::completed() trips its "already erased" assertion.
patch(
    "src/cpu/o3/lsq_unit.cc",
    "    // Squashed instructions do not need to complete their access.\n"
    "    if (inst->isSquashed()) {\n"
    "        assert (!inst->isStore() || inst->isStoreConditional());\n"
    "        ++stats.ignoredResponses;\n"
    "        return;\n"
    "    }\n",
    "    // Squashed instructions do not need to complete their access.\n"
    "    if (inst->isSquashed()) {\n"
    "        assert (!inst->isStore() || inst->isStoreConditional());\n"
    "        ++stats.ignoredResponses;\n"
    "        return;\n"
    "    }\n"
    "\n"
    "    // An early-retired software prefetch was already handed to commit\n"
    "    // when the L1 accepted its request, and it binds to no architectural\n"
    "    // register, so the data response carries nothing it needs. Drop it\n"
    "    // here: falling through would call instToCommit() a second time and\n"
    "    // trip the MemDepUnit::completed() assertion.\n"
    "    if (earlyRetirePrefetch && inst->isDataPrefetch() &&\n"
    "        inst->isExecuted()) {\n"
    "        ++stats.ignoredResponses;\n"
    "        return;\n"
    "    }\n",
    "lsq_unit.cc: suppress duplicate writeback of early-retired PF",
)

print("\nAll hunks applied.")
