// Standalone check that permute_idx() in camel.c really is a bijection on
// [0, 2^lg) for every size we simulate. Run natively, not under gem5.
//   gcc -O2 check_perm.c -o check_perm && ./check_perm
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>

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

int main(void)
{
    int bad = 0;
    for (unsigned lg = 8; lg <= 24; lg++) {
        size_t m = (size_t)1 << lg;
        unsigned char *seen = calloc(m, 1);
        if (!seen) { perror("calloc"); return 1; }
        size_t dup = 0, oob = 0;
        for (size_t i = 0; i < m; i++) {
            uint64_t v = permute_idx(i, lg);
            if (v >= m) { oob++; continue; }
            if (seen[v]) dup++;
            seen[v] = 1;
        }
        size_t missing = 0;
        for (size_t i = 0; i < m; i++) if (!seen[i]) missing++;

        // Sanity on scrambling: a permutation that is close to the identity
        // would still pass the bijection test but would be prefetchable.
        size_t fixed = 0;
        for (size_t i = 0; i < m; i++) if (permute_idx(i, lg) == i) fixed++;

        const char *verdict = (dup || oob || missing) ? "FAIL" : "ok";
        if (dup || oob || missing) bad = 1;
        printf("lg=%2u m=%8zu dup=%zu oob=%zu missing=%zu fixed_points=%zu  %s\n",
               lg, m, dup, oob, missing, fixed, verdict);
        free(seen);
    }
    printf("\n%s\n", bad ? "NOT a permutation -- do not use" : "bijection verified for all sizes");
    return bad;
}
