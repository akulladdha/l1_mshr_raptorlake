// camel.c - Camel micro-benchmark (Kwon 2022, Listing 3.1) for MLP / software-prefetch study.
//
//   for (i = 0; i < n; i++)
//       sum += hash^N( data[ idx[i] ] );
//
// idx[] is walked sequentially (left to the hardware stride prefetcher, per the
// dissertation's footnote 1); data[] is indexed by a random permutation so the
// indirect access is unpredictable and misses to DRAM. N chained hashes set the
// memory intensity: h0 is the most memory-intensive, h5 the least.
//
// Build-time knobs:
//   HASH_N   number of chained hashes after the indirect load (0..9)   [default 0]
//   SWPF     if defined, issue a prefetcht0 PFDIST iterations ahead
//   PFDIST   prefetch distance in iterations                           [default 32]
//   LOG2_M   log2 of the number of elements in data[]                  [default 22]
//   NACCESS  indirect accesses performed in the timed region
//   GEM5     use m5 ops to bound the region of interest
//
// Linux / gem5:
//   gcc -O2 -fno-tree-vectorize -static -DGEM5 -DHASH_N=0 -DSWPF \
//       -I<gem5>/include camel.c <gem5>/util/m5/build/x86/out/libm5.a -o camel_h0_t0
//
// Windows (MSVC, for the real-machine panel):
//   cl /O2 /DHASH_N=0 /DSWPF camel.c /Fe:camel_h0_t0.exe

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>

#ifdef _WIN32
#include <windows.h>
#include <intrin.h>
#define PREFETCH_T0(p) _mm_prefetch((const char *)(p), _MM_HINT_T0)
#else
#include <time.h>
#include <xmmintrin.h>
#define PREFETCH_T0(p) _mm_prefetch((const char *)(p), _MM_HINT_T0)
#endif

#ifdef GEM5
#include <gem5/m5ops.h>
#endif

#ifndef HASH_N
#define HASH_N 0
#endif
#ifndef PFDIST
#define PFDIST 32
#endif
#ifndef LOG2_M
#define LOG2_M 22
#endif
#ifndef NACCESS
#define NACCESS 2000000ull
#endif

// Murmur3 finalizer: a short dependent chain of ~10 uops, used to dial memory
// intensity down without touching memory.
static inline uint64_t hashf(uint64_t x)
{
    x ^= x >> 33;
    x *= 0xff51afd7ed558ccdULL;
    x ^= x >> 33;
    x *= 0xc4ceb9fe1a85ec53ULL;
    x ^= x >> 33;
    return x;
}

// A bijection on [0, 2^lg). Both primitives are invertible modulo 2^lg --
// multiplying by an odd constant, and x ^= x >> s -- so the composition is a
// permutation, which means idx[] touches every element of data[] exactly once
// with no reuse. Computing it in closed form keeps initialization to a single
// division-free pass; a Fisher-Yates shuffle with a 64-bit modulo per element
// costs more simulated time than the measured region itself.
static inline uint64_t permute_idx(uint64_t i, unsigned lg)
{
    const uint64_t mask = (lg >= 64) ? ~0ull : (((uint64_t)1 << lg) - 1);
    uint64_t x = i & mask;
    x = (x ^ (x >> 7)) & mask;
    x = (x * 0x9E3779B97F4A7C15ull) & mask;
    x = (x ^ (x >> 11)) & mask;
    x = (x * 0xC2B2AE3D27D4EB4Full) & mask;
    x = (x ^ (x >> 13)) & mask;
    return x;
}

int main(int argc, char **argv)
{
    const size_t m = (size_t)1 << LOG2_M;
    const uint64_t naccess = NACCESS;

    // idx[] is over-allocated by PFDIST so the look-ahead prefetch never runs
    // off the end and we do not need a bounds check in the hot loop.
    uint32_t *idx = (uint32_t *)malloc((m + PFDIST) * sizeof(uint32_t));
    uint64_t *data = (uint64_t *)malloc(m * sizeof(uint64_t));
    if (!idx || !data) {
        fprintf(stderr, "allocation failed\n");
        return 1;
    }

    fprintf(stderr, "[camel] init data[] (%zu MiB)\n",
            (m * sizeof(uint64_t)) >> 20);
    for (size_t i = 0; i < m; i++)
        data[i] = (uint64_t)i * 2654435761u + 1;

    // Scrambled permutation -> the indirect access hits a random line every
    // time, so the hardware prefetchers cannot predict it.
    fprintf(stderr, "[camel] init idx[] (%zu MiB)\n",
            (m * sizeof(uint32_t)) >> 20);
    for (size_t i = 0; i < m; i++)
        idx[i] = (uint32_t)permute_idx(i, LOG2_M);
    for (size_t i = 0; i < PFDIST; i++)
        idx[m + i] = idx[i];

    fprintf(stderr, "[camel] entering ROI\n");

    uint64_t sum = 0;

#ifdef _WIN32
    LARGE_INTEGER f, t0, t1;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&t0);
#else
    struct timespec ts0, ts1;
    clock_gettime(CLOCK_MONOTONIC, &ts0);
#endif

#ifdef GEM5
    m5_reset_stats(0, 0);
#endif

    // ---- region of interest -------------------------------------------------
    for (uint64_t s = 0; s < naccess; s++) {
        size_t i = (size_t)(s & (m - 1));
#ifdef SWPF
        PREFETCH_T0(&data[idx[i + PFDIST]]);
#endif
        uint64_t v = data[idx[i]];
#if HASH_N > 0
        for (int k = 0; k < HASH_N; k++)
            v = hashf(v);
#endif
        sum += v;
    }
    // -------------------------------------------------------------------------

#ifdef GEM5
    m5_dump_stats(0, 0);
#endif

#ifdef _WIN32
    QueryPerformanceCounter(&t1);
    double sec = (double)(t1.QuadPart - t0.QuadPart) / (double)f.QuadPart;
#else
    clock_gettime(CLOCK_MONOTONIC, &ts1);
    double sec = (ts1.tv_sec - ts0.tv_sec) + 1e-9 * (ts1.tv_nsec - ts0.tv_nsec);
#endif

    printf("hash_n=%d swpf=%d pfdist=%d log2_m=%d accesses=%llu "
           "%.2f ns/access sum=%llx\n",
           HASH_N,
#ifdef SWPF
           1,
#else
           0,
#endif
           PFDIST, LOG2_M, (unsigned long long)naccess,
           sec * 1e9 / (double)naccess, (unsigned long long)sum);

    free(idx);
    free(data);
    return 0;
}
