/* Project-authored native iOS digitizer helper. No Safari or WebKit hooks.
 * All event objects are allocated before the first dispatch. An interrupted
 * hold still dispatches release. Dispatch is not proof of UI delivery. */
typedef unsigned long long U64;
typedef unsigned int U32;
typedef unsigned char Bool;
typedef void *Ref;
extern void *dlopen(const char *, int);
extern void *dlsym(void *, const char *);
extern int printf(const char *, ...);
extern double strtod(const char *, char **);
extern int usleep(unsigned int);
extern U64 mach_absolute_time(void);
typedef Ref (*Hand)(Ref,U64,U32,U32,U32,U32,U32,double,double,double,double,double,Bool,Bool,U32);
typedef Ref (*Finger)(Ref,U64,U32,U32,U32,double,double,double,double,double,Bool,Bool,U32);

static int equal(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return *a == *b;
}
static int number(const char *s, double *value, double minimum, double maximum) {
    char *end = 0;
    if (!s[0] || s[0] == ' ' || s[0] == '\t' || s[0] == '\n') return 0;
    *value = strtod(s, &end);
    return end != s && !*end && __builtin_isfinite(*value) && *value >= minimum && *value <= maximum;
}
static int failure(const char *stage, int code) {
    printf("{\"protocol\":1,\"status\":\"failed\",\"stage\":\"%s\",\"dispatch_count\":0}\n", stage);
    return code;
}

int main(int argc, char **argv) {
    int check = argc == 2 && equal(argv[1], "check");
    double nx = 0, ny = 0, radius = 0, hold = 0;
    if (!check && (argc != 6 || !equal(argv[1], "tap") ||
        !number(argv[2], &nx, 0, 1) || !number(argv[3], &ny, 0, 1) ||
        !number(argv[4], &radius, 0.000001, 0.05) ||
        !number(argv[5], &hold, 1, 1000))) return failure("arguments", 2);

    void *io = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit", 2);
    void *cf = dlopen("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation", 2);
    if (!io || !cf) return failure("frameworks", 3);
    Ref (*clientCreate)(Ref) = dlsym(io, "IOHIDEventSystemClientCreate");
    Hand handCreate = (Hand)dlsym(io, "IOHIDEventCreateDigitizerEvent");
    Finger fingerCreate = (Finger)dlsym(io, "IOHIDEventCreateDigitizerFingerEvent");
    void (*setInteger)(Ref,U32,long) = dlsym(io, "IOHIDEventSetIntegerValue");
    void (*setFloat)(Ref,U32,double) = dlsym(io, "IOHIDEventSetFloatValue");
    void (*setTime)(Ref,U64) = dlsym(io, "IOHIDEventSetTimeStamp");
    void (*setSender)(Ref,U64) = dlsym(io, "IOHIDEventSetSenderID");
    void (*append)(Ref,Ref,U32) = dlsym(io, "IOHIDEventAppendEvent");
    void (*dispatch)(Ref,Ref) = dlsym(io, "IOHIDEventSystemClientDispatchEvent");
    void (*release)(Ref) = dlsym(cf, "CFRelease");
    if (!clientCreate || !handCreate || !fingerCreate || !setInteger || !setFloat ||
        !setTime || !setSender || !append || !dispatch || !release) return failure("symbols", 4);
    Ref client = clientCreate(0);
    if (!client) return failure("client", 5);
    if (check) {
        release(client);
        printf("{\"protocol\":1,\"status\":\"ready\",\"dispatch_count\":0}\n");
        return 0;
    }
    Ref hands[2] = {0,0}, fingers[2] = {0,0};
    for (int phase = 0; phase < 2; phase++) {
        Bool down = phase == 0;
        U32 mask = down ? 3 : 2;
        hands[phase] = handCreate(0,0,3,0,0,mask,0,0,0,0,0,0,down,down,0);
        fingers[phase] = fingerCreate(0,0,0,2,mask,nx,ny,0,0,0,down,down,0);
        if (!hands[phase] || !fingers[phase]) {
            for (int i = 0; i < 2; i++) {
                if (hands[i]) release(hands[i]);
                if (fingers[i]) release(fingers[i]);
            }
            release(client);
            return failure("event_allocation", 6);
        }
        setInteger(hands[phase], (11U << 16) + 25, 1);
        setFloat(fingers[phase], (11U << 16) + 20, down ? radius : 0);
        setFloat(fingers[phase], (11U << 16) + 21, down ? radius : 0);
        append(hands[phase], fingers[phase], 0);
        setSender(hands[phase], 0x8000000817319372ULL);
    }
    U64 now = mach_absolute_time();
    setTime(hands[0], now); setTime(fingers[0], now);
    dispatch(client, hands[0]);
    int slept = usleep((unsigned int)(hold * 1000)) == 0;
    now = mach_absolute_time();
    setTime(hands[1], now); setTime(fingers[1], now);
    dispatch(client, hands[1]);
    for (int i = 0; i < 2; i++) { release(hands[i]); release(fingers[i]); }
    release(client);
    printf("{\"protocol\":1,\"status\":\"dispatched\",\"dispatch_count\":2,"
           "\"release_dispatched\":true,\"normalized_x\":%.17g,\"normalized_y\":%.17g,"
           "\"normalized_radius\":%.17g,\"requested_hold_ms\":%.17g,\"hold_completed\":%s,"
           "\"page_result_verified\":false}\n", nx, ny, radius, hold, slept ? "true" : "false");
    return slept ? 0 : 7;
}
