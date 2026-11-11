#define main candidate_main
#define dlopen mock_dlopen
#define dlsym mock_dlsym
#define mach_absolute_time mock_ticks
#define usleep mock_usleep
#include "keyboard_helper.c"
#undef main
static unsigned allocated,dispatched,released,fail_at,interrupted;
static U32 seen_usage[4];static Bool seen_down[4];static U64 now;
struct Key{U32 usage;Bool down;};static struct Key keys[4];
void *mock_dlopen(const char *p,int n){(void)p;(void)n;return (void*)1;}
U64 mock_ticks(void){return ++now;}
int mock_usleep(unsigned n){if(n!=20000||dispatched!=4)return -1;return interrupted?-1:0;}
static Ref client(Ref p){(void)p;return (void*)2;}
static Ref key(Ref a,U64 b,U32 page,U32 usage,Bool down,U32 flags){(void)a;(void)b;if(flags!=(1U<<8))return 0;if(page!=7)return 0;if(++allocated==fail_at)return 0;keys[allocated-1]=(struct Key){usage,down};return &keys[allocated-1];}
static void integer(Ref a,U32 field,long value){(void)a;(void)field;(void)value;}
static void sender(Ref a,U64 b){(void)a;(void)b;}
static void dispatch(Ref c,Ref p){(void)c;struct Key *k=p;seen_usage[dispatched]=k->usage;seen_down[dispatched++]=k->down;}
static void release(Ref p){(void)p;released++;}
static U64 clock(U32 n){return n==6?++now:0;}
void *mock_dlsym(void *p,const char *n){(void)p;
 if(equal(n,"IOHIDEventSystemClientCreate"))return client;
 if(equal(n,"IOHIDEventCreateKeyboardEvent"))return key;
 if(equal(n,"IOHIDEventSetIntegerValue"))return integer;
 if(equal(n,"IOHIDEventSetSenderID")||equal(n,"IOHIDEventSetTimeStamp"))return sender;
 if(equal(n,"IOHIDEventSystemClientDispatchEvent"))return dispatch;
 if(equal(n,"CFRelease"))return release;
 if(equal(n,"clock_gettime_nsec_np"))return clock;
 return 0;
}
int main(int argc,char **argv){
 if(argc!=2)return 20;
 if(equal(argv[1],"allocation"))fail_at=3;
 if(equal(argv[1],"interrupted"))interrupted=1;
 char *arguments[]={"helper","press","cmd+w",0};
 if(equal(argv[1],"invalid"))arguments[2]="cmd+shift+w";
 int code=candidate_main(3,arguments);
 if(fail_at)return !(code==6&&dispatched==0&&released==3);
 if(equal(argv[1],"invalid"))return !(code==2&&allocated==0&&dispatched==0);
 if(dispatched!=4||released!=5)return 21;
 if(seen_usage[0]!=0xe3||seen_usage[1]!=0x1a||seen_usage[2]!=0x1a||seen_usage[3]!=0xe3||seen_down[0]!=1||seen_down[1]!=1||seen_down[2]||seen_down[3])return 22;
 return code==(interrupted?7:0)?0:23;
}
