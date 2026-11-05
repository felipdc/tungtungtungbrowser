/* SPDX-License-Identifier: MIT
 * Open one native Web Inspector service for an owning host connection.
 * No page scripts, automation sessions, preference changes, or browser hooks.
 * The Darwin bindings below are resolved against the guest's own libraries.
 */
typedef unsigned long Size;
typedef unsigned int U32;
extern void *dlopen(const char *, int), *dlsym(void *, const char *);
extern int socket(int, int, int), bind(int, const void *, U32), listen(int, int);
extern int accept(int, void *, U32 *), getsockname(int, void *, U32 *);
extern int connect(int, const void *, U32), poll(void *, unsigned, int);
extern int setsockopt(int, int, int, const void *, U32), close(int), open(const char *, int, ...);
extern int printf(const char *, ...), snprintf(char *, Size, const char *, ...), fflush(void *);
extern int getpid(void), usleep(unsigned), *__error(void);
extern int sysctlbyname(const char *, void *, Size *, const void *, Size);
extern long read(int, void *, Size), write(int, const void *, Size), time(long *);

struct VsockAddress { unsigned char length, family; unsigned short reserved; U32 port, cid; };
struct InetAddress { unsigned char length, family; unsigned short port; U32 address; char zero[8]; };
struct Timeout { long seconds; int microseconds; };
struct Poll { int fd; short events, returned; };
_Static_assert(sizeof(struct VsockAddress) == 12 && sizeof(struct InetAddress) == 16
               && sizeof(struct Timeout) == 16, "Darwin socket ABI");

static void *(*xpc_connection)(const char *, void *, Size);
static void (*xpc_handler)(void *, void (^)(void *));
static void (*xpc_resume)(void *), (*xpc_cancel)(void *), (*xpc_release)(void *);
static void *(*xpc_dict)(const char **, void *const *, Size);
static void (*xpc_string)(void *, const char *, const char *);
static void (*xpc_date)(void *, const char *, long), (*xpc_fd)(void *, const char *, int);
static void (*xpc_data)(void *, const char *, const void *, Size), (*xpc_send)(void *, void *);
static unsigned char (*xpc_bool)(void *, const char *);
static void *(*xpc_type)(void *), *dictionary_type;
static unsigned long generation;
static int acknowledgment;
static char boot[37];

static int equal(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return *a == *b;
}

static int initialize(void) {
    Size size = sizeof(boot);
    if (sysctlbyname("kern.bootsessionuuid", boot, &size, 0, 0) || size != 37 || boot[36]) return 0;
    for (int i = 0; i < 36; i++) {
        if (i == 8 || i == 13 || i == 18 || i == 23) { if (boot[i] != '-') return 0; }
        else if (!((boot[i] >= '0' && boot[i] <= '9') || (boot[i] >= 'A' && boot[i] <= 'F')
                   || (boot[i] >= 'a' && boot[i] <= 'f'))) return 0;
    }
    void *library = dlopen("/usr/lib/system/libxpc.dylib", 2);
    if (!library) return 0;
#define LOAD(variable, symbol) variable = dlsym(library, symbol); if (!variable) return 0
    LOAD(xpc_connection, "xpc_connection_create_mach_service");
    LOAD(xpc_handler, "xpc_connection_set_event_handler");
    LOAD(xpc_resume, "xpc_connection_resume"); LOAD(xpc_cancel, "xpc_connection_cancel");
    LOAD(xpc_dict, "xpc_dictionary_create"); LOAD(xpc_string, "xpc_dictionary_set_string");
    LOAD(xpc_date, "xpc_dictionary_set_date"); LOAD(xpc_fd, "xpc_dictionary_set_fd");
    LOAD(xpc_data, "xpc_dictionary_set_data"); LOAD(xpc_send, "xpc_connection_send_message");
    LOAD(xpc_release, "xpc_release"); LOAD(xpc_bool, "xpc_dictionary_get_bool");
    LOAD(xpc_type, "xpc_get_type"); LOAD(dictionary_type, "_xpc_type_dictionary");
#undef LOAD
    return 1;
}

static int response(int fd, const char *status, unsigned port) {
    char data[512];
    int length = snprintf(data, sizeof(data),
        "{\"protocol\":2,\"status\":\"%s\",\"pid\":%d,\"boot_uuid\":\"%s\",\"port\":%u}\n",
        status, getpid(), boot, port);
    if (length < 0 || (Size)length >= sizeof(data)) return 0;
    Size done = 0;
    while (done < (Size)length) {
        long count = write(fd, data + done, (Size)length - done);
        if (count < 0 && *__error() == 4) continue;
        if (count <= 0) return 0;
        done += (Size)count;
    }
    return 1;
}

/* Read exactly one line without consuming a following command. */
static int command(int fd, char text[16]) {
    for (int i = 0; i < 15; i++) {
        long count = read(fd, text + i, 1);
        if (count < 0 && *__error() == 4) { i--; continue; }
        if (count != 1 || !text[i]) return 0;
        if (text[i] == '\n') { text[i] = 0; return 1; }
    }
    return 0;
}

static void *open_inspector(unsigned *port) {
    int listener = socket(2, 1, 0);
    if (listener < 0) return 0;
    struct InetAddress address = {16, 2, 0, 0x0100007f, {0}};
    U32 size = sizeof(address);
    if (bind(listener, &address, sizeof(address)) || listen(listener, 1)
            || getsockname(listener, &address, &size) || size != sizeof(address)) {
        close(listener); return 0;
    }
    *port = (address.port >> 8) | ((address.port & 255) << 8);
    void *connection = xpc_connection("com.apple.webinspector", 0, 0);
    if (!connection) { close(listener); return 0; }
    unsigned long own_generation = __atomic_add_fetch(&generation, 1, __ATOMIC_ACQ_REL);
    __atomic_store_n(&acknowledgment, 0, __ATOMIC_RELEASE);
    xpc_handler(connection, ^(void *event) {
        if (__atomic_load_n(&generation, __ATOMIC_ACQUIRE) != own_generation) return;
        if (xpc_type(event) == dictionary_type) {
            if (xpc_bool(event, "ACK")) __atomic_store_n(&acknowledgment, 1, __ATOMIC_RELEASE);
        } else __atomic_store_n(&acknowledgment, -1, __ATOMIC_RELEASE);
    });
    xpc_resume(connection);
    void *message = xpc_dict(0, 0, 0);
    if (!message) { close(listener); xpc_cancel(connection); xpc_release(connection); return 0; }
    /* Empty binary property list dictionary, authored here. The check-in data
     * is serialized plist data; the socket is a listener that the service accepts.
     * No pairing certificates, identity overrides, or TLS interception are supplied.
     */
    const unsigned char empty_dictionary[] = {
        98,112,108,105,115,116,48,48,208,8,0,0,0,0,0,0,1,1,0,0,0,0,0,0,0,1,
        0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,9
    };
    xpc_string(message, "_LDCHECKININFO", "com.apple.webinspector");
    xpc_date(message, "_LDTIMESTAMP", time(0) * 1000000000L);
    xpc_data(message, "_LDCHECKINDICT", empty_dictionary, sizeof(empty_dictionary));
    xpc_fd(message, "_LDSERVICESOCK", listener);
    int log = open("/dev/null", 1);
    if (log >= 0) xpc_fd(message, "_LDSERVICELOGSOCK", log);
    xpc_send(connection, message);
    xpc_release(message);
    if (log >= 0) close(log);
    for (int i = 0; i < 2000 && !__atomic_load_n(&acknowledgment, __ATOMIC_ACQUIRE); i++) usleep(10000);
    close(listener);
    if (__atomic_load_n(&acknowledgment, __ATOMIC_ACQUIRE) != 1) {
        xpc_cancel(connection); xpc_release(connection); return 0;
    }
    return connection;
}

/* The owner carries the native plist stream directly. Closing either side
 * closes both descriptors and the XPC handoff; no separate data connection or
 * timer is needed to decide whether the session still has an owner.
 */
static void relay(int owner, int native) {
    char buffer[16384];
    struct Poll descriptors[2] = {{owner, 1, 0}, {native, 1, 0}};
    while (1) {
        int result = poll(descriptors, 2, -1);
        if (result < 0 && *__error() == 4) continue;
        if (result <= 0) return;
        for (int i = 0; i < 2; i++) {
            if (!(descriptors[i].returned & (1 | 8 | 16 | 32))) continue;
            long count = read(descriptors[i].fd, buffer, sizeof(buffer));
            if (count < 0 && *__error() == 4) continue;
            if (count <= 0) return;
            long done = 0;
            while (done < count) {
                long sent = write(descriptors[1-i].fd, buffer + done, (Size)(count - done));
                if (sent < 0 && *__error() == 4) continue;
                if (sent <= 0) return;
                done += sent;
            }
        }
    }
}

int main(int argc, char **argv) {
    if (argc != 2 || !equal(argv[1], "serve")) return 2;
    if (!initialize()) return 3;
    int listener = socket(40, 1, 0);
    if (listener < 0) return 4;
    struct VsockAddress address = {12, 40, 0, 1343, 0xffffffffU};
    if (bind(listener, &address, sizeof(address)) || listen(listener, 4)) { close(listener); return 5; }
    printf("{\"protocol\":2,\"status\":\"listening\",\"pid\":%d,\"boot_uuid\":\"%s\",\"port\":1343}\n", getpid(), boot);
    fflush(0);
    int stopping = 0;
    while (!stopping) {
        struct VsockAddress peer = {0}; U32 size = sizeof(peer);
        int client = accept(listener, &peer, &size);
        if (client < 0) { if (*__error() == 4) continue; close(listener); return 6; }
        if (size != sizeof(peer) || peer.family != 40 || peer.cid != 2) { close(client); continue; }
        int yes = 1; struct Timeout timeout = {10, 0};
        if (setsockopt(client, 0xffff, 0x1022, &yes, sizeof(yes))
                || setsockopt(client, 0xffff, 0x1005, &timeout, sizeof(timeout))
                || setsockopt(client, 0xffff, 0x1006, &timeout, sizeof(timeout))) { close(client); continue; }
        char text[16];
        if (!command(client, text)) { close(client); continue; }
        if (equal(text, "health")) response(client, "ready", 1343);
        else if (equal(text, "stop")) { response(client, "stopping", 1343); stopping = 1; }
        else if (equal(text, "open")) {
            unsigned port = 0; void *connection = open_inspector(&port);
            if (!connection) response(client, "open_failed", 0);
            else {
                int native = socket(2, 1, 0);
                struct InetAddress endpoint = {16, 2,
                    (unsigned short)((port >> 8) | ((port & 255) << 8)), 0x0100007f, {0}};
                timeout.seconds = 30;
                int ready = native >= 0 && !setsockopt(native, 0xffff, 0x1022, &yes, sizeof(yes))
                    && !setsockopt(native, 0xffff, 0x1005, &timeout, sizeof(timeout))
                    && !setsockopt(client, 0xffff, 0x1005, &timeout, sizeof(timeout))
                    && !connect(native, &endpoint, sizeof(endpoint));
                if (ready) {
                    if (response(client, "opened", 1343)) relay(client, native);
                } else {
                    response(client, "connect_failed", 0);
                }
                if (native >= 0) close(native);
                __atomic_add_fetch(&generation, 1, __ATOMIC_ACQ_REL);
                xpc_cancel(connection); xpc_release(connection);
            }
        } else response(client, "invalid_request", 0);
        close(client);
    }
    close(listener);
    return 0;
}
