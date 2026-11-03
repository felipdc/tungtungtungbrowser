/* Probe the ARM64 memory instructions used by the native graphics runtime.
 * Each child checks one operation on its own anonymous memory. This verifies
 * returned/stored values and faults, not concurrent atomicity or ordering.
 */
#define _GNU_SOURCE
#if !defined(__linux__) || !defined(__aarch64__)
#error "This check requires an ARM64 Linux host"
#endif
#include <asm/hwcap.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/auxv.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>

static int check_operation(unsigned char *address, unsigned operation) {
    uint32_t value = 0, expected = UINT32_C(0x12345678);
    uint32_t replacement = UINT32_C(0x76543210);
    memcpy(address, &expected, sizeof(expected));
    switch (operation) {
    case 0:
        __asm__ volatile("ldar %w0, [%1]"
                         : "=&r"(value) : "r"(address) : "memory");
        return value == expected;
    case 1:
        __asm__ volatile("ldapr %w0, [%1]"
                         : "=&r"(value) : "r"(address) : "memory");
        return value == expected;
    case 2:
        __asm__ volatile("stlr %w0, [%1]"
                         : : "r"(replacement), "r"(address) : "memory");
        memcpy(&value, address, sizeof(value));
        return value == replacement;
    case 3:
        __asm__ volatile("ldsetal %w2, %w0, [%1]"
                         : "=&r"(value) : "r"(address), "r"(1u) : "memory");
        memcpy(&replacement, address, sizeof(replacement));
        return value == expected && replacement == (expected | 1u);
    default:
        return 0;
    }
}

int main(void) {
    const struct rlimit no_core = {0, 0};
    if (setrlimit(RLIMIT_CORE, &no_core) != 0) {
        perror("setrlimit");
        return 2;
    }
    long page_size = sysconf(_SC_PAGESIZE);
    if (page_size < 16) {
        fputs("Invalid host page size\n", stderr);
        return 2;
    }
    unsigned long caps = getauxval(AT_HWCAP);
    unsigned long required = HWCAP_ATOMICS | HWCAP_LRCPC | HWCAP_USCAT;
    int features = (caps & required) == required;
    unsigned char *memory = mmap(NULL, (size_t)page_size, PROT_READ | PROT_WRITE,
                                 MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (memory == MAP_FAILED) {
        perror("mmap");
        return 2;
    }
    printf("{\"hwcap\":%lu,\"atomics\":%s,\"lrcpc\":%s,\"uscat\":%s,"
           "\"required_features_present\":%s,\"page_size\":%ld,\"cases\":[",
           caps, caps & HWCAP_ATOMICS ? "true" : "false",
           caps & HWCAP_LRCPC ? "true" : "false",
           caps & HWCAP_USCAT ? "true" : "false",
           features ? "true" : "false", page_size);
    fflush(stdout);
    const char *names[] = {"ldar", "ldapr", "stlr", "ldsetal"};
    int first = 1, passed = features;
    unsigned count = 0;
    for (unsigned operation = 0; features && operation < 4; ++operation) {
        /* Every four-byte access stays inside the same 16-byte granule. */
        for (unsigned offset = 0; offset <= 12; ++offset) {
            pid_t child = fork();
            if (child < 0) {
                perror("fork");
                return 2; /* Partial JSON must not be accepted as a result. */
            }
            if (child == 0) {
                alarm(2);
                _exit(check_operation(memory + offset, operation) ? 0 : 1);
            }
            int status = 0;
            pid_t waited;
            do {
                waited = waitpid(child, &status, 0);
            } while (waited < 0 && errno == EINTR);
            if (waited != child) {
                perror("waitpid");
                return 2;
            }
            int correct = WIFEXITED(status) && WEXITSTATUS(status) == 0;
            passed &= correct;
            ++count;
            printf("%s{\"operation\":\"%s\",\"offset\":%u,\"passed\":%s,"
                   "\"exit_code\":%d,\"signal\":%d}",
                   first ? "" : ",", names[operation], offset,
                   correct ? "true" : "false",
                   WIFEXITED(status) ? WEXITSTATUS(status) : -1,
                   WIFSIGNALED(status) ? WTERMSIG(status) : 0);
            first = 0;
            fflush(stdout);
        }
    }
    printf("],\"case_count\":%u,\"all_values_correct_without_fault\":%s,"
           "\"concurrent_atomicity_tested\":false}\n", count,
           passed && count == 52 ? "true" : "false");
    if (munmap(memory, (size_t)page_size) != 0) {
        perror("munmap");
        return 2;
    }
    return passed && count == 52 ? 0 : 1;
}
