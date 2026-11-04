/* Host-side failure injection for touch_helper.c. No real HID calls. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#define main helper_main
#define dlopen fake_dlopen
#define dlsym fake_dlsym
#define usleep fake_usleep
#define mach_absolute_time fake_time
#include "touch_helper.c"
#undef main

typedef struct Event { double x, y, major, minor; U64 time, sender; int touch; struct Event *child; } Event;
static Event events[4], client;
static int allocations, releases, sent, fail_at, interrupted, missing_symbol;
static U64 previous_time;
static Ref allocate(void) {
    allocations++;
    return allocations == fail_at ? 0 : &events[allocations - 1];
}
static Ref make_client(Ref allocator) { (void)allocator; return &client; }
static Ref make_hand(Ref a,U64 t,U32 type,U32 index,U32 identity,U32 mask,U32 buttons,
                     double x,double y,double z,double pressure,double twist,Bool range,Bool touch,U32 options) {
    (void)a;(void)t;(void)index;(void)identity;(void)buttons;(void)x;(void)y;(void)z;
    (void)pressure;(void)twist;(void)options;
    assert(type == 3 && mask == (touch ? 3U : 2U) && range == touch);
    Event *e = allocate(); if (e) e->touch = touch; return e;
}
static Ref make_finger(Ref a,U64 t,U32 index,U32 identity,U32 mask,double x,double y,double z,
                       double pressure,double twist,Bool range,Bool touch,U32 options) {
    (void)a;(void)t;(void)index;(void)z;(void)twist;(void)options;
    assert(identity == 2 && mask == (touch ? 3U : 2U) && range == touch && pressure == 0);
    Event *e = allocate(); if (e) { e->x = x; e->y = y; e->touch = touch; } return e;
}
static void integer(Ref e,U32 field,long value) { (void)e; assert(field == (11U<<16)+25 && value == 1); }
static void floating(Ref e,U32 field,double value) {
    if (field == (11U<<16)+20) ((Event *)e)->major = value;
    else { assert(field == (11U<<16)+21); ((Event *)e)->minor = value; }
}
static void timestamp(Ref e,U64 value) { ((Event *)e)->time = value; }
static void sender(Ref e,U64 value) { ((Event *)e)->sender = value; }
static void append(Ref e,Ref child,U32 options) { assert(options == 0); ((Event *)e)->child = child; }
static void dispatch(Ref c,Ref event) {
    Event *e = event, *f = e->child;
    assert(c == &client && allocations == 4 && f && sent < 2);
    assert(e->time > previous_time && f->time == e->time);
    assert(e->sender == 0x8000000817319372ULL && e->touch == (sent == 0) && f->touch == e->touch);
    assert(f->x == 0.25 && f->y == 0.5 && f->major == (sent ? 0 : 0.01) && f->minor == f->major);
    previous_time = e->time; sent++;
}
static void release(Ref e) { assert(e); releases++; }
void *fake_dlopen(const char *path,int flags) { (void)path; assert(flags == 2); return &client; }
void *fake_dlsym(void *library,const char *symbol) {
    (void)library;
    if (missing_symbol && equal(symbol, "IOHIDEventSetTimeStamp")) return 0;
#define SYMBOL(name, function) if (equal(symbol, name)) return (void *)function
    SYMBOL("IOHIDEventSystemClientCreate", make_client);
    SYMBOL("IOHIDEventCreateDigitizerEvent", make_hand);
    SYMBOL("IOHIDEventCreateDigitizerFingerEvent", make_finger);
    SYMBOL("IOHIDEventSetIntegerValue", integer);
    SYMBOL("IOHIDEventSetFloatValue", floating);
    SYMBOL("IOHIDEventSetTimeStamp", timestamp);
    SYMBOL("IOHIDEventSetSenderID", sender);
    SYMBOL("IOHIDEventAppendEvent", append);
    SYMBOL("IOHIDEventSystemClientDispatchEvent", dispatch);
    SYMBOL("CFRelease", release);
#undef SYMBOL
    assert(0); return 0;
}
int fake_usleep(unsigned int microseconds) { assert(sent == 1 && microseconds == 50000); return interrupted ? -1 : 0; }
U64 fake_time(void) { return previous_time + 1000; }
static void reset(void) {
    memset(events,0,sizeof(events)); allocations = releases = sent = fail_at = interrupted = missing_symbol = 0; previous_time = 0;
}
int main(void) {
    char *tap[] = {"helper", "tap", "0.25", "0.5", "0.01", "50"};
    reset(); assert(helper_main(6,tap) == 0 && sent == 2 && releases == 5);
    reset(); interrupted = 1; assert(helper_main(6,tap) == 7 && sent == 2 && releases == 5);
    for (int i = 1; i <= 4; i++) {
        reset(); fail_at = i; assert(helper_main(6,tap) == 6 && sent == 0);
        assert(releases == allocations); /* every successful allocation plus client */
    }
    reset(); missing_symbol = 1; assert(helper_main(6,tap) == 4 && sent == 0 && allocations == 0);
    char *check[] = {"helper", "check"};
    reset(); assert(helper_main(2,check) == 0 && sent == 0 && allocations == 0 && releases == 1);
    const char *invalid[] = {"", "nan", "inf", "0.5suffix", "-1", "1.1", " 0.2"};
    for (unsigned i = 0; i < sizeof(invalid)/sizeof(invalid[0]); i++) {
        reset(); tap[2] = (char *)invalid[i]; assert(helper_main(6,tap) == 2 && allocations == 0 && sent == 0);
    }
    puts("Native helper host checks passed.");
    return 0;
}
