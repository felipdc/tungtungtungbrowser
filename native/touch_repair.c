/* SPDX-License-Identifier: MIT
 * Build-pinned native cache installation. No browser hooks or page code.
 * The recipe is generated from scripts/prepare_touch_repair.py at build time.
 * Installation writes a separate cache, keeps a complete backup and renames
 * only after verification. Reboot and native browser validation are separate.
 */
#include "touch_repair_recipe.h"

typedef unsigned long Size;
typedef long SSize;
typedef long long Off;
typedef unsigned long long U64;
extern int printf(const char *, ...);
extern int snprintf(char *, Size, const char *, ...);

struct Identity {
    U64 device, inode, size;
    unsigned int mode, uid, gid, flags, links;
};
struct Operations {
    int (*open)(const char *, int); /* 0 read, 1 exclusive create, 2 directory */
    int (*close)(int);
    int (*sync)(int);
    int (*lock)(int, int); /* 0 shared, 1 exclusive; always nonblocking */
    int (*mkdir)(const char *);
    int (*identity)(int, struct Identity *);
    int (*metadata)(int, int);
    int (*rename)(const char *, const char *);
    int (*error)(void);
    int (*boot_uuid)(char *, Size);
    SSize (*read_at)(int, void *, Size, Off);
    SSize (*write_at)(int, const void *, Size, Off);
    int (*sha_init)(void *);
    int (*sha_update)(void *, const void *, unsigned int);
    int (*sha_final)(unsigned char *, void *);
};
static struct Operations api;
static int journal = -1, replaced;
static Off journal_offset;
static const char *operation = "inspect", *attempt = "";
static char boot_uuid[64];

static int equal(const void *a, const void *b, Size n)
{
    const unsigned char *x = a, *y = b;
    for (Size i = 0; i < n; i++) if (x[i] != y[i]) return 0;
    return 1;
}

static int word(const char *a, const char *b)
{
    Size i = 0;
    while (a[i] && b[i] && a[i] == b[i]) i++;
    return a[i] == b[i];
}

static int valid_attempt(const char *s)
{
    for (int i = 0; i < 32; i++) {
        if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'a' && s[i] <= 'f'))) return 0;
    }
    return s[32] == 0;
}

static int write_all(int fd, const void *data, Size size, Off offset)
{
    const unsigned char *bytes = data;
    while (size) {
        SSize n = api.write_at(fd, bytes, size, offset);
        if (n <= 0 || (Size)n > size) return 0;
        bytes += n; offset += n; size -= (Size)n;
    }
    return 1;
}

static int event(const char *stage, const char *state, const char *digest)
{
    char line[1024];
    int error = word(state, "failed") ? api.error() : 0;
    int n = snprintf(line, sizeof(line),
        "{\"protocol\":1,\"operation\":\"%s\",\"attempt\":\"%s\","
        "\"stage\":\"%s\",\"status\":\"%s\",\"sha256\":\"%s\","
        "\"boot_uuid\":\"%s\",\"replacement_performed\":%s,\"errno\":%d}\n",
        operation, attempt, stage, state, digest, boot_uuid,
        replaced ? "true" : "false", error);
    if (n < 0 || (Size)n >= sizeof(line)) return 0;
    if (journal >= 0) {
        if (!write_all(journal, line, (Size)n, journal_offset) || api.sync(journal)) return 0;
        journal_offset += n;
    }
    printf("%s", line);
    return 1;
}

static int fail(const char *stage)
{
    /* A failed journal is still reported to stdout. No mutation is retried. */
    if (!event(stage, "failed", "")) {
        printf("{\"protocol\":1,\"status\":\"failed\",\"stage\":\"journal\","
               "\"replacement_performed\":%s}\n", replaced ? "true" : "false");
    }
    return 1;
}

static int identity(int fd, struct Identity *value)
{
    return api.identity(fd, value) == 0 && (value->mode & 0170000) == 0100000
        && value->size == REPAIR_SIZE && value->links == 1;
}

static int same_file(const struct Identity *a, const struct Identity *b)
{
    return a->device == b->device && a->inode == b->inode && a->size == b->size;
}

static int same_metadata(const struct Identity *a, const struct Identity *b)
{
    return a->mode == b->mode && a->uid == b->uid && a->gid == b->gid && a->flags == b->flags;
}

static int hash_file(int fd, char digest[65])
{
    unsigned char block[65536], hash[32];
    U64 context[32] = {0};
    struct Identity info;
    if (!identity(fd, &info) || api.sha_init(context) != 1) return 0;
    for (Off offset = 0; offset < REPAIR_SIZE;) {
        Size size = REPAIR_SIZE - offset < (Off)sizeof(block) ? (Size)(REPAIR_SIZE - offset) : sizeof(block);
        SSize n = api.read_at(fd, block, size, offset);
        if (n <= 0 || (Size)n > size || api.sha_update(context, block, (unsigned int)n) != 1) return 0;
        offset += n;
    }
    if (!identity(fd, &info) || api.sha_final(hash, context) != 1) return 0;
    const char *hex = "0123456789abcdef";
    for (int i = 0; i < 32; i++) { digest[i * 2] = hex[hash[i] >> 4]; digest[i * 2 + 1] = hex[hash[i] & 15]; }
    digest[64] = 0;
    return 1;
}

static int copy_data(int from, int to)
{
    unsigned char block[65536];
    for (Off offset = 0; offset < REPAIR_SIZE;) {
        Size size = REPAIR_SIZE - offset < (Off)sizeof(block) ? (Size)(REPAIR_SIZE - offset) : sizeof(block);
        SSize n = api.read_at(from, block, size, offset);
        if (n <= 0 || (Size)n > size || !write_all(to, block, (Size)n, offset)) return 0;
        offset += n;
    }
    return api.sync(to) == 0;
}

static int inspect_cache(int source, char digest[65])
{
    if (!hash_file(source, digest)) return 0;
    return equal(digest, original_hash, 64) || equal(digest, repaired_hash, 64);
}

static int run(int argc, char **argv)
{
    if (argc < 2) return fail("arguments");
    int inspect = word(argv[1], "inspect"), apply = word(argv[1], "apply"), revert = word(argv[1], "revert");
    if ((!inspect && !apply && !revert) || (inspect ? argc != 2 : argc != 3)) return fail("arguments");
    operation = apply ? "apply" : revert ? "revert" : "inspect";
    if (!inspect && !valid_attempt(argv[2])) return fail("attempt_id");
    if (!inspect) attempt = argv[2];
    if (!api.boot_uuid(boot_uuid, sizeof(boot_uuid))) return fail("boot_identity");

    /* The lock is a separate inode: it continues to serialize helpers across
     * replacement of the cache inode. Inspect never creates it. */
    int lock = api.open(lock_path, 0);
    if (lock < 0 && !inspect) lock = api.open(lock_path, 1);
    if (lock < 0 && (!inspect || api.error() != 2)) return fail("open_lock");
    if (lock >= 0 && api.lock(lock, !inspect)) return fail("busy");
    int source = api.open(cache_path, 0);
    if (source < 0) return fail("open_cache");
    char before[65], after[65];
    struct Identity original, current, candidate;
    if (!identity(source, &original) || !inspect_cache(source, before)) return fail("unsupported_cache");
    if (inspect) return event("inspection", "verified", before) ? 0 : 1;
    const char *expected_before = apply ? original_hash : repaired_hash;
    const char *expected_after = apply ? repaired_hash : original_hash;
    if (!equal(before, expected_before, 64)) return fail("expected_preimage");

    char directory[512], backup_path[576], staging[576], journal_path[576];
    if (snprintf(directory, sizeof(directory), "%s/%s", backup_root, attempt) >= (int)sizeof(directory)
        || snprintf(backup_path, sizeof(backup_path), "%s/backup.cache", directory) >= (int)sizeof(backup_path)
        || snprintf(journal_path, sizeof(journal_path), "%s/events.jsonl", directory) >= (int)sizeof(journal_path)
        || snprintf(staging, sizeof(staging), "%s/.ttt-touch-%s.staging", cache_directory, attempt) >= (int)sizeof(staging)) return fail("paths");
    if (api.mkdir(backup_root) && api.error() != 17) return fail("backup_root");
    int root_fd = api.open(backup_root, 2);
    if (root_fd < 0) return fail("backup_directory");
    int cache_fd = api.open(cache_directory, 2);
    if (cache_fd < 0) return fail("cache_directory");
    if (api.mkdir(directory) || api.sync(root_fd)) return fail("exclusive_attempt");
    int directory_fd = api.open(directory, 2);
    journal = api.open(journal_path, 1);
    if (directory_fd < 0 || journal < 0 || api.sync(directory_fd)
        || !event("preparing", "running", before)) return fail("journal_creation");
    int backup = api.open(backup_path, 1);
    if (backup < 0 || !copy_data(source, backup) || !hash_file(backup, after)
        || !equal(after, before, 64) || api.sync(directory_fd)
        || !event("backup_verified", "running", before)) return fail("backup");

    int stage = api.open(staging, 1);
    if (stage < 0 || !copy_data(source, stage)) return fail("staging_copy");
    for (Size i = 0; i < sizeof(repair_spans) / sizeof(repair_spans[0]); i++) {
        const struct RepairSpan *span = &repair_spans[i];
        if (!write_all(stage, apply ? span->repaired : span->original, span->size, span->offset)) return fail("staging_patch");
    }
    if (!hash_file(stage, after) || !equal(after, expected_after, 64)) return fail("staging_hash");
    if (api.metadata(source, stage) || !identity(stage, &candidate)
        || !same_metadata(&original, &candidate) || api.sync(stage) || api.sync(cache_fd)) return fail("staging_metadata");
    if (!hash_file(stage, after) || !equal(after, expected_after, 64)) return fail("staging_final_hash");
    int live = api.open(cache_path, 0);
    if (live < 0 || !identity(live, &current) || !same_file(&original, &current)
        || !same_metadata(&original, &current)
        || !hash_file(live, before) || !equal(before, expected_before, 64)) return fail("source_changed");
    if (!event("ready_to_replace", "running", after)) return fail("journal_before_replace");
    if (api.rename(staging, cache_path)) return fail("replace");
    replaced = 1;
    if (api.sync(cache_fd)) return fail("sync_replacement_directory");
    int installed = api.open(cache_path, 0);
    if (installed < 0 || !identity(installed, &current) || !same_file(&candidate, &current)
        || !same_metadata(&original, &current) || !hash_file(installed, after)
        || !equal(after, expected_after, 64)) return fail("installed_verification");
    return event("reboot_required", "complete", after) ? 0 : fail("journal_complete");
}

#ifndef TTT_REPAIR_TEST
#include "touch_repair_darwin.h"
int main(int argc, char **argv)
{
    if (!initialize_native()) { printf("{\"protocol\":1,\"status\":\"failed\",\"stage\":\"native_symbols\"}\n"); return 1; }
    return run(argc, argv);
}
#endif
