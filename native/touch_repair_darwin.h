/* SPDX-License-Identifier: MIT
 * Minimal arm64 Darwin bindings, resolved from the guest's libraries.
 * ABI references: apple-oss-distributions/xnu bsd/sys/{fcntl,stat}.h and
 * apple-oss-distributions/copyfile copyfile.h. No SDK or OS code is included.
 */
extern void *dlopen(const char *, int);
extern void *dlsym(void *, const char *);
static int (*native_open)(const char *, int, ...);
static int (*native_close)(int), (*native_sync)(int), (*native_lock)(int, int);
static int (*native_mkdir)(const char *, unsigned short);
static int (*native_stat)(int, void *), (*native_copy)(int, int, void *, unsigned int);
static int (*native_rename)(const char *, const char *), *(*native_errno)(void);
static int (*native_sysctl)(const char *, void *, Size *, const void *, Size);

static U64 little(const unsigned char *bytes, int count)
{
    U64 value = 0;
    for (int i = 0; i < count; i++) value |= (U64)bytes[i] << (i * 8);
    return value;
}

static int darwin_identity(int fd, struct Identity *out)
{
    U64 storage[32] = {0};
    unsigned char *data = (unsigned char *)storage;
    if (native_stat(fd, storage)) return -1;
    /* arm64 Darwin uses the 144-byte, 64-bit-inode stat layout. */
    out->device = little(data, 4); out->mode = (unsigned int)little(data + 4, 2);
    out->links = (unsigned int)little(data + 6, 2); out->inode = little(data + 8, 8);
    out->uid = (unsigned int)little(data + 16, 4); out->gid = (unsigned int)little(data + 20, 4);
    out->size = little(data + 96, 8); out->flags = (unsigned int)little(data + 116, 4);
    return 0;
}

static int darwin_open(const char *path, int kind)
{
    /* O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, plus O_RDWR|O_CREAT|O_EXCL
     * for new files or O_DIRECTORY for directories. Never truncate. */
    int flags = 0x01000104 | (kind == 1 ? 0xa02 : 0) | (kind == 2 ? 0x00100000 : 0);
    int fd = native_open(path, flags, 0600);
    if (fd < 0) return fd;
    struct Identity value;
    if (darwin_identity(fd, &value) || (value.mode & 0170000) != (kind == 2 ? 0040000 : 0100000)) {
        native_close(fd); *native_errno() = 22; return -1;
    }
    return fd;
}

static int darwin_mkdir(const char *path) { return native_mkdir(path, 0700); }
static int darwin_error(void) { return *native_errno(); }
static int darwin_lock(int fd, int exclusive) { return native_lock(fd, (exclusive ? 2 : 1) | 4); }
static int darwin_metadata(int from, int to)
{
    /* COPYFILE_ACL | COPYFILE_STAT | COPYFILE_XATTR, without COPYFILE_DATA. */
    return native_copy(from, to, (void *)0, 7);
}

static int darwin_boot_uuid(char *out, Size capacity)
{
    Size size = capacity;
    if (native_sysctl("kern.bootsessionuuid", out, &size, (void *)0, 0) || size != 37 || out[36]) return 0;
    for (int i = 0; i < 36; i++) {
        if (i == 8 || i == 13 || i == 18 || i == 23) { if (out[i] != '-') return 0; }
        else if (!((out[i] >= '0' && out[i] <= '9') || (out[i] >= 'a' && out[i] <= 'f')
                   || (out[i] >= 'A' && out[i] <= 'F'))) return 0;
    }
    return 1;
}

static int initialize_native(void)
{
    void *lib = dlopen("/usr/lib/libSystem.B.dylib", 2);
    void *crypto = dlopen("/usr/lib/system/libcommonCrypto.dylib", 2);
    if (!lib || !crypto) return 0;
#define LOAD(target, handle, name) target = dlsym(handle, name); if (!target) return 0
    LOAD(native_open, lib, "open"); LOAD(native_close, lib, "close");
    LOAD(native_sync, lib, "fsync"); LOAD(native_lock, lib, "flock");
    LOAD(native_mkdir, lib, "mkdir"); LOAD(native_stat, lib, "fstat");
    LOAD(native_copy, lib, "fcopyfile"); LOAD(native_rename, lib, "rename");
    LOAD(native_errno, lib, "__error"); LOAD(native_sysctl, lib, "sysctlbyname");
    LOAD(api.read_at, lib, "pread"); LOAD(api.write_at, lib, "pwrite");
    LOAD(api.sha_init, crypto, "CC_SHA256_Init");
    LOAD(api.sha_update, crypto, "CC_SHA256_Update");
    LOAD(api.sha_final, crypto, "CC_SHA256_Final");
#undef LOAD
    api.open = darwin_open; api.close = native_close; api.sync = native_sync;
    api.lock = darwin_lock; api.mkdir = darwin_mkdir; api.identity = darwin_identity;
    api.metadata = darwin_metadata; api.rename = native_rename; api.error = darwin_error;
    api.boot_uuid = darwin_boot_uuid;
    return 1;
}
