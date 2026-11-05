/* SPDX-License-Identifier: MIT
 * Read one native first responder through AXRuntime attribute 3002.
 * The mapping was verified from AXElement.firstResponder on iOS 26.6.2.
 * This process sends no UI input and never executes in a page.
 */
typedef unsigned long Size;typedef unsigned char Boolean;typedef const void *CF;
typedef struct {double x,y,w,h;} Rect;
extern void *dlopen(const char *,int),*dlsym(void *,const char *);
extern int printf(const char *,...),fflush(void *),strcmp(const char *,const char *);extern unsigned alarm(unsigned);
static int (*copyAttr)(CF,CF,CF *);static void(*release)(CF);static Size(*typeID)(CF),(*stringType)(void),(*numberType)(void),(*valueType)(void);
static Boolean(*stringUTF8)(CF,char *,long,unsigned),(*numberValue)(CF,int,void *),(*axValue)(CF,int,void *);
static int valid_pid(const char *s) {
 unsigned n=0;if(!*s)return 0;
 for(;*s;s++){if(*s<'0'||*s>'9'||n>214748364U||(n==214748364U&&*s>'7'))return 0;n=n*10+(*s-'0');}
 return n>1?(int)n:0;
}
static void quoted(const char*s){printf("\"");for(;*s;s++){unsigned char c=*s;if(c=='"'||c=='\\')printf("\\%c",c);else if(c<32)printf("\\u%04x",c);else printf("%c",c);}printf("\"");}
static void attr(CF e,unsigned key){CF v=0;int err=copyAttr(e,(CF)(Size)key,&v);printf("{\"error\":%d,\"value\":",err);if(!v)printf("null");else if(typeID(v)==stringType()){char b[8192]={0};if(stringUTF8(v,b,sizeof(b),0x08000100))quoted(b);else printf("null");}else if(typeID(v)==numberType()){long long n=0;if(numberValue(v,4,&n))printf("%lld",n);else printf("null");}else if(typeID(v)==valueType()){Rect r={0};if(axValue(v,3,&r)&&__builtin_isfinite(r.x)&&__builtin_isfinite(r.y)&&__builtin_isfinite(r.w)&&__builtin_isfinite(r.h))printf("{\"x\":%.6f,\"y\":%.6f,\"width\":%.6f,\"height\":%.6f}",r.x,r.y,r.w,r.h);else printf("null");}else printf("null");printf("}");if(v)release(v);}
int main(int argc,char **argv){
 alarm(20);
 int check=argc==2&&!strcmp(argv[1],"check");
 int pid=argc==3&&!strcmp(argv[1],"pid")?valid_pid(argv[2]):0;
 if(!check&&!pid)return 2;
 void*cf=dlopen("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",2),*ax=dlopen("/System/Library/PrivateFrameworks/AXRuntime.framework/AXRuntime",2),*lib=dlopen("/usr/lib/libAccessibility.dylib",2);if(!cf||!ax||!lib)return 4;
 CF(*create)(int)=dlsym(ax,"_AXUIElementCreateAppElementWithPid");int(*timeout)(CF,float)=dlsym(ax,"AXUIElementSetMessagingTimeout"),(*getPid)(CF,int*)=dlsym(ax,"AXUIElementGetPid");Size(*elementType)(void)=dlsym(ax,"AXUIElementGetTypeID");
#define LOAD(V,L,N) V=(__typeof__(V))dlsym(L,N)
 LOAD(copyAttr,ax,"AXUIElementCopyAttributeValue");LOAD(release,cf,"CFRelease");LOAD(typeID,cf,"CFGetTypeID");LOAD(stringType,cf,"CFStringGetTypeID");LOAD(numberType,cf,"CFNumberGetTypeID");LOAD(valueType,ax,"AXValueGetTypeID");LOAD(stringUTF8,cf,"CFStringGetCString");LOAD(numberValue,cf,"CFNumberGetValue");LOAD(axValue,ax,"AXValueGetValue");
 Boolean(*app)(void)=dlsym(lib,"_AXSApplicationAccessibilityEnabled"),(*automation)(void)=dlsym(lib,"_AXSAutomationEnabled");void(*setApp)(Boolean)=dlsym(lib,"_AXSApplicationAccessibilitySetEnabled"),(*setAuto)(Boolean)=dlsym(lib,"_AXSSetAutomationEnabled");void(*client)(unsigned)=dlsym(ax,"__AXSetRequestingClient");unsigned long long(*override)(unsigned long long)=dlsym(ax,"_AXOverrideRequestingClientType");
 if(!create||!timeout||!getPid||!elementType||!copyAttr||!release||!typeID||!stringType||!numberType||!valueType||!stringUTF8||!numberValue||!axValue||!app||!automation||!setApp||!setAuto)return 5;
 if(check){printf("{\"protocol\":1,\"status\":\"ready\",\"ui_input_sent\":false,\"ax_queried\":false}\n");fflush(0);return 0;}
 Boolean beforeApp=app(),beforeAuto=automation();if(!beforeApp)setApp(1);if(!beforeAuto)setAuto(1);if(client)client(2);if(override)override(2);
 CF root=create(pid),focused=0;int e=-1;if(root){e=timeout(root,0.25f);if(!e)e=copyAttr(root,(CF)3002,&focused);}
 if(focused&&typeID(focused)!=elementType())e=-2;
 printf("{\"protocol\":1,\"requested_pid\":%d,\"error\":%d,\"before\":{\"application\":%d,\"automation\":%d},\"element\":",pid,e,beforeApp,beforeAuto);
 if(focused&&!e&&typeID(focused)==elementType()){
  int owner=0,pe=getPid(focused,&owner),te=timeout(focused,0.25f);printf("{\"pid\":%d,\"pid_error\":%d,\"timeout_error\":%d,\"attributes\":{",owner,pe,te);
  unsigned keys[]={2001,2006,5019,2004,2003};const char*names[]={"label","value","identifier","traits","frame"};for(unsigned i=0;i<5;i++){if(i)printf(",");quoted(names[i]);printf(":");attr(focused,keys[i]);}printf("}}");
 }else printf("null");
 if(focused)release(focused);if(root)release(root);if(!beforeAuto)setAuto(0);if(!beforeApp)setApp(0);printf(",\"after\":{\"application\":%d,\"automation\":%d}}\n",app(),automation());fflush(0);return 0;
}
