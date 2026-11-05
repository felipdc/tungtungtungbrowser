/* Exercise unsupported native states without calling host accessibility APIs. */
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#define main native_main
#define dlopen fake_dlopen
#define dlsym fake_dlsym
#define printf fake_printf
#define fflush fake_fflush
#define getuid fake_getuid
#define geteuid fake_geteuid
#define getgid fake_getgid
#define setuid fake_setuid
#define setgid fake_setgid
#define sysctlbyname fake_sysctlbyname
#define alarm fake_alarm
#include "accessibility_state.c"
#undef main
#undef printf
#undef fflush

static int uid_value, gid_value, drop_error, boot_error, missing_library, missing_symbol;
static unsigned char application_value, automation_value;
static unsigned reads, symbols, alarms;
static char output[512];
static unsigned char application_get(void) { reads++; return application_value; }
static unsigned char automation_get(void) { reads++; return automation_value; }

void *fake_dlopen(const char *name, int flags) {
    assert(strcmp(name, "/usr/lib/libAccessibility.dylib") == 0 && flags == 2);
    return missing_library ? NULL : &reads;
}
void *fake_dlsym(void *library, const char *name) {
    assert(library == &reads); symbols++;
    /* Any attempt to bind a setter, query a tree or use another API fails. */
    if (strcmp(name, "_AXSApplicationAccessibilityEnabled") == 0)
        return missing_symbol == 1 ? NULL : (void *)application_get;
    assert(strcmp(name, "_AXSAutomationEnabled") == 0);
    return missing_symbol == 2 ? NULL : (void *)automation_get;
}
int fake_getuid(void) { return uid_value; }
int fake_geteuid(void) { return uid_value; }
int fake_getgid(void) { return gid_value; }
int fake_setuid(unsigned value) { assert(value == 501); if (drop_error) return -1; uid_value = value; return 0; }
int fake_setgid(unsigned value) { assert(value == 501); if (drop_error) return -1; gid_value = value; return 0; }
unsigned fake_alarm(unsigned seconds) { assert(seconds == 15); alarms++; return 0; }
int fake_fflush(void *stream) { assert(stream == NULL); return 0; }
int fake_sysctlbyname(const char *name, void *buffer, Size *size, void *new_value, Size new_size) {
    assert(strcmp(name, "kern.bootsessionuuid") == 0 && *size == 64 && !new_value && !new_size);
    if (boot_error) return -1;
    memcpy(buffer, "12345678-1234-1234-1234-123456789ABC", 37); *size = 37; return 0;
}
int fake_printf(const char *format, ...) {
    va_list args; va_start(args, format);
    int n = vsnprintf(output, sizeof(output), format, args); va_end(args);
    assert(n > 0 && (size_t)n < sizeof(output)); return n;
}
static void reset(void) {
    uid_value = gid_value = 0; drop_error = boot_error = missing_library = missing_symbol = 0;
    application_value = automation_value = 0; reads = symbols = alarms = 0; output[0] = 0;
}
int main(void) {
    for (unsigned app = 0; app < 2; app++) for (unsigned automation = 0; automation < 2; automation++) {
        reset(); application_value = app; automation_value = automation;
        assert(native_main() == 0 && reads == 2 && symbols == 2 && alarms == 1);
        assert(uid_value == 501 && gid_value == 501);
        assert(strstr(output, app ? "\"application_accessibility\":true" : "\"application_accessibility\":false"));
        assert(strstr(output, automation ? "\"automation\":true" : "\"automation\":false"));
        assert(strstr(output, "\"settings_written\":false,\"ax_tree_queried\":false"));
        puts(output);
    }
    reset(); uid_value = 502; assert(native_main() == 4 && !reads && !output[0]);
    reset(); drop_error = 1; assert(native_main() == 3 && !reads && !output[0]);
    reset(); boot_error = 1; assert(native_main() == 5 && !reads && !output[0]);
    reset(); missing_library = 1; assert(native_main() == 6 && !reads && !output[0]);
    for (int missing = 1; missing <= 2; missing++) {
        reset(); missing_symbol = missing; assert(native_main() == 7 && !reads && !output[0]);
    }
    reset(); application_value = 2; assert(native_main() == 8 && !output[0]);
    reset(); automation_value = 2; assert(native_main() == 8 && !output[0]);
    return 0;
}
