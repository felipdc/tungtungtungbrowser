#include "focus_guard.h"
#include "reusable_guard.h"
typedef unsigned long Size;
extern void *dlopen(const char *,int),*dlsym(void *,const char *);
extern int printf(const char *,...),snprintf(char *,Size,const char *,...),fflush(void *),getpid(void),sysctlbyname(const char *,void *,Size *,const void *,Size);
extern int socket(int,int,int),bind(int,const void *,unsigned),listen(int,int),accept(int,void *,unsigned *),setsockopt(int,int,int,const void *,unsigned);
extern unsigned alarm(unsigned);
static char boot[64];static volatile int stopped;
static int equal(const char*a,const char*b){while(*a && *a==*b){a++;b++;}return *a==*b;}
static void stopping(int sig){(void)sig;stopped=1;}
#include "spawn.h"
struct Address{unsigned char len,family;unsigned short reserved;unsigned port,cid;};
struct Timeval{long seconds;int microseconds;};
/* A stream may fragment a request. The total read budget never renews. */
static int request(int client,char *input,unsigned capacity){
 double start=focus_guard_now();if(start<0)return 0;double end=start+2;
 for(unsigned n=0;n+1<capacity;n++){
  double now=focus_guard_now();if(now<0||now>=end)return 0;
  if(!focus_guard_receive(client,input+n,1,(int)((end-now)*1000)+1))return 0;
  if(input[n]=='\n'){input[n]=0;return n>0;}
  if(input[n]<32||input[n]>126)return 0;
 }return 0;
}
static int serve(const char *guardian){
 signal(SIGPIPE,SIG_IGN);signal(15,stopping);signal(2,stopping);
 int listener=socket(40,1,0);if(listener<0)return 10;
 struct Address address={12,40,0,1346,0xffffffffU};
 if(bind(listener,&address,sizeof(address))||listen(listener,4)){close(listener);return 11;}
 struct FocusGuard guard;struct FocusGuardState state={0};
 if(!focus_guard_spawn(guardian,&guard)){close(listener);return 12;}
 if(!focus_guard_begin(&guard,&state)){
  close(guard.input);close(guard.output);close(listener);alarm(20);int status;waitpid(guard.pid,&status,0);return 13;
 }
 int before_app=state.before_app,before_auto=state.before_auto;
 printf("{\"protocol\":1,\"status\":\"enabled\",\"pid\":%d,\"guardian_pid\":%d,\"boot_uuid\":\"%s\",\"before\":{\"application\":%d,\"automation\":%d},\"after\":{\"application\":%d,\"automation\":%d},\"deadline_seconds\":1800}\n",getpid(),guard.pid,boot,before_app,before_auto,state.after_app,state.after_auto);fflush(0);
 double end=focus_guard_now()+1800;char expected[96],pause[96],resume[96];
 snprintf(expected,sizeof(expected),"stop %d %s",getpid(),boot);
 snprintf(pause,sizeof(pause),"pause %d %s",getpid(),boot);
 snprintf(resume,sizeof(resume),"resume %d %s",getpid(),boot);
 int active=1,guard_failed=0;
 printf("{\"protocol\":1,\"status\":\"listening\",\"pid\":%d,\"boot_uuid\":\"%s\",\"port\":1346}\n",getpid(),boot);fflush(0);
 while(!stopped && focus_guard_now()<end){
  struct pollfd p={listener,POLLIN,0};int available=poll(&p,1,1000);if(available<0)continue;if(!available)continue;
  struct Address peer={0};unsigned size=sizeof(peer);int client=accept(listener,&peer,&size);if(client<0)continue;
  if(size!=sizeof(peer)||peer.family!=40||peer.cid!=2){close(client);continue;}
  struct Timeval timeout={2,0};int yes=1;
  if(setsockopt(client,0xffff,0x1022,&yes,sizeof(yes))||setsockopt(client,0xffff,0x1005,&timeout,sizeof(timeout))||setsockopt(client,0xffff,0x1006,&timeout,sizeof(timeout))){close(client);continue;}
  char input[128]={0};char output[768];int length;
  if(!request(client,input,sizeof(input)))input[0]=0;
  if(stopped || focus_guard_now()<0 || focus_guard_now()>=end){close(client);break;}
  if(equal(input,"health")||equal(input,expected)||equal(input,pause)||equal(input,resume)){
   if(equal(input,expected))stopped=1;
   char command=equal(input,pause)?'P':equal(input,resume)?'A':'H';
   int next=command=='P'?0:command=='A'?1:active;
   if(!stopped && !lease_guard_command(&guard,command,&state,before_app,before_auto,next)){
    guard_failed=1;stopped=1;
   }else if(!stopped)active=next;
   const char *status=guard_failed?"failed":stopped?"stopping":command=='H'?"ready":"scope";
   length=snprintf(output,sizeof(output),"{\"protocol\":1,\"status\":\"%s\",\"pid\":%d,\"guardian_pid\":%d,\"boot_uuid\":\"%s\",\"port\":1346,\"navigation_scope\":\"guarded-v1\",\"active\":%s,\"before_application\":%d,\"before_automation\":%d,\"after_application\":%d,\"after_automation\":%d,\"guard_status\":%d}\n",status,getpid(),guard.pid,boot,active?"true":"false",before_app,before_auto,state.after_app,state.after_auto,state.status);
  }else length=snprintf(output,sizeof(output),"{\"protocol\":1,\"status\":\"rejected\"}\n");
  if(length>0 && length<(int)sizeof(output)){
   /* An incomplete receipt stays uncertain; never repeat its transition. */
   if(write(client,output,(Size)length)!=length){close(client);continue;}
  }close(client);
 }
 close(listener);alarm(20);int restored;
 if(guard_failed){
  /* A failure frame is terminal. Do not replay a transition or consume it as
   * the acknowledgement of a new command. EOF still reaches the guardian. */
  close(guard.input);close(guard.output);int status=-1;
  int reaped=waitpid(guard.pid,&status,0)==guard.pid;
  restored=reaped && status==(11<<8) && state.protocol==1 && state.status>0;
 }else restored=focus_guard_finish(&guard,&state);
 alarm(0);
 restored=restored && state.before_app==before_app && state.before_auto==before_auto && state.after_app==before_app && state.after_auto==before_auto;
 printf("{\"protocol\":1,\"status\":\"closed\",\"pid\":%d,\"boot_uuid\":\"%s\",\"restored\":%s,\"guard_failed\":%s,\"before\":{\"application\":%d,\"automation\":%d},\"after\":{\"application\":%d,\"automation\":%d}}\n",getpid(),boot,restored?"true":"false",guard_failed?"true":"false",before_app,before_auto,state.after_app,state.after_auto);fflush(0);return restored&&!guard_failed?0:14;
}
int main(int argc,char **argv){
 if(!focus_guard_reset_signals())return 2;Size size=sizeof(boot);if(sysctlbyname("kern.bootsessionuuid",boot,&size,0,0)||size!=37)return 3;
 if(argc==4 && equal(argv[1],"spawn"))return spawn_owned(argv[0],argv[2],argv[3]);
 if(argc==3 && equal(argv[1],"serve") && argv[2][0]=='/')return serve(argv[2]);
 return 2;
}
