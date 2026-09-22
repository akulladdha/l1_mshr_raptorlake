// mlp_chase.c - interleaved pointer chasing to stress the L1D line fill buffers
//
// Usage:  mlp_chase_itt.exe <N chains> [log2 of node count, default 25 (~512 MB)]
//
// ITT build (records only the timed loop under VTune -start-paused):
//   cl /O2 /Zi /DUSE_ITT /I "C:\Program Files (x86)\Intel\oneAPI\vtune\latest\sdk\include" mlp_chase.c advapi32.lib
//      "C:\Program Files (x86)\Intel\oneAPI\vtune\latest\sdk\lib64\libittnotify.lib"
//      /Fe:mlp_chase_itt.exe /link /DEBUG /INCREMENTAL:NO

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <windows.h>
#ifdef USE_ITT
#include <ittnotify.h>
#endif

typedef struct Node { struct Node *next; uint64_t pad; } Node;  // 16 bytes

static uint64_t rng = 0x9E3779B97F4A7C15ull;
static uint64_t xorshift(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return rng; }

// Large pages need the "Lock pages in memory" right, and the process must switch it on.
static int enable_lock_privilege(void) {
    HANDLE tok;
    TOKEN_PRIVILEGES tp;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES, &tok)) return 0;
    tp.PrivilegeCount = 1;
    tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
    if (!LookupPrivilegeValueA(NULL, "SeLockMemoryPrivilege", &tp.Privileges[0].Luid)) {
        CloseHandle(tok);
        return 0;
    }
    AdjustTokenPrivileges(tok, FALSE, &tp, 0, NULL, NULL);
    int ok = (GetLastError() == ERROR_SUCCESS);
    CloseHandle(tok);
    return ok;
}

int main(int argc, char **argv) {
    int n  = argc > 1 ? atoi(argv[1]) : 1;
    int lg = argc > 2 ? atoi(argv[2]) : 25;
    if (n < 1) n = 1;

    // Pin this process to logical CPU 2 (a P-core), same as "start /affinity 4".
    SetProcessAffinityMask(GetCurrentProcess(), (DWORD_PTR)1 << 2);

    size_t m = (size_t)1 << lg;
    const uint64_t total_loads = 200000000ull;   // same total work for every N
    uint64_t steps = total_loads / (uint64_t)n;

    // Try 2 MB pages first so TLB misses / page walks don't become the bottleneck.
    size_t bytes = m * sizeof(Node);
    size_t lp = GetLargePageMinimum();
    Node *pool = NULL;
    if (lp && enable_lock_privilege()) {
        size_t lbytes = (bytes + lp - 1) / lp * lp;
        pool = VirtualAlloc(NULL, lbytes, MEM_COMMIT | MEM_RESERVE | MEM_LARGE_PAGES, PAGE_READWRITE);
        if (!pool) fprintf(stderr, "large-page VirtualAlloc failed, error %lu\n", GetLastError());
    } else {
        fprintf(stderr, "could not enable SeLockMemoryPrivilege\n");
    }
    int large = (pool != NULL);
    if (!pool) pool = VirtualAlloc(NULL, bytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);

    uint32_t *perm = malloc(m * sizeof(uint32_t));
    Node **cur = malloc((size_t)n * sizeof(Node *));
    if (!pool || !perm || !cur) { fprintf(stderr, "allocation failed\n"); return 1; }

    // Random permutation -> one big cycle that visits every node in random memory order,
    // so the hardware prefetchers can't predict the next address.
    for (size_t i = 0; i < m; i++) perm[i] = (uint32_t)i;
    for (size_t i = m - 1; i > 0; i--) {
        size_t j = (size_t)(xorshift() % (i + 1));
        uint32_t t = perm[i]; perm[i] = perm[j]; perm[j] = t;
    }
    for (size_t k = 0; k < m; k++) pool[perm[k]].next = &pool[perm[(k + 1) % m]];

    // N cursors spread evenly around the cycle: each chain's loads depend only on its own
    // previous load, so the out-of-order core can have up to N cache misses in flight at once.
    for (int i = 0; i < n; i++) cur[i] = &pool[perm[(size_t)i * (m / (size_t)n)]];
    free(perm);

    LARGE_INTEGER f, t0, t1;
    QueryPerformanceFrequency(&f);
#ifdef USE_ITT
    __itt_resume();
#endif
    QueryPerformanceCounter(&t0);
    for (uint64_t s = 0; s < steps; s++)
        for (int i = 0; i < n; i++)
            cur[i] = cur[i]->next;
    QueryPerformanceCounter(&t1);
#ifdef USE_ITT
    __itt_pause();
#endif

    // Use the results so the compiler can't delete the loop as dead code.
    uintptr_t check = 0;
    for (int i = 0; i < n; i++) check ^= (uintptr_t)cur[i];

    double sec = (double)(t1.QuadPart - t0.QuadPart) / (double)f.QuadPart;
    uint64_t loads = steps * (uint64_t)n;
    printf("N=%d  large_pages=%s  loads=%llu  %.2f ns/load  check=%llx\n",
           n, large ? "yes" : "no", (unsigned long long)loads,
           sec * 1e9 / (double)loads, (unsigned long long)check);
    return 0;
}