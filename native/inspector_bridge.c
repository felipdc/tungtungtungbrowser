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
static void (*xpc_activate)(void *), (*xpc_cancel)(void *), (*xpc_release)(void *);
static void *(*xpc_dict)(const char **, void *const *, Size);
static void (*xpc_string)(void *, const char *, const char *);
static void (*xpc_date)(void *, const char *, long), (*xpc_fd)(void *, const char *, int);
static void (*xpc_data)(void *, const char *, const void *, Size), (*xpc_send)(void *, void *);
static unsigned char (*xpc_bool)(void *, const char *);
static void *(*xpc_type)(void *), *dictionary_type;
static void *connection_invalid, *connection_interrupted, *termination_imminent;
static unsigned long generation;
static int acknowledgment;
static int handoff_error;
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
    LOAD(xpc_activate, "xpc_connection_activate"); LOAD(xpc_cancel, "xpc_connection_cancel");
    LOAD(xpc_dict, "xpc_dictionary_create"); LOAD(xpc_string, "xpc_dictionary_set_string");
    LOAD(xpc_date, "xpc_dictionary_set_date"); LOAD(xpc_fd, "xpc_dictionary_set_fd");
    LOAD(xpc_data, "xpc_dictionary_set_data"); LOAD(xpc_send, "xpc_connection_send_message");
    LOAD(xpc_release, "xpc_release"); LOAD(xpc_bool, "xpc_dictionary_get_bool");
    LOAD(xpc_type, "xpc_get_type"); LOAD(dictionary_type, "_xpc_type_dictionary");
#undef LOAD
    connection_invalid = dlsym(library, "_xpc_error_connection_invalid");
    connection_interrupted = dlsym(library, "_xpc_error_connection_interrupted");
    termination_imminent = dlsym(library, "_xpc_error_termination_imminent");
    return 1;
}

static int response(int fd, const char *status, unsigned port, const char *handoff_failure) {
    char data[512];
    int length = snprintf(data, sizeof(data),
        "{\"protocol\":2,\"status\":\"%s\",\"pid\":%d,\"boot_uuid\":\"%s\",\"port\":%u,\"handoff_error\":\"%s\"}\n",
        status, getpid(), boot, port, handoff_failure ? handoff_failure : "");
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
static int command(int fd, char text[96]) {
    for (int i = 0; i < 95; i++) {
        long count = read(fd, text + i, 1);
        if (count < 0 && *__error() == 4) { i--; continue; }
        if (count != 1 || !text[i]) return 0;
        if (text[i] == '\n') { text[i] = 0; return 1; }
    }
    return 0;
}

static void *open_inspector_core(int *native, const char **failure) {
    *native = -1;
    *failure = "initialization_failed";
    int listener = socket(2, 1, 0);
    if (listener < 0) return 0;
    struct InetAddress address = {16, 2, 0, 0x0100007f, {0}};
    U32 size = sizeof(address);
    if (bind(listener, &address, sizeof(address)) || listen(listener, 1)
            || getsockname(listener, &address, &size) || size != sizeof(address)) {
        close(listener); return 0;
    }
    /* Queue our native client before handing the listener to webinspectord.
     * Waiting for ACK before connecting can lose the service's short idle
     * window during cold initialization. This does not send any page command.
     */
    int stream = socket(2, 1, 0), yes = 1;
    struct Timeout timeout = {30, 0};
    if (stream < 0 || setsockopt(stream, 0xffff, 0x1022, &yes, sizeof(yes))
            || setsockopt(stream, 0xffff, 0x1005, &timeout, sizeof(timeout))
            || connect(stream, &address, sizeof(address))) {
        if (stream >= 0) close(stream);
        close(listener); return 0;
    }
    /* Build the entire check-in before activating a service connection. The
     * service can start its idle deadline as soon as that connection activates.
     */
    void *message = xpc_dict(0, 0, 0);
    if (!message) { close(stream); close(listener); return 0; }
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
    if (log >= 0) close(log);
    void *connection = xpc_connection("com.apple.webinspector", 0, 0);
    if (!connection) { xpc_release(message); close(stream); close(listener); return 0; }
    unsigned long own_generation = __atomic_add_fetch(&generation, 1, __ATOMIC_ACQ_REL);
    __atomic_store_n(&acknowledgment, 0, __ATOMIC_RELEASE);
    __atomic_store_n(&handoff_error, 0, __ATOMIC_RELEASE);
    xpc_handler(connection, ^(void *event) {
        if (__atomic_load_n(&generation, __ATOMIC_ACQUIRE) != own_generation) return;
        if (xpc_type(event) == dictionary_type) {
            if (xpc_bool(event, "ACK")) __atomic_store_n(&acknowledgment, 1, __ATOMIC_RELEASE);
        } else {
            int error = event == connection_invalid ? 1 : event == connection_interrupted ? 2
                : event == termination_imminent ? 3 : 4;
            __atomic_store_n(&handoff_error, error, __ATOMIC_RELEASE);
            __atomic_store_n(&acknowledgment, -1, __ATOMIC_RELEASE);
        }
    });
    /* Queue before activation, so construction and descriptor duplication do
     * not consume the service's short initial no-client window. Sending alone
     * is not success: the native ACK remains required below. There is no retry.
     */
    xpc_send(connection, message);
    xpc_activate(connection);
    xpc_release(message);
    for (int i = 0; i < 2000 && !__atomic_load_n(&acknowledgment, __ATOMIC_ACQUIRE); i++) usleep(10000);
    close(listener);
    if (__atomic_load_n(&acknowledgment, __ATOMIC_ACQUIRE) != 1) {
        int error = __atomic_load_n(&handoff_error, __ATOMIC_ACQUIRE);
        const char *reason = error == 1 ? "connection_invalid" : error == 2 ? "connection_interrupted"
            : error == 3 ? "termination_imminent" : error == 4 ? "unexpected_xpc_event" : "acknowledgment_timeout";
        *failure = reason;
        printf("{\"protocol\":2,\"event\":\"inspector_handoff_failed\",\"reason\":\"%s\"}\n", reason);
        fflush(0);
        close(stream);
        xpc_cancel(connection); xpc_release(connection); return 0;
    }
    *failure = 0;
    *native = stream;
    return connection;
}

/* Classify only the owned startup request as user-initiated.
 * A fresh pthread owns that scheduling class; the serial bridge thread is never
 * promoted. Native ACK, descriptor ownership and all handoff bounds are unchanged.
 * Darwin arm64 pthread_attr_t is 64 bytes with long alignment. */
union ThreadAttributes { long alignment; unsigned char bytes[64]; };
struct OpenRequest { int native; const char *failure; void *connection; unsigned qos; };
static unsigned (*current_qos)(void);
static void *open_worker(void *argument) {
    struct OpenRequest *request = argument;
    request->qos = current_qos();
    if (request->qos != 0x19) { request->failure = "startup_qos_not_applied"; return 0; }
    request->connection = open_inspector_core(&request->native, &request->failure);
    return 0;
}
static void *open_inspector(int *native, const char **failure) {
    *native = -1; *failure = "startup_worker_failed";
    void *library = dlopen("/usr/lib/libSystem.B.dylib", 2);
    if (!library) return 0;
    int (*attr_init)(void *) = dlsym(library, "pthread_attr_init");
    int (*attr_destroy)(void *) = dlsym(library, "pthread_attr_destroy");
    int (*attr_qos)(void *, unsigned, int) = dlsym(library, "pthread_attr_set_qos_class_np");
    int (*create)(void **, const void *, void *(*)(void *), void *) = dlsym(library, "pthread_create");
    int (*join)(void *, void **) = dlsym(library, "pthread_join");
    void (*process_exit)(int) = dlsym(library, "_exit");
    current_qos = dlsym(library, "qos_class_self");
    if (!attr_init || !attr_destroy || !attr_qos || !create || !join || !process_exit || !current_qos) return 0;
    unsigned caller_qos = current_qos();
    union ThreadAttributes attributes;
    int error = attr_init(&attributes);
    if (error) return 0;
    error = attr_qos(&attributes, 0x19, 0); /* QOS_CLASS_USER_INITIATED */
    struct OpenRequest request = {-1, "startup_worker_failed", 0, 0};
    void *thread = 0;
    if (!error) error = create(&thread, &attributes, open_worker, &request);
    int destroy_error = attr_destroy(&attributes);
    if (error) return 0;
    int join_error = join(thread, 0);
    /* Never expose a stack request or a socket while a worker may still own it.
     * A join failure terminates this owned bridge, causing terminal host EOF. */
    if (join_error) process_exit(10);
    printf("{\"protocol\":2,\"event\":\"inspector_handoff_schedule\",\"caller_qos\":%u,\"requested_qos\":25,\"worker_qos\":%u,\"caller_qos_after\":%u,\"attributes_destroy_error\":%d,\"unix\":%ld}\n",
           caller_qos, request.qos, current_qos(), destroy_error, time(0));
    fflush(0);
    *native = request.native; *failure = request.failure;
    return request.connection;
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

/* Launch the same executable without retaining the administrative RPC's pipes
 * or unrelated descriptors. The caller owns the returned PID and must verify
 * the native health identity before using it. This is not a launchd service.
 */
static int spawn_owned(const char *executable, const char *log_path) {
    if (executable[0] != '/' || log_path[0] != '/' || equal(executable, log_path)) return 2;
    void *library = dlopen("/usr/lib/libSystem.B.dylib", 2);
    if (!library) return 7;
    int (*actions_init)(void **) = dlsym(library, "posix_spawn_file_actions_init");
    int (*actions_destroy)(void **) = dlsym(library, "posix_spawn_file_actions_destroy");
    int (*add_open)(void **, int, const char *, int, unsigned short) = dlsym(library, "posix_spawn_file_actions_addopen");
    int (*add_dup)(void **, int, int) = dlsym(library, "posix_spawn_file_actions_adddup2");
    int (*add_close)(void **, int) = dlsym(library, "posix_spawn_file_actions_addclose");
    int (*attributes_init)(void **) = dlsym(library, "posix_spawnattr_init");
    int (*attributes_destroy)(void **) = dlsym(library, "posix_spawnattr_destroy");
    int (*set_flags)(void **, short) = dlsym(library, "posix_spawnattr_setflags");
    int (*set_group)(void **, int) = dlsym(library, "posix_spawnattr_setpgroup");
    int (*spawn)(int *, const char *, void **, void **, char *const *, char *const *) = dlsym(library, "posix_spawn");
    if (!actions_init || !actions_destroy || !add_open || !add_dup || !add_close
            || !attributes_init || !attributes_destroy || !set_flags || !set_group || !spawn) return 7;
    /* Darwin O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW. Never replace a log or
     * follow a pre-existing link, including after an uncertain earlier launch.
     */
    int log = open(log_path, 1 | 0x200 | 0x800 | 0x100, 0600);
    if (log < 3) { if (log >= 0) close(log); return 8; }
    void *actions = 0, *attributes = 0;
    int error = actions_init(&actions), pid = 0;
    int actions_ready = !error, attributes_ready = 0;
    if (!error) { error = attributes_init(&attributes); attributes_ready = !error; }
    if (!error) error = add_open(&actions, 0, "/dev/null", 0, 0);
    if (!error) error = add_dup(&actions, log, 1);
    if (!error) error = add_dup(&actions, log, 2);
    if (!error) error = add_close(&actions, log);
    /* POSIX_SPAWN_CLOEXEC_DEFAULT | POSIX_SPAWN_SETPGROUP; a zero process
     * group makes the child its leader, independent of the short-lived caller.
     */
    if (!error) error = set_flags(&attributes, 0x4002);
    if (!error) error = set_group(&attributes, 0);
    char *arguments[] = {(char *)executable, "serve", 0};
    char *environment[] = {"PATH=/usr/bin:/bin:/usr/sbin:/sbin", 0};
    if (!error) error = spawn(&pid, executable, &actions, &attributes, arguments, environment);
    int actions_error = actions_ready ? actions_destroy(&actions) : 0;
    int attributes_error = attributes_ready ? attributes_destroy(&attributes) : 0;
    close(log);
    printf("{\"protocol\":2,\"status\":\"%s\",\"pid\":%d,\"boot_uuid\":\"%s\",\"port\":1343,"
           "\"spawn_error\":%d,\"actions_destroy_error\":%d,\"attributes_destroy_error\":%d}\n",
           error ? "spawn_failed" : "spawned", pid, boot, error, actions_error, attributes_error);
    fflush(0);
    return error ? 9 : 0;
}

int main(int argc, char **argv) {
    int spawning = argc == 3 && equal(argv[1], "spawn");
    if (!spawning && (argc != 2 || !equal(argv[1], "serve"))) return 2;
    if (!initialize()) return 3;
    if (spawning) return spawn_owned(argv[0], argv[2]);
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
        char text[96], owned_stop[96];
        snprintf(owned_stop, sizeof(owned_stop), "stop %d %s", getpid(), boot);
        if (!command(client, text)) { close(client); continue; }
        if (equal(text, "health")) response(client, "ready", 1343, 0);
        else if (equal(text, "stop") || equal(text, owned_stop)) {
            response(client, "stopping", 1343, 0); stopping = 1;
        }
        else if (equal(text, "open")) {
            int native = -1; const char *failure = 0;
            void *connection = open_inspector(&native, &failure);
            if (!connection) response(client, "open_failed", 0, failure);
            else {
                timeout.seconds = 30;
                int ready = native >= 0 && !setsockopt(client, 0xffff, 0x1005, &timeout, sizeof(timeout));
                if (ready) {
                    if (response(client, "opened", 1343, 0)) relay(client, native);
                } else {
                    response(client, "connect_failed", 0, 0);
                }
                if (native >= 0) close(native);
                __atomic_add_fetch(&generation, 1, __ATOMIC_ACQ_REL);
                xpc_cancel(connection); xpc_release(connection);
            }
        } else response(client, "invalid_request", 0, 0);
        close(client);
    }
    close(listener);
    return 0;
}
