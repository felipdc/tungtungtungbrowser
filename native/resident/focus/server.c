#include "query.c"
extern int snprintf(char *,Size,const char *,...),getpid(void),sysctlbyname(const char *,void *,Size *,const void *,Size);
extern int socket(int,int,int),bind(int,const void *,unsigned),listen(int,int),accept(int,void *,unsigned *),setsockopt(int,int,int,const void *,unsigned);
static char boot[64];static volatile int stopped;
static int equal(const char*a,const char*b){while(*a&&*a==*b){a++;b++;}return *a==*b;}
static void stopping(int sig){(void)sig;stopped=1;}
#include "spawn.h"
struct Address{unsigned char len,family;unsigned short reserved;unsigned port,cid;};
struct Timeval{long seconds;int microseconds;};
static int serve(char *self){
 int listener=socket(40,1,0);if(listener<0)return 10;
 if(fcntl(listener,F_SETFD,FD_CLOEXEC)){close(listener);return 10;}
 struct Address address={12,40,0,1347,0xffffffffU};if(bind(listener,&address,sizeof(address))||listen(listener,4)){close(listener);return 11;}
 char *check[]={self,"check",0};capturing=1;int status=query_main(2,check);alarm(0);capturing=0;used=0;output_failed=0;if(status){close(listener);return 12;}
 signal(SIGPIPE,SIG_IGN);signal(15,stopping);signal(2,stopping);
 double end=focus_guard_now()+1800;char expected[96];snprintf(expected,sizeof(expected),"stop %d %s",getpid(),boot);
 printf("{\"protocol\":2,\"status\":\"listening\",\"pid\":%d,\"boot_uuid\":\"%s\",\"port\":1347}\n",getpid(),boot);fflush(0);
 while(!stopped&&focus_guard_now()<end){
  focus_guard_pool_maintain(self);
  struct pollfd p={listener,POLLIN,0};if(poll(&p,1,1000)<=0)continue;
  struct Address peer={0};unsigned size=sizeof(peer);int fd=accept(listener,&peer,&size);if(fd<0)continue;
  if(fcntl(fd,F_SETFD,FD_CLOEXEC)){close(fd);continue;}
  if(size!=sizeof(peer)||peer.family!=40||peer.cid!=2){close(fd);continue;}
  struct Timeval t={2,0};int yes=1;if(setsockopt(fd,0xffff,0x1022,&yes,sizeof(yes))||setsockopt(fd,0xffff,0x1005,&t,sizeof(t))||setsockopt(fd,0xffff,0x1006,&t,sizeof(t))){close(fd);continue;}
  char input[128]={0};unsigned got=0;int done=0;
  while(got<sizeof(input)-1&&!done){long count=read(fd,input+got,sizeof(input)-1-got);if(count<=0)break;got+=(unsigned)count;for(unsigned i=0;i<got;i++)if(input[i]=='\n'){done=1;break;}}
  if(done&&got&&input[got-1]=='\n')input[got-1]=0;else input[0]=0;
  output_fd=fd;used=0;output_failed=0;capturing=0;
  if(equal(input,"health")||equal(input,expected)){
   stopped=equal(input,expected);printf("{\"protocol\":2,\"status\":\"%s\",\"pid\":%d,\"boot_uuid\":\"%s\",\"port\":1347}\n",stopped?"stopping":"ready",getpid(),boot);
  }else if(input[0]=='p'&&input[1]=='i'&&input[2]=='d'&&input[3]==' '&&valid_pid(input+4)){
   printf("{\"protocol\":2,\"status\":\"query\",\"pid\":%d,\"boot_uuid\":\"%s\",\"result\":",getpid(),boot);OutSize prefix=used;
   char *args[]={self,"pid",input+4,0};capturing=1;status=query_main(3,args);alarm(0);capturing=0;
   signal(SIGPIPE,SIG_IGN);signal(15,stopping);signal(2,stopping);
   if(used==prefix)printf("null");printf(",\"exit_code\":%d,\"prepared_guard\":%s}\n",status,focus_pool_used_last?"true":"false");
  }else printf("{\"protocol\":2,\"status\":\"rejected\"}\n");
  fflush(0);close(fd);output_fd=1;used=0;output_failed=0;
 }
 close(listener);if(!focus_guard_pool_close())return 14;printf("{\"protocol\":2,\"status\":\"closed\",\"pid\":%d,\"boot_uuid\":\"%s\",\"guard_pool\":{\"spawned\":%d,\"reused\":%d,\"discarded\":%d,\"reaped\":%d,\"failed\":%d,\"remaining\":%d}}\n",getpid(),boot,focus_pool.spawned,focus_pool.reused,focus_pool.discarded,focus_pool.reaped,focus_pool.failed,focus_pool.guard.pid);fflush(0);return 0;
}
int main(int argc,char **argv){
 if(argc==2&&equal(argv[1],"guard"))return query_main(argc,argv);
 if(!focus_guard_reset_signals())return 2;Size size=sizeof(boot);if(sysctlbyname("kern.bootsessionuuid",boot,&size,0,0)||size!=37)return 3;
 if(argc==4&&equal(argv[1],"spawn"))return spawn_owned(argv[0],argv[2],argv[3]);
 if(argc==3&&equal(argv[1],"serve"))return serve(argv[0]);return 2;
}
