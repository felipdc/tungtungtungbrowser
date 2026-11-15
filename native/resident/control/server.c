#include "focus_guard.h"
extern unsigned alarm(unsigned);
/* Native Safari focal-state observer. No page code.
 * RunningBoard selectors follow pinned icli 2fbdb5ee61d8c320732deb9768b22506f5e3d411.
 * Inspect only the executable whose path ends in /MobileSafari.app/MobileSafari.
 * A focal assertion is an observation, not proof of displayed page content. */
typedef void *Ref;
typedef unsigned long Size;
typedef unsigned long long U64;
typedef unsigned int U32;
typedef unsigned char Bool;
extern void *dlopen(const char *, int), *dlsym(void *, const char *);
extern int printf(const char *, ...), fflush(void *);
extern int vsnprintf(char *,Size,const char *,__builtin_va_list),getpid(void);
extern long read(int,void *,Size),write(int,const void *,Size);
extern int close(int),socket(int,int,int),bind(int,const void *,U32),listen(int,int),accept(int,void *,U32 *),setsockopt(int,int,int,const void *,U32);
static char output[65536];static Size used;static int output_fd=1,output_failed;
static int emit(const char *format,...) {
 if(output_failed)return -1;__builtin_va_list ap;__builtin_va_start(ap,format);int n=vsnprintf(output+used,sizeof(output)-used,format,ap);__builtin_va_end(ap);
 if(n<0||(Size)n>=sizeof(output)-used){output_failed=1;return -1;}used+=(Size)n;return n;
}
static int flush_output(void *unused) {
 (void)unused;if(output_failed)return -1;Size done=0;
 while(done<used){long n=write(output_fd,output+done,used-done);if(n<=0){output_failed=1;return -1;}done+=(Size)n;}used=0;return 0;
}
#define printf emit
#define fflush flush_output

static Ref (*class_named)(const char *), (*selector)(const char *), (*pool_push)(void);
static void (*pool_pop)(Ref), (*release)(Ref);
static Ref msg;
static Bool (*get_string)(Ref, char *, long, U32);
static Ref (*make_string)(Ref,const char *,U32);
static int (*list_pids)(U32,U32,void *,int), (*pid_path)(int,void *,U32);
static int (*sysctl_named)(const char *,void *,Size *,const void *,Size);
static int (*notify_register)(const char *,int *), (*notify_state)(int,U64 *), (*notify_cancel)(int);
struct Usage { unsigned char uuid[16]; U64 user,system,idle,interrupt,pageins,wired,resident,footprint,start,exit; };
static int (*pid_usage)(int,int,void *);
static U64 (*ticks)(void);
struct Timebase { U32 numerator,denominator; };
static int (*timebase)(struct Timebase *);
static struct Timebase tb;
static U64 started;
static char boot[37];
static int equal(const char *a,const char *b) { while(*a && *a==*b){a++;b++;}return *a==*b; }
static Size length(const char *p) { Size n=0;while(p[n])n++;return n; }
static int suffix(const char *p,const char *end) { Size a=length(p),b=length(end);return a>=b && equal(p+a-b,end); }
static int contains(const char *p,const char *part) { for(;*p;p++){const char *a=p,*b=part;while(*b && *a==*b){a++;b++;}if(!*b)return 1;}return 0; }
static Ref call0(Ref o,const char *s) { return ((Ref(*)(Ref,Ref))msg)(o,selector(s)); }
static Ref call1(Ref o,const char *s,Ref a) { return ((Ref(*)(Ref,Ref,Ref))msg)(o,selector(s),a); }
static int responds(Ref o,const char *s) { return o && ((Bool(*)(Ref,Ref,Ref))msg)(o,selector("respondsToSelector:"),selector(s)); }
static int string(Ref o,char *out,Size size) {
 if(!o || !((Bool(*)(Ref,Ref,Ref))msg)(o,selector("isKindOfClass:"),class_named("NSString")))return 0;
 return get_string(o,out,(long)size,0x08000100);
}
static Ref key(Ref o,const char *s) { Ref k=make_string(0,s,0x08000100);if(!k)return 0;Ref r=call1(o,"valueForKey:",k);release(k);return r; }
static double elapsed(void) { return (double)(ticks()-started)*(double)tb.numerator/(double)tb.denominator/1e9; }
static void quote(const char *s) { printf("\"");for(;*s;s++){unsigned char c=(unsigned char)*s;if(c=='"'||c=='\\')printf("\\%c",c);else if(c<32)printf("\\u%04x",c);else printf("%c",c);}printf("\""); }
static int failed(const char *s,int code) { printf("{\"protocol\":1,\"status\":\"failed\",\"stage\":\"%s\"}\n",s);fflush(0);return code; }
static int init(void) {
 void *lib=dlopen("/usr/lib/libSystem.B.dylib",2),*objc=dlopen("/usr/lib/libobjc.A.dylib",2);
 void *cf=dlopen("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",2);
 if(!lib||!objc||!cf||!dlopen("/System/Library/Frameworks/Foundation.framework/Foundation",2))return 0;
#define LOAD(target,handle,name) target=dlsym(handle,name);if(!target)return 0
 LOAD(class_named,objc,"objc_getClass");LOAD(selector,objc,"sel_registerName");LOAD(msg,objc,"objc_msgSend");
 LOAD(pool_push,objc,"objc_autoreleasePoolPush");LOAD(pool_pop,objc,"objc_autoreleasePoolPop");
 LOAD(get_string,cf,"CFStringGetCString");LOAD(make_string,cf,"CFStringCreateWithCString");LOAD(release,cf,"CFRelease");
 LOAD(list_pids,lib,"proc_listpids");LOAD(pid_path,lib,"proc_pidpath");LOAD(pid_usage,lib,"proc_pid_rusage");
 LOAD(sysctl_named,lib,"sysctlbyname");LOAD(notify_register,lib,"notify_register_check");LOAD(notify_state,lib,"notify_get_state");LOAD(notify_cancel,lib,"notify_cancel");
 LOAD(ticks,lib,"mach_absolute_time");LOAD(timebase,lib,"mach_timebase_info");
#undef LOAD
 if(timebase(&tb)||!tb.denominator)return 0;started=ticks();Size size=sizeof(boot);
 return !sysctl_named("kern.bootsessionuuid",boot,&size,0,0)&&size==37&&!boot[36];
}
static int flags(U64 *values) {
 const char *names[]={"com.apple.springboard.finishedstartup","com.apple.springboard.lockstate","com.apple.springboard.hasBlankedScreen"};
 for(int i=0;i<3;i++){int token;if(notify_register(names[i],&token))return 0;int error=notify_state(token,&values[i]);notify_cancel(token);if(error)return 0;}
 return values[0] && values[1]<=1 && values[2]<=1;
}
static int inspect(void){
 static int pids[4096];int bytes=list_pids(1,0,pids,sizeof(pids));
 if(bytes<=0||bytes>=(int)sizeof(pids)||bytes%4)return failed("process_list",4);
 void *framework=dlopen("/System/Library/PrivateFrameworks/RunningBoardServices.framework/RunningBoardServices",2);if(!framework)return failed("runningboard_framework",5);
 Ref identifiers=class_named("RBSProcessIdentifier"),handles=class_named("RBSProcessHandle");
 if(!responds(identifiers,"identifierWithPid:")||!responds(handles,"handleForIdentifier:error:"))return failed("runningboard_selectors",6);
 int focal_count=0,selected=0,candidates=0;char selected_bundle[256]={0};U64 selected_start=0;
 for(int i=0;i<bytes/4;i++){
  int pid=pids[i];if(pid<=1)continue;char path[4096]={0};int pathless=pid_path(pid,path,sizeof(path))<=0;
  if(!pathless){
   const char *app=0;for(const char *p=path;*p;p++)if(p[0]=='.'&&p[1]=='a'&&p[2]=='p'&&p[3]=='p'&&p[4]=='/'){app=p;break;}
   if(!app||contains(app+5,"/")||contains(path,"WidgetRenderer"))continue;
  }
  struct Usage before={0},after={0};if(pid_usage(pid,0,&before)){if(pathless)continue;return failed("candidate_identity_before",7);}
  Ref ident=((Ref(*)(Ref,Ref,int))msg)(identifiers,selector("identifierWithPid:"),pid),error=0;
  Ref handle=((Ref(*)(Ref,Ref,Ref,Ref*))msg)(handles,selector("handleForIdentifier:error:"),ident,&error);
  if(!handle||error){if(pathless)continue;return failed("candidate_handle",8);}
  Ref identity=key(handle,"identity");
  if(pathless&&(!responds(identity,"isApplication")||!((Bool(*)(Ref,Ref))msg)(identity,selector("isApplication"))))continue;
  char bundle[256]={0};if(!string(key(identity,"embeddedApplicationIdentifier"),bundle,sizeof(bundle))||!bundle[0]||contains(bundle,"WidgetRenderer"))continue;
  candidates++;Ref state=key(handle,"currentState");if(!state)return failed("candidate_state",9);
  Ref assertions=key(state,"assertions"),array=0;
  if(responds(assertions,"allObjects"))array=call0(assertions,"allObjects");else if(responds(assertions,"objectAtIndex:"))array=assertions;
  if(!responds(array,"count")||!responds(array,"objectAtIndex:"))return failed("assertions",10);
  Size n=((Size(*)(Ref,Ref))msg)(array,selector("count"));if(n>64)return failed("assertion_limit",11);int focal=0;
  for(Size j=0;j<n;j++){
   Ref a=((Ref(*)(Ref,Ref,Size))msg)(array,selector("objectAtIndex:"),j);char domain[512]={0};if(!string(key(a,"domain"),domain,sizeof(domain)))continue;
   if(contains(domain,"Workspace-ForegroundFocal")||contains(domain,"com.apple.frontboard:SuspendableRole-UIFocal"))focal=1;
  }
  if(pid_usage(pid,0,&after)||before.start!=after.start)return failed("candidate_identity_changed",13);
  if(focal){
   focal_count++;selected=pid;selected_start=before.start;
   Size j=0;for(;bundle[j]&&j<sizeof(selected_bundle)-1;j++)selected_bundle[j]=bundle[j];selected_bundle[j]=0;
  }
 }
 U64 screen[3]={0};int screen_valid=flags(screen);int verified=screen_valid&&!screen[1]&&!screen[2]&&focal_count==1&&equal(selected_bundle,"com.apple.mobilesafari");
 if(verified){struct Usage final={0};if(pid_usage(selected,0,&final)||final.start!=selected_start)return failed("final_identity_changed",14);}
 printf("{\"protocol\":2,\"status\":\"observed\",\"helper_pid\":%d,\"boot_uuid\":\"%s\",\"pid\":%d,\"process_start\":%llu,\"bundle_id\":",getpid(),boot,selected,selected_start);quote(selected_bundle);
 printf(",\"verified\":%s,\"source\":\"runningboard_unique_focal_process\",\"focal_process_count\":%d,\"candidate_count\":%d,\"screen_state_valid\":%s,\"locked\":%llu,\"screen_off\":%llu,\"elapsed_seconds\":%.6f,\"strict_ambiguity\":true}\n",verified?"true":"false",focal_count,candidates,screen_valid?"true":"false",screen[1],screen[2],elapsed());return 0;
}
#include "keyboard.c"
#include "../owned_key.h"
extern int snprintf(char *,Size,const char *,...);
static volatile int stopped;
static void stopping(int sig){(void)sig;stopped=1;}
#include "spawn.h"
struct Address{unsigned char len,family;unsigned short reserved;unsigned port,cid;};
struct Timeval{long seconds;int microseconds;};
static int serve(void){
 int listener=socket(40,1,0);if(listener<0)return 20;
 if(fcntl(listener,F_SETFD,FD_CLOEXEC)){close(listener);return 21;}
 struct Address address={12,40,0,1348,0xffffffffU};if(bind(listener,&address,sizeof(address))||listen(listener,4)){close(listener);return 22;}
 signal(SIGPIPE,SIG_IGN);signal(15,stopping);signal(2,stopping);double end=focus_guard_now()+1800;char expected[96];snprintf(expected,sizeof(expected),"stop %d %s",getpid(),boot);
 char key_prefix[96];snprintf(key_prefix,sizeof(key_prefix),"key %d %s ",getpid(),boot);
 printf("{\"protocol\":2,\"status\":\"listening\",\"pid\":%d,\"boot_uuid\":\"%s\",\"port\":1348}\n",getpid(),boot);fflush(0);
 while(!stopped&&focus_guard_now()<end){
  struct pollfd p={listener,POLLIN,0};if(poll(&p,1,1000)<=0)continue;
  struct Address peer={0};unsigned size=sizeof(peer);int fd=accept(listener,&peer,&size);if(fd<0)continue;
  if(fcntl(fd,F_SETFD,FD_CLOEXEC)||size!=sizeof(peer)||peer.family!=40||peer.cid!=2){close(fd);continue;}
  struct Timeval t={2,0};int yes=1;if(setsockopt(fd,0xffff,0x1022,&yes,sizeof(yes))||setsockopt(fd,0xffff,0x1005,&t,sizeof(t))||setsockopt(fd,0xffff,0x1006,&t,sizeof(t))){close(fd);continue;}
  char input[128]={0};unsigned got=0;int done=0;
  while(got<sizeof(input)-1&&!done){long count=read(fd,input+got,sizeof(input)-1-got);if(count<=0)break;got+=(unsigned)count;for(unsigned i=0;i<got;i++)if(input[i]=='\n'){done=1;break;}}
  if(done&&got&&input[got-1]=='\n')input[got-1]=0;else input[0]=0;
  output_fd=fd;used=0;output_failed=0;started=ticks();Ref pool=pool_push();alarm(20);
  if(equal(input,"health")||equal(input,expected)){
   stopped=equal(input,expected);printf("{\"protocol\":2,\"status\":\"%s\",\"pid\":%d,\"boot_uuid\":\"%s\",\"port\":1348}\n",stopped?"stopping":"ready",getpid(),boot);
  }else if(equal(input,"inspect"))inspect();
  else if(owned_key(input,key_prefix)){
   printf("{\"protocol\":2,\"status\":\"key\",\"helper_pid\":%d,\"boot_uuid\":\"%s\",\"result\":",getpid(),boot);
   char *args[]={"resident-key","press",(char *)owned_key(input,key_prefix),0};int code=keyboard_main(3,args);printf(",\"exit_code\":%d}\n",code);
  }
  else failed("request",23);
  fflush(0);pool_pop(pool);alarm(0);close(fd);output_fd=1;used=0;output_failed=0;
 }
 close(listener);printf("{\"protocol\":2,\"status\":\"closed\",\"pid\":%d,\"boot_uuid\":\"%s\"}\n",getpid(),boot);fflush(0);return 0;
}
int main(int argc,char **argv){
 if(!focus_guard_reset_signals())return 2;alarm(60);if(!init())return 3;alarm(0);
 if(argc==4&&equal(argv[1],"spawn"))return spawn_owned(argv[0],argv[2],argv[3]);
 if(argc==3&&equal(argv[1],"serve")){
  char *args[]={argv[0],"check",0};int code=keyboard_main(2,args);used=0;output_failed=0;if(code)return 24;return serve();
 }return 2;
}
