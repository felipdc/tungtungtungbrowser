/* SPDX-License-Identifier: MIT
 * Linux filesystem adapter for the installer core's failure tests. This is
 * never linked into the iOS helper and does not validate Darwin's ABI/APFS.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#define TTT_REPAIR_TEST
#include "touch_repair.c"

static const char *test_root, *fault;
static int injected;

static void host_path(char out[1024], const char *path)
{
    if (snprintf(out, 1024, "%s%s", test_root, path) >= 1024) abort();
}

static void fd_path(int fd, char out[1024])
{
    char link[64];
    snprintf(link, sizeof(link), "/proc/self/fd/%d", fd);
    ssize_t count = readlink(link, out, 1023);
    if (count < 0) abort();
    out[count] = 0;
}

static int host_open(const char *path, int kind)
{
    char full[1024]; host_path(full, path);
    return open(full, O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC
                | (kind == 1 ? O_RDWR | O_CREAT | O_EXCL : O_RDONLY)
                | (kind == 2 ? O_DIRECTORY : 0), 0600);
}

static int host_mkdir(const char *path)
{
    char full[1024]; host_path(full, path); return mkdir(full, 0700);
}

static int host_identity(int fd, struct Identity *out)
{
    struct stat s;
    if (fstat(fd, &s)) return -1;
    *out = (struct Identity){s.st_dev, s.st_ino, s.st_size, s.st_mode, s.st_uid, s.st_gid, 0, s.st_nlink};
    return 0;
}

static int host_metadata(int from, int to)
{
    struct stat s;
    if (word(fault, "metadata")) { errno = EIO; return -1; }
    if (fstat(from, &s) || fchmod(to, s.st_mode & 07777)) return -1;
    if (word(fault, "metadata_mode")) return fchmod(to, 0600);
    if (word(fault, "metadata_corrupt")) {
        unsigned char byte = 0xff;
        return pwrite(to, &byte, 1, 0) == 1 ? 0 : -1;
    }
    if (word(fault, "source_changed")) {
        char path[1024]; host_path(path, cache_path);
        int fd = open(path, O_WRONLY); unsigned char byte = 0xff;
        if (fd < 0 || pwrite(fd, &byte, 1, 0) != 1) abort();
        close(fd);
    }
    if (word(fault, "source_metadata_changed")) return fchmod(from, 0600);
    return 0;
}

static int host_sync(int fd)
{
    char path[1024]; fd_path(fd, path);
    if (word(fault, "sync_after_replace") && replaced && strstr(path, "/com.apple.dyld")) { errno = EIO; return -1; }
    if (word(fault, "backup_sync") && strstr(path, "/backup.cache")) { errno = EIO; return -1; }
    return fsync(fd);
}

static int host_rename(const char *from, const char *to)
{
    char source[1024], destination[1024]; host_path(source, from); host_path(destination, to);
    if (word(fault, "rename")) { errno = EIO; return -1; }
    int result = rename(source, destination);
    if (result == 0 && word(fault, "installed_corrupt")) {
        int fd = open(destination, O_WRONLY); unsigned char byte = 0xff;
        if (fd < 0 || pwrite(fd, &byte, 1, 0) != 1) abort();
        close(fd);
    }
    return result;
}

static SSize host_write(int fd, const void *data, Size size, Off offset)
{
    char path[1024]; fd_path(fd, path);
    int backup = strstr(path, "/backup.cache") != NULL;
    int staging = strstr(path, ".staging") != NULL;
    int log = strstr(path, "/events.jsonl") != NULL;
    if ((word(fault, "backup_write") && backup) || (word(fault, "staging_write") && staging)
        || (word(fault, "signature_write") && staging && offset == repair_spans[1].offset)
        || (word(fault, "journal_before") && log && memmem(data, size, "ready_to_replace", 16))
        || (word(fault, "journal_after") && log && memmem(data, size, "reboot_required", 15))) {
        errno = EIO; return -1;
    }
    if (word(fault, "short_write") && !injected && staging && size > 1) { injected = 1; size /= 2; }
    return pwrite(fd, data, size, offset);
}

static SSize host_read(int fd, void *data, Size size, Off offset) { return pread(fd, data, size, offset); }
static int host_error(void) { return errno; }
static int host_lock(int fd, int exclusive) { return flock(fd, (exclusive ? LOCK_EX : LOCK_SH) | LOCK_NB); }
static int host_boot(char *out, Size size)
{
    if (word(fault, "boot_identity") || size < 37) return 0;
    memcpy(out, "12345678-1234-1234-1234-123456789abc", 37); return 1;
}
static int (*crypto_init)(void *), (*crypto_update)(void *, const void *, Size), (*crypto_final)(unsigned char *, void *);
static int host_sha_init(void *c) { return crypto_init(c); }
static int host_sha_update(void *c, const void *p, unsigned int n) { return crypto_update(c, p, n); }
static int host_sha_final(unsigned char *p, void *c) { return crypto_final(p, c); }

int main(int argc, char **argv)
{
    if (argc < 4) return 2;
    test_root = argv[1]; fault = argv[2];
    void *crypto = dlopen("libcrypto.so.3", RTLD_NOW);
    if (!crypto) crypto = dlopen("libcrypto.so.1.1", RTLD_NOW);
    if (!crypto) return 3;
    crypto_init = dlsym(crypto, "SHA256_Init"); crypto_update = dlsym(crypto, "SHA256_Update");
    crypto_final = dlsym(crypto, "SHA256_Final");
    if (!crypto_init || !crypto_update || !crypto_final) return 3;
    api = (struct Operations){host_open, close, host_sync, host_lock, host_mkdir,
        host_identity, host_metadata, host_rename, host_error, host_boot,
        host_read, host_write, host_sha_init, host_sha_update, host_sha_final};
    return run(argc - 2, argv + 2);
}
