/* Configure Safari's own WebKit proxy defaults, never system proxy settings. */
typedef unsigned long Size;
typedef long Index;
typedef unsigned char Boolean;
typedef const void *Ref;
extern void *dlopen(const char *, int), *dlsym(void *, const char *);
extern int strcmp(const char *, const char *), getuid(void), geteuid(void);
extern int setuid(unsigned), setgid(unsigned), printf(const char *, ...);
extern long write(int, const void *, Size);
extern unsigned alarm(unsigned);

#define LOAD(handle, type, name) type name = (type)dlsym(handle, #name); if (!name) return 4
/* Function-pointer typedefs keep this build independent of an Apple SDK. */
typedef void *(*GetClass)(const char *);
typedef void *(*Selector)(const char *);
typedef void *(*Message)(void *, void *);
typedef void *(*Message1)(void *, void *, Ref);
typedef Ref (*String)(Ref, const char *, unsigned);
typedef Ref (*Array)(Ref, const Ref *, Index, const void *);
typedef Ref (*Dictionary)(Ref, Index, const void *, const void *);
typedef void (*Put)(Ref, Ref, Ref);
typedef Ref (*Lookup)(Ref, Ref);
typedef Boolean (*Equal)(Ref, Ref);
typedef Index (*Count)(Ref);
typedef void (*SetPreference)(Ref, Ref, Ref, Ref, Ref, Ref);
typedef Ref (*GetPreferences)(Ref, Ref, Ref, Ref, Ref);
typedef Boolean (*SyncPreferences)(Ref, Ref, Ref, Ref);
typedef Ref (*Serialize)(Ref, Ref, Index, Size, Ref *);
typedef const unsigned char *(*Bytes)(Ref);
typedef int (*ListPids)(void *, int);
typedef int (*Name)(int, void *, unsigned);

int main(int argc, char **argv) {
    alarm(25);
    if (argc != 2 || (strcmp(argv[1], "read") && strcmp(argv[1], "enable") && strcmp(argv[1], "disable"))) return 2;
    int change = strcmp(argv[1], "read") != 0;
    void *system = dlopen("/usr/lib/libSystem.B.dylib", 2);
    void *cf = dlopen("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation", 2);
    void *objc = dlopen("/usr/lib/libobjc.A.dylib", 2);
    if (!system || !cf || !objc || !dlopen("/System/Library/Frameworks/Foundation.framework/Foundation", 2)
        || !dlopen("/System/Library/Frameworks/CoreServices.framework/CoreServices", 2)) return 3;
    LOAD(system, ListPids, proc_listallpids);
    LOAD(system, Name, proc_name);
    if (change) {
        int pids[4096], n = proc_listallpids(pids, sizeof(pids));
        if (n <= 0 || n >= 4096) return 5;
        for (int i = 0; i < n; i++) {
            char name[256] = {0};
            if (proc_name(pids[i], name, sizeof(name)) > 0 && !strcmp(name, "MobileSafari")) {
                printf("Safari must be stopped before changing its proxy preferences.\n"); return 6;
            }
        }
    }
    LOAD(objc, GetClass, objc_getClass);
    LOAD(objc, Selector, sel_registerName);
    LOAD(objc, Message, objc_msgSend);
    Message1 send1 = (Message1)dlsym(objc, "objc_msgSend");
    if (!send1) return 4;
    LOAD(cf, String, CFStringCreateWithCString);
    LOAD(cf, Array, CFArrayCreate);
    LOAD(cf, Dictionary, CFDictionaryCreateMutable);
    LOAD(cf, Put, CFDictionarySetValue);
    LOAD(cf, Lookup, CFDictionaryGetValue);
    LOAD(cf, Count, CFDictionaryGetCount);
    LOAD(cf, Equal, CFEqual);
    LOAD(cf, SetPreference, _CFPreferencesSetValueWithContainer);
    LOAD(cf, GetPreferences, _CFPreferencesCopyMultipleWithContainer);
    LOAD(cf, SyncPreferences, _CFPreferencesSynchronizeWithContainer);
    LOAD(cf, Serialize, CFPropertyListCreateData);
    LOAD(cf, Bytes, CFDataGetBytePtr);
    LOAD(cf, Count, CFDataGetLength);
    const void *arrayCallbacks = dlsym(cf, "kCFTypeArrayCallBacks");
    const void *keyCallbacks = dlsym(cf, "kCFTypeDictionaryKeyCallBacks");
    const void *valueCallbacks = dlsym(cf, "kCFTypeDictionaryValueCallBacks");
    Ref *anyHost = dlsym(cf, "kCFPreferencesAnyHost");
    Ref *currentUser = dlsym(cf, "kCFPreferencesCurrentUser");
    if (!arrayCallbacks || !keyCallbacks || !valueCallbacks || !anyHost || !currentUser) return 4;
#define S(value) CFStringCreateWithCString(0, value, 0x08000100)
    Ref application = S("com.apple.mobilesafari");
    void *proxy = send1(objc_getClass("LSApplicationProxy"), sel_registerName("applicationProxyForIdentifier:"), application);
    void *url = proxy ? objc_msgSend(proxy, sel_registerName("dataContainerURL")) : 0;
    Ref container = url ? objc_msgSend(url, sel_registerName("path")) : 0;
    if (!container) return 7;
    if (geteuid() == 0 && (setgid(501) || setuid(501))) return 8;
    if (getuid() != 501 || geteuid() != 501) return 8;
    Ref keys[2] = {S("WebKit2HTTPProxy"), S("WebKit2HTTPSProxy")};
    Ref keyList = CFArrayCreate(0, keys, 2, arrayCallbacks);
    Ref before = _CFPreferencesCopyMultipleWithContainer(keyList, application, *currentUser, *anyHost, container);
    if (!before) return 9;
    Ref endpoint = S("http://10.0.2.2:1080");
    if (change) {
        for (int i = 0; i < 2; i++) {
            Ref existing = CFDictionaryGetValue(before, keys[i]);
            if (existing && !CFEqual(existing, endpoint)) {
                printf("Existing Safari proxy preference is not owned by this helper.\n"); return 16;
            }
        }
        Ref value = !strcmp(argv[1], "enable") ? endpoint : 0;
        for (int i = 0; i < 2; i++) _CFPreferencesSetValueWithContainer(keys[i], value, application, *currentUser, *anyHost, container);
        if (!_CFPreferencesSynchronizeWithContainer(application, *currentUser, *anyHost, container)) return 10;
    }
    Ref after = _CFPreferencesCopyMultipleWithContainer(keyList, application, *currentUser, *anyHost, container);
    if (!after) return 11;
    if (!strcmp(argv[1], "enable")) {
        for (int i = 0; i < 2; i++) {
            Ref value = CFDictionaryGetValue(after, keys[i]);
            if (!value || !CFEqual(value, endpoint)) return 12;
        }
    } else if (!strcmp(argv[1], "disable") && CFDictionaryGetCount(after)) return 12;
    Ref report = CFDictionaryCreateMutable(0, 0, keyCallbacks, valueCallbacks);
    CFDictionarySetValue(report, S("status"), S("complete"));
    CFDictionarySetValue(report, S("action"), S(argv[1]));
    CFDictionarySetValue(report, S("application"), application);
    CFDictionarySetValue(report, S("container"), container);
    CFDictionarySetValue(report, S("before"), before);
    CFDictionarySetValue(report, S("after"), after);
    Ref data = CFPropertyListCreateData(0, report, 100, 0, 0);
    if (!data) return 13;
    const unsigned char *bytes = CFDataGetBytePtr(data);
    Index length = CFDataGetLength(data), offset = 0;
    if (length <= 0 || length > 65536) return 14;
    while (offset < length) {
        long n = write(1, bytes + offset, (Size)(length - offset));
        if (n <= 0) return 15;
        offset += n;
    }
    return 0;
}
