/* Read-only process identity observer. Darwin ABI follows Apple XNU proc_info.h; see docs/process-observer.md. No Foundation,
 * RunningBoard, browser APIs, process signaling or UI operations. */
typedef unsigned long Size;
typedef unsigned long long U64;
typedef unsigned int U32;
struct BSDInfo {
 U32 flags,status,xstatus,pid,ppid,uid,gid,ruid,rgid,svuid,svgid,reserved;
 char comm[16],name[32];
 U32 nfiles,pgid,pjobc,tdev,tpgid;int nice;
 U64 start_sec,start_usec;
};
_Static_assert(sizeof(struct BSDInfo)==136,"proc_bsdinfo ABI size");
_Static_assert(__builtin_offsetof(struct BSDInfo,start_sec)==120,"proc_bsdinfo start offset");
_Static_assert(sizeof(U32)==4 && sizeof(U64)==8 && sizeof(Size)==8,"Darwin arm64 widths");
static int (*pid_info)(int,int,U64,void *,int),(*pid_path)(int,void *,U32),*(*errno_address)(void);
static int equal(const char *a,const char *b){while(*a && *a==*b){a++;b++;}return *a==*b;}
static int valid_pid(const char *s){U32 n=0;if(!*s)return 0;for(;*s;s++){if(*s<'0'||*s>'9'||n>214748364U||(n==214748364U&&*s>'7'))return 0;n=n*10+(*s-'0');}return (int)n;}
struct Observation { struct BSDInfo info;char path[4096];const char *status,*stage;int error; };
static int info(int pid,struct BSDInfo *out,int *error){
 *errno_address()=0;int n=pid_info(pid,3,0,out,sizeof(*out));*error=*errno_address();
 if(n<=0)return *error==3?1:2;
 if(n!=(int)sizeof(*out)||out->pid!=(U32)pid||!out->start_sec||out->start_usec>=1000000||out->status<1||out->status>5)return 2;
 return 0;
}
static struct Observation observe(int pid){
 struct Observation r={0};struct BSDInfo before={0};r.status="failed";r.stage="identity_before";
 int result=info(pid,&before,&r.error);if(result){if(result==1)r.status="absent";return r;}
 r.info=before;
 if(before.status==5||(before.flags&4)){r.status="not_alive";r.stage="process_state";return r;}
 r.stage="executable";*errno_address()=0;int size=pid_path(pid,r.path,sizeof(r.path));r.error=*errno_address();
 if(size<=0||size>=(int)sizeof(r.path)||r.path[0]!='/')return r;
 int terminated=0;for(int i=0;i<size+1;i++)if(!r.path[i]){terminated=1;break;}if(!terminated)return r;
 r.stage="identity_after";result=info(pid,&r.info,&r.error);if(result){if(result==1)r.status="changed";return r;}
 if(r.info.pid!=before.pid||r.info.start_sec!=before.start_sec||r.info.start_usec!=before.start_usec){r.status="changed";return r;}
 if(r.info.status==5||(r.info.flags&4)){r.status="not_alive";r.stage="process_state";return r;}
 r.status="observed";r.stage="complete";r.error=0;return r;
}
#ifndef TTT_PROCESS_TEST
extern void *dlopen(const char *,int),*dlsym(void *,const char *);
extern int printf(const char *,...),fflush(void *),getpid(void),vsnprintf(char *,Size,const char *,__builtin_va_list);
extern long read(int,void *,Size),write(int,const void *,Size);
extern int close(int),socket(int,int,int),bind(int,const void *,U32),listen(int,int),accept(int,void *,U32 *),setsockopt(int,int,int,const void *,U32);
extern unsigned int alarm(unsigned int);
struct Timebase{U32 numerator,denominator;};
static U64 (*ticks)(void);static int (*timebase)(struct Timebase *),(*sysctl_named)(const char *,void *,Size *,const void *,Size);
static struct Timebase tb;static char boot[37];static char output[32768];static Size used;static int output_failed;
static int emit(const char *format,...){
 if(output_failed)return -1;__builtin_va_list ap;__builtin_va_start(ap,format);int n=vsnprintf(output+used,sizeof(output)-used,format,ap);__builtin_va_end(ap);
 if(n<0||(Size)n>=sizeof(output)-used){output_failed=1;return -1;}used+=(Size)n;return n;
}
static void quote(const char *s){emit("\"");for(;*s;s++){unsigned char c=(unsigned char)*s;if(c=='"'||c=='\\')emit("\\%c",c);else if(c<32)emit("\\u%04x",c);else emit("%c",c);}emit("\"");}
static int initialize(void){
 void *lib=dlopen("/usr/lib/libSystem.B.dylib",2);if(!lib)return 0;
#define LOAD(target,name) target=dlsym(lib,name);if(!target)return 0
 LOAD(pid_info,"proc_pidinfo");LOAD(pid_path,"proc_pidpath");LOAD(errno_address,"__error");LOAD(ticks,"mach_absolute_time");LOAD(timebase,"mach_timebase_info");LOAD(sysctl_named,"sysctlbyname");
#undef LOAD
 if(timebase(&tb)||!tb.denominator)return 0;Size size=sizeof(boot);
 if(sysctl_named("kern.bootsessionuuid",boot,&size,0,0)||size!=37||boot[36])return 0;
 for(int i=0;i<36;i++)if(i==8||i==13||i==18||i==23){if(boot[i]!='-')return 0;}else if(!((boot[i]>='0'&&boot[i]<='9')||(boot[i]>='A'&&boot[i]<='F')||(boot[i]>='a'&&boot[i]<='f')))return 0;
 return 1;
}
static void process(int pid){
 U64 started=ticks();struct Observation r=observe(pid);U64 elapsed=(ticks()-started)*tb.numerator/tb.denominator;
 emit("{\"protocol\":1,\"status\":\"%s\",\"stage\":\"%s\",\"errno\":%d,\"boot_uuid\":\"%s\",\"requested_pid\":%d,\"pid\":%u,\"start_seconds\":%llu,\"start_microseconds\":%llu,\"kernel_state\":%u,\"kernel_flags\":%u,\"elapsed_nanoseconds\":%llu,\"foreground_verified\":false,\"executable\":",r.status,r.stage,r.error,boot,pid,r.info.pid,r.info.start_sec,r.info.start_usec,r.info.status,r.info.flags,elapsed);quote(r.path);emit("}\n");
}
struct Address{unsigned char len,family;unsigned short reserved;U32 port,cid;};
struct Timeval{long seconds;int microseconds;};
_Static_assert(sizeof(struct Address)==12 && sizeof(struct Timeval)==16,"Darwin socket ABI");
static int serve(void){
 int listener=socket(40,1,0);if(listener<0)return 4;struct Address address={12,40,0,1342,0xffffffffU};
 if(bind(listener,&address,sizeof(address))||listen(listener,4)){close(listener);return 5;}
 printf("{\"protocol\":1,\"status\":\"listening\",\"boot_uuid\":\"%s\",\"pid\":%d,\"port\":1342}\n",boot,getpid());fflush(0);alarm(1200);
 int stopping=0;
 while(!stopping){
  struct Address peer={0};U32 size=sizeof(peer);int client=accept(listener,&peer,&size);if(client<0){if(*errno_address()==4)continue;close(listener);return 6;}
  if(size!=sizeof(peer)||peer.family!=40||peer.cid!=2){close(client);continue;}
  int yes=1;struct Timeval timeout={2,0};
  if(setsockopt(client,0xffff,0x1022,&yes,sizeof(yes))||setsockopt(client,0xffff,0x1005,&timeout,sizeof(timeout))||setsockopt(client,0xffff,0x1006,&timeout,sizeof(timeout))){close(client);continue;}
  used=0;output_failed=0;char command[64]={0};Size got=0;int newline=0;
  while(got<sizeof(command)-1&&!newline){long n=read(client,command+got,sizeof(command)-1-got);if(n<=0)break;got+=(Size)n;for(Size i=0;i<got;i++)if(command[i]=='\n')newline=1;}
  int valid=got>1&&command[got-1]=='\n';
  for(Size i=0;i+1<got;i++)if(!command[i]||command[i]=='\n')valid=0;
  if(valid)command[got-1]=0;else command[0]=0;
  int pid=0;if(command[0]=='p'&&command[1]=='i'&&command[2]=='d'&&command[3]==' ')pid=valid_pid(command+4);
  if(equal(command,"health"))emit("{\"protocol\":1,\"status\":\"ready\",\"pid\":%d,\"boot_uuid\":\"%s\",\"port\":1342,\"read_only\":true}\n",getpid(),boot);
  else if(pid)process(pid);
  else if(equal(command,"stop")){emit("{\"protocol\":1,\"status\":\"stopping\",\"pid\":%d,\"boot_uuid\":\"%s\"}\n",getpid(),boot);stopping=1;}
  else emit("{\"protocol\":1,\"status\":\"failed\",\"stage\":\"request\"}\n");
  if(!output_failed){Size done=0;while(done<used){long n=write(client,output+done,used-done);if(n<=0)break;done+=(Size)n;}}
  close(client);
 }
 close(listener);return 0;
}
int main(int argc,char **argv){if(argc!=2||!equal(argv[1],"serve"))return 2;if(!initialize())return 3;return serve();}
#endif
