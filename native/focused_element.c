/* SPDX-License-Identifier: MIT
 * Read one native first responder through AXRuntime attribute 3002.
 * The mapping was verified from AXElement.firstResponder on iOS 26.6.2.
 * This process sends no UI input and never executes in a page.
 */
#include "focus_guard.h"
#include "focus_deadline.h"
#include "address_field.h"
#include "focus_resolution.h"
#include "focus_attributes.h"

typedef unsigned long Size;typedef unsigned char Boolean;typedef const void *CF;
typedef struct {double x,y,w,h;} Rect;
extern void *dlopen(const char *,int),*dlsym(void *,const char *);
extern int printf(const char *,...),fflush(void *),strcmp(const char *,const char *);extern unsigned alarm(unsigned);
extern unsigned getuid(void),geteuid(void),getgid(void),getegid(void);
extern int setuid(unsigned),setgid(unsigned),setgroups(int,const unsigned *);
static int (*copyAttr)(CF,CF,CF *);static void(*release)(CF);static Size(*typeID)(CF),(*stringType)(void),(*numberType)(void),(*valueType)(void);
static Boolean(*stringUTF8)(CF,char *,long,unsigned),(*numberValue)(CF,int,void *),(*axValue)(CF,int,void *);
static int valid_pid(const char *s) {
 unsigned n=0;if(!*s)return 0;
 for(;*s;s++){if(*s<'0'||*s>'9'||n>214748364U||(n==214748364U&&*s>'7'))return 0;n=n*10+(*s-'0');}
 return n>1?(int)n:0;
}
static void quoted(const char*s){printf("\"");for(;*s;s++){unsigned char c=*s;if(c=='"'||c=='\\')printf("\\%c",c);else if(c<32)printf("\\u%04x",c);else printf("%c",c);}printf("\"");}
static void attr_value(int err,CF v){printf("{\"error\":%d,\"value\":",err);if(!v)printf("null");else if(typeID(v)==stringType()){char b[8192]={0};if(stringUTF8(v,b,sizeof(b),0x08000100))quoted(b);else printf("null");}else if(typeID(v)==numberType()){long long n=0;if(numberValue(v,4,&n))printf("%lld",n);else printf("null");}else if(typeID(v)==valueType()){Rect r={0};if(axValue(v,3,&r)&&__builtin_isfinite(r.x)&&__builtin_isfinite(r.y)&&__builtin_isfinite(r.w)&&__builtin_isfinite(r.h))printf("{\"x\":%.6f,\"y\":%.6f,\"width\":%.6f,\"height\":%.6f}",r.x,r.y,r.w,r.h);else printf("null");}else printf("null");printf("}");}
#include "focus_guard_trace.h"
extern int nanosleep(const struct timespec *, struct timespec *);
struct ResolutionContext { CF root; int pid; struct AddressFieldAPI *finder; int (*timeout)(CF,float); };
static int resolve_focused(void *opaque, const void **found) {
 struct ResolutionContext *c=opaque;int error=c->timeout(c->root,0.25f);
 return error?error:copyAttr(c->root,(CF)3002,found);
}
static int resolve_address(void *opaque, const void **found, struct AddressFieldScan *scan) {
 struct ResolutionContext *c=opaque;return address_field_find(c->finder,c->root,c->pid,found,scan);
}
static int resolve_settle(void *opaque) {
 (void)opaque;struct timespec delay={0,400000000};return nanosleep(&delay,0);
}
static void print_resolution(const struct FocusResolution *r) {
 printf("{\"source\":%d,\"first_error\":%d,\"first_present\":%s,\"first_seconds\":%.6f,\"total_seconds\":%.6f,\"attempts\":[",r->source,r->first_error,r->first_present?"true":"false",r->first_seconds,r->total_seconds);
 for(int i=0;i<r->attempts;i++){const struct FocusResolutionAttempt *a=&r->address[i];if(i)printf(",");printf("{\"error\":%d,\"seconds\":%.6f,\"complete\":%s,\"elements\":%ld,\"visited\":%ld,\"matches\":%ld,\"foreign\":%ld}",a->error,a->seconds,a->scan.complete?"true":"false",a->scan.elements,a->scan.visited,a->scan.matches,a->scan.foreign);}
 printf("]}");
}
int main(int argc,char **argv){
 if(!focus_guard_reset_signals())return 13;
 int guard_mode=argc==2&&!strcmp(argv[1],"guard");
 if(!guard_mode)focus_deadline_setup();
 signal(SIGPIPE,SIG_IGN);
 int check=argc==2&&!strcmp(argv[1],"check");
 int address_mode=argc==3&&!strcmp(argv[1],"address");
 int resolve_mode=argc==3&&!strcmp(argv[1],"resolve");
 int pid=argc==3&&(!strcmp(argv[1],"pid")||address_mode||resolve_mode)?valid_pid(argv[2]):0;
 if(!check&&!pid&&!guard_mode)return 2;
 struct FocusGuard guard={0,-1,-1};
 /* Spawn while still privileged: the immutable executable is root-owned.
  * Both processes drop to mobile before loading preference frameworks. */
 if(pid&&!focus_guard_spawn(argv[0],&guard))return 8;
 /* Safari and its accessibility preferences belong to the mobile user. Switch
  * before loading the frameworks so this short-lived query reads and restores
  * that user's state. Do not retain administrative supplementary groups.
  */
 if(geteuid()==0&&(setgroups(0,0)||setgid(501)||setuid(501)))return 6;
 if(getuid()!=501||geteuid()!=501||getgid()!=501||getegid()!=501)return 7;
 void*lib=dlopen("/usr/lib/libAccessibility.dylib",2);if(!lib)return 4;
 Boolean(*app)(void)=dlsym(lib,"_AXSApplicationAccessibilityEnabled"),(*automation)(void)=dlsym(lib,"_AXSAutomationEnabled");void(*setApp)(Boolean)=dlsym(lib,"_AXSApplicationAccessibilitySetEnabled"),(*setAuto)(Boolean)=dlsym(lib,"_AXSSetAutomationEnabled");
 if(!app||!automation||!setApp||!setAuto)return 5;
 if(guard_mode)return focus_guard_run("/private/var/mobile/Library/ttt-native-focus.lock",app,automation,setApp,setAuto);
 void*cf=dlopen("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",2),*ax=dlopen("/System/Library/PrivateFrameworks/AXRuntime.framework/AXRuntime",2);if(!cf||!ax)return 4;
 CF(*create)(int)=dlsym(ax,"_AXUIElementCreateAppElementWithPid");int(*timeout)(CF,float)=dlsym(ax,"AXUIElementSetMessagingTimeout"),(*getPid)(CF,int*)=dlsym(ax,"AXUIElementGetPid");Size(*elementType)(void)=dlsym(ax,"AXUIElementGetTypeID");
#define LOAD(V,L,N) V=(__typeof__(V))dlsym(L,N)
 LOAD(copyAttr,ax,"AXUIElementCopyAttributeValue");LOAD(release,cf,"CFRelease");LOAD(typeID,cf,"CFGetTypeID");LOAD(stringType,cf,"CFStringGetTypeID");LOAD(numberType,cf,"CFNumberGetTypeID");LOAD(valueType,ax,"AXValueGetTypeID");LOAD(stringUTF8,cf,"CFStringGetCString");LOAD(numberValue,cf,"CFNumberGetValue");LOAD(axValue,ax,"AXValueGetValue");
 void(*client)(unsigned)=dlsym(ax,"__AXSetRequestingClient");unsigned long long(*override)(unsigned long long)=dlsym(ax,"_AXOverrideRequestingClientType");
 if(!create||!timeout||!getPid||!elementType||!copyAttr||!release||!typeID||!stringType||!numberType||!valueType||!stringUTF8||!numberValue||!axValue||!app||!automation||!setApp||!setAuto)return 5;
 struct FocusAttributeAPI batch={0};
 LOAD(batch.number,cf,"CFNumberCreate");LOAD(batch.array,cf,"CFArrayCreate");
 LOAD(batch.array_type,cf,"CFArrayGetTypeID");LOAD(batch.null_type,cf,"CFNullGetTypeID");
 LOAD(batch.count,cf,"CFArrayGetCount");LOAD(batch.at,cf,"CFArrayGetValueAtIndex");
 LOAD(batch.copy,ax,"AXUIElementCopyMultipleAttributeValues");
 batch.type=typeID;batch.value_type=valueType;batch.read_value=axValue;batch.release=release;
 if(!batch.number||!batch.array||!batch.array_type||!batch.null_type||!batch.count||!batch.at||!batch.copy)return 5;
 struct AddressFieldAPI finder={0};struct AddressFieldScan scan={0};unsigned long long disabled_trait=0;
 if(address_mode||resolve_mode||check){
  finder.copy=copyAttr;finder.owner=getPid;finder.timeout=timeout;finder.type=typeID;finder.element_type=elementType;finder.string_type=stringType;finder.release=release;finder.string=stringUTF8;finder.now=focus_guard_now;
  LOAD(finder.array_type,cf,"CFArrayGetTypeID");LOAD(finder.count,cf,"CFArrayGetCount");LOAD(finder.at,cf,"CFArrayGetValueAtIndex");LOAD(finder.retain,cf,"CFRetain");
  void *ui=dlopen("/System/Library/Frameworks/UIKit.framework/UIKit",2);
  const unsigned long long *disabled=ui?dlsym(ui,"UIAccessibilityTraitNotEnabled"):0;
  if(!finder.array_type||!finder.count||!finder.at||!finder.retain||!disabled||!*disabled)return 5;
  disabled_trait=*disabled;
 }
 if(check){printf("{\"protocol\":1,\"status\":\"ready\",\"ui_input_sent\":false,\"ax_queried\":false,\"address_resolution\":\"guarded-v1\",\"query_timing\":\"phased-v1\"}\n");fflush(0);return 0;}
 struct FocusGuardState initial={0},restored={0};
 if(!focus_guard_begin(&guard,&initial))return 9;
 focus_deadline_query();
 Boolean beforeApp=initial.before_app,beforeAuto=initial.before_auto;
 if(client)client(2);if(override)override(2);
 CF root=create(pid),focused=0;int e=-1;struct FocusResolution resolution={0};
 if(root){
  if(resolve_mode){
   int owner=0;e=getPid(root,&owner);if(!e&&owner!=pid)e=-2;
   if(!e){struct ResolutionContext context={root,pid,&finder,timeout};struct FocusResolutionAPI api={resolve_focused,resolve_address,release,focus_guard_now,resolve_settle};e=focus_resolve(&api,&context,&focused,&scan,&resolution);}
  }else if(address_mode)e=address_field_find(&finder,root,pid,&focused,&scan);
  else{e=timeout(root,0.25f);if(!e)e=copyAttr(root,(CF)3002,&focused);}
 }
 if(focused&&typeID(focused)!=elementType())e=-2;
 printf("{\"protocol\":1,\"uid\":501,\"requested_pid\":%d,\"error\":%d,\"before\":{\"application\":%d,\"automation\":%d}",pid,e,beforeApp,beforeAuto);
 if(address_mode)printf(",\"lookup\":\"address\",\"disabled_trait\":%llu,\"scan\":{\"complete\":%s,\"elements\":%ld,\"visited\":%ld,\"matches\":%ld,\"foreign\":%ld}",disabled_trait,scan.complete?"true":"false",scan.elements,scan.visited,scan.matches,scan.foreign);
 if(resolve_mode){printf(",\"lookup\":\"resolve\",\"disabled_trait\":%llu,\"scan\":{\"complete\":%s,\"elements\":%ld,\"visited\":%ld,\"matches\":%ld,\"foreign\":%ld},\"resolution\":",disabled_trait,scan.complete?"true":"false",scan.elements,scan.visited,scan.matches,scan.foreign);print_resolution(&resolution);}
 printf(",\"element\":");
 if(focused&&!e&&typeID(focused)==elementType()){
  int owner=0,pe=getPid(focused,&owner),te=timeout(focused,0.25f);printf("{\"pid\":%d,\"pid_error\":%d,\"timeout_error\":%d,\"attributes\":{",owner,pe,te);
  struct FocusAttributeResult attributes={0};double started=focus_guard_now();focus_attributes_read(&batch,focused,&attributes);double seconds=focus_guard_now()-started;
 const char*names[]={"label","value","identifier","traits","frame"};
 for(unsigned i=0;i<5;i++){CF v=0;int error=focus_attribute_value(&batch,&attributes,i,&v);if(i)printf(",");quoted(names[i]);printf(":");attr_value(error,v);}
 printf("},\"attribute_batch\":{\"error\":%d,\"count\":5,\"seconds\":%.6f}}",attributes.error,seconds);
 if(attributes.values)release(attributes.values);
 }else printf("null");
 if(focused)release(focused);if(root)release(root);
 focus_deadline_restore();
 int complete=focus_guard_finish(&guard,&restored);
 complete=complete&&restored.before_app==beforeApp&&restored.before_auto==beforeAuto
   &&restored.after_app==beforeApp&&restored.after_auto==beforeAuto;
 printf(",\"after\":{\"application\":%d,\"automation\":%d},\"guard_restored\":%s,\"guard_trace\":",restored.after_app,restored.after_auto,complete?"true":"false");print_guard_trace();printf("}\n");
 fflush(0);return complete?0:12;
}
