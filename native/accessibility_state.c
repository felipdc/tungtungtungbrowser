/* Read the pinned native accessibility switches; never resolve or call setters. */
typedef unsigned long Size;
typedef unsigned char Boolean;
extern void *dlopen(const char *, int), *dlsym(void *, const char *);
extern int printf(const char *, ...), fflush(void *), getuid(void), geteuid(void), getgid(void);
extern int setuid(unsigned), setgid(unsigned), sysctlbyname(const char *, void *, Size *, void *, Size);
extern unsigned alarm(unsigned);

int main(void) {
    alarm(15);
    if (geteuid() == 0 && (setgid(501) || setuid(501))) return 3;
    if (getuid() != 501 || geteuid() != 501) return 4;
    char boot[64] = {0}; Size n = sizeof(boot);
    if (sysctlbyname("kern.bootsessionuuid", boot, &n, 0, 0) || n < 36 || n > sizeof(boot)) return 5;
    void *library = dlopen("/usr/lib/libAccessibility.dylib", 2);
    if (!library) return 6;
    Boolean (*application)(void) = dlsym(library, "_AXSApplicationAccessibilityEnabled");
    Boolean (*automation)(void) = dlsym(library, "_AXSAutomationEnabled");
    if (!application || !automation) return 7;
    Boolean app = application(), auto_value = automation();
    if (app > 1 || auto_value > 1) return 8;
    printf("{\"protocol\":1,\"boot_uuid\":\"%.36s\",\"uid\":%d,\"gid\":%d,\"application_accessibility\":%s,\"automation\":%s,\"settings_written\":false,\"ax_tree_queried\":false}\n", boot, getuid(), getgid(), app ? "true" : "false", auto_value ? "true" : "false");
    fflush(0);
    return 0;
}
