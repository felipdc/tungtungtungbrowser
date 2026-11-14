/* Optional Safari-scoped UDP policy. Owned by this process, removed on exit. */
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


static char boot[37];
static int equal(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return *a == *b;
}

static int initialize(void){Size n=sizeof(boot);return !sysctlbyname("kern.bootsessionuuid",boot,&n,0,0)&&n==37&&!boot[36];}
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

/* A scoped, ephemeral NECP policy. No routes or system-wide defaults are changed. */
static void *guard_session, *guard_message, *(*guard_class)(const char*), *(*guard_sel)(const char*);
static void *guard_call0(void*o,const char*s){return ((void*(*)(void*,void*))guard_message)(o,guard_sel(s));}
static void *guard_call1(void*o,const char*s,void*a){return ((void*(*)(void*,void*,void*))guard_message)(o,guard_sel(s),a);}
static const char *guard_utf8(void*o){return o?((const char*(*)(void*,void*))guard_message)(o,guard_sel("UTF8String")):"";}
static int guard_start(void){
 void*objc=dlopen("/usr/lib/libobjc.A.dylib",2),*cf=dlopen("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",2);
 if(!objc||!cf||!dlopen("/System/Library/Frameworks/Foundation.framework/Foundation",2)||!dlopen("/System/Library/Frameworks/NetworkExtension.framework/NetworkExtension",2)||!dlopen("/System/Library/Frameworks/CoreServices.framework/CoreServices",2))return 20;
 guard_class=dlsym(objc,"objc_getClass");guard_sel=dlsym(objc,"sel_registerName");guard_message=dlsym(objc,"objc_msgSend");
 void*(*str)(void*,const char*,unsigned)=dlsym(cf,"CFStringCreateWithCString");if(!guard_class||!guard_sel||!guard_message||!str)return 21;
 void*app=guard_call1(guard_class("LSApplicationProxy"),"applicationProxyForIdentifier:",str(0,"com.apple.mobilesafari",0x08000100));
 void*bundle=guard_call1(guard_class("NSBundle"),"bundleWithURL:",guard_call0(app,"bundleURL"));
 void*data=guard_call1(guard_class("NSData"),"dataWithContentsOfURL:",guard_call0(bundle,"executableURL"));
 Size length=((Size(*)(void*,void*))guard_message)(data,guard_sel("length"));if(length<32||length>64*1024*1024)return 22;
 const unsigned char*bytes=guard_call0(data,"bytes");const U32*header=(const U32*)bytes;if(header[0]!=0xfeedfacf||header[1]!=0x100000c||header[4]>4096||header[5]>length-32)return 23;
 const unsigned char*uuid=0;Size offset=32;for(U32 i=0;i<header[4];i++){if(offset+8>length)return 24;const U32*cmd=(const U32*)(bytes+offset);if(cmd[1]<8||offset+cmd[1]>length)return 24;if(cmd[0]==0x1b){if(uuid||cmd[1]!=24)return 24;uuid=bytes+offset+8;}offset+=cmd[1];}if(!uuid)return 25;
 void*app_uuid=guard_call1(guard_call0(guard_class("NSUUID"),"alloc"),"initWithUUIDBytes:",(void*)uuid);if(!app_uuid)return 26;
 void*conditions[2];conditions[0]=guard_call1(guard_class("NEPolicyCondition"),"effectiveApplication:",app_uuid);
 conditions[1]=((void*(*)(void*,void*,unsigned))guard_message)(guard_class("NEPolicyCondition"),guard_sel("ipProtocol:"),17);
 if(!conditions[0]||!conditions[1])return 27;
 void*array=((void*(*)(void*,void*,void**,Size))guard_message)(guard_class("NSArray"),guard_sel("arrayWithObjects:count:"),conditions,2);
 void*result=((void*(*)(void*,void*,unsigned))guard_message)(guard_class("NEPolicyResult"),guard_sel("dropWithFlags:"),2);
 void*policy=((void*(*)(void*,void*,unsigned,void*,void*))guard_message)(guard_call0(guard_class("NEPolicy"),"alloc"),guard_sel("initWithOrder:result:conditions:"),1,result,array);
 guard_session=guard_call0(guard_call0(guard_class("NEPolicySession"),"alloc"),"initFromPrivilegedProcess");if(!policy||!guard_session)return 28;
 ((void(*)(void*,void*,unsigned))guard_message)(guard_session,guard_sel("setPriority:"),4);
 unsigned long long policy_id=((unsigned long long(*)(void*,void*,void*))guard_message)(guard_session,guard_sel("addPolicy:"),policy);if(!policy_id)return 29;
 if(!((unsigned char(*)(void*,void*))guard_message)(guard_session,guard_sel("apply")))return 30;
 printf("{\"status\":\"safari_udp_policy_applied\",\"policy_id\":%llu,\"application_uuid\":\"%s\",\"protocol\":17,\"priority\":4,\"other_applications\":\"unchanged\"}\n",policy_id,guard_utf8(guard_call0(app_uuid,"UUIDString")));fflush(0);return 0;
}
static int guard_stop(void){
 int removed=((unsigned char(*)(void*,void*))guard_message)(guard_session,guard_sel("removeAllPolicies"));
 int applied=((unsigned char(*)(void*,void*))guard_message)(guard_session,guard_sel("apply"));
 guard_call0(guard_session,"release");guard_session=0;
 printf("{\"status\":\"owned_policy_session_closed\",\"removed\":%s,\"applied\":%s}\n",removed?"true":"false",applied?"true":"false");fflush(0);return removed&&applied?0:31;
}

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
    printf("{\"protocol\":2,\"status\":\"%s\",\"pid\":%d,\"boot_uuid\":\"%s\",\"port\":1345,"
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
    struct VsockAddress address = {12, 40, 0, 1345, 0xffffffffU};
    if (bind(listener, &address, sizeof(address)) || listen(listener, 4)) { close(listener); return 5; }
    int setup = guard_start();
    if (setup) { printf("{\"status\":\"policy_failed\",\"code\":%d}\n", setup); fflush(0); close(listener); return setup; }
    printf("{\"protocol\":2,\"status\":\"listening\",\"pid\":%d,\"boot_uuid\":\"%s\",\"port\":1345}\n", getpid(), boot);
    fflush(0);
    int stopping = 0;
    while (!stopping) {
        struct VsockAddress peer = {0}; U32 size = sizeof(peer);
        int client = accept(listener, &peer, &size);
        if (client < 0) { if (*__error() == 4) continue; close(listener); guard_stop(); return 6; }
        if (size != sizeof(peer) || peer.family != 40 || peer.cid != 2) { close(client); continue; }
        int yes = 1; struct Timeout timeout = {10, 0};
        if (setsockopt(client, 0xffff, 0x1022, &yes, sizeof(yes))
                || setsockopt(client, 0xffff, 0x1005, &timeout, sizeof(timeout))
                || setsockopt(client, 0xffff, 0x1006, &timeout, sizeof(timeout))) { close(client); continue; }
        char text[96], owned_stop[96];
        snprintf(owned_stop, sizeof(owned_stop), "stop %d %s", getpid(), boot);
        if (!command(client, text)) { close(client); continue; }
        if (equal(text, "health")) response(client, "ready", 1345, 0);
        else if (equal(text, owned_stop)) {
            response(client, "stopping", 1345, 0); stopping = 1;
        }
        else response(client, "invalid_request", 0, 0);
        close(client);
    }
    close(listener);
    return guard_stop();
}
