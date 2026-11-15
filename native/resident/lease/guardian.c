#define TTT_FOCUS_GUARD_WAIT_MS 1900000
#include "focus_guard.h"
extern void *dlopen(const char *,int),*dlsym(void *,const char *);
extern unsigned getuid(void),geteuid(void);
extern int setuid(unsigned),setgid(unsigned);
int main(void) {
 if(!focus_guard_reset_signals())return 2;
 if(geteuid()==0 && (setgid(501)||setuid(501)))return 3;
 if(getuid()!=501 || geteuid()!=501)return 4;
 void *lib=dlopen("/usr/lib/libAccessibility.dylib",2);if(!lib)return 5;
 unsigned char(*app)(void)=dlsym(lib,"_AXSApplicationAccessibilityEnabled"),(*automation)(void)=dlsym(lib,"_AXSAutomationEnabled");
 void(*setApp)(unsigned char)=dlsym(lib,"_AXSApplicationAccessibilitySetEnabled"),(*setAuto)(unsigned char)=dlsym(lib,"_AXSSetAutomationEnabled");
 if(!app||!automation||!setApp||!setAuto)return 6;
 return focus_guard_run("/private/var/mobile/Library/ttt-accessibility-lease.lock",app,automation,setApp,setAuto);
}
