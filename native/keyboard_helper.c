/* Native navigation keys: preallocate one chord and enqueue release without a
 * scheduling sleep while keys are held. The post-release pause is not UI ACK. */
typedef unsigned long long U64;
typedef unsigned U32;
typedef unsigned char Bool;
typedef void *Ref;
extern void *dlopen(const char *,int),*dlsym(void *,const char *);
extern int printf(const char *,...),usleep(unsigned);
extern U64 mach_absolute_time(void);
static int equal(const char *a,const char *b){while(*a&&*a==*b){a++;b++;}return *a==*b;}
static int fail(const char *stage,int code){printf("{\"protocol\":1,\"status\":\"failed\",\"stage\":\"%s\",\"dispatch_count\":0}\n",stage);return code;}
int main(int argc,char **argv){
 int check=argc==2&&equal(argv[1],"check"),command=0;U32 key=0;
 if(!check){
  if(argc!=3||!equal(argv[1],"press"))return fail("arguments",2);
  if(equal(argv[2],"return"))key=0x28;
  else if(equal(argv[2],"cmd+a")){key=0x04;command=1;}
  else if(equal(argv[2],"cmd+l")){key=0x0f;command=1;}
  else if(equal(argv[2],"cmd+t")){key=0x17;command=1;}
  else if(equal(argv[2],"cmd+w")){key=0x1a;command=1;}
  else return fail("key",2);
 }
 void *io=dlopen("/System/Library/Frameworks/IOKit.framework/IOKit",2),*cf=dlopen("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",2),*lib=dlopen("/usr/lib/libSystem.B.dylib",2);
 if(!io||!cf||!lib)return fail("frameworks",3);
 Ref (*create_client)(Ref)=dlsym(io,"IOHIDEventSystemClientCreate");
 Ref (*make_key)(Ref,U64,U32,U32,Bool,U32)=dlsym(io,"IOHIDEventCreateKeyboardEvent");
 void (*set_integer)(Ref,U32,long)=dlsym(io,"IOHIDEventSetIntegerValue");
 void (*set_sender)(Ref,U64)=dlsym(io,"IOHIDEventSetSenderID");
 void (*set_time)(Ref,U64)=dlsym(io,"IOHIDEventSetTimeStamp");
 void (*dispatch)(Ref,Ref)=dlsym(io,"IOHIDEventSystemClientDispatchEvent");
 void (*release)(Ref)=dlsym(cf,"CFRelease");
 U64 (*clock_ns)(U32)=dlsym(lib,"clock_gettime_nsec_np");
 if(!create_client||!make_key||!set_integer||!set_sender||!set_time||!dispatch||!release||!clock_ns)return fail("symbols",4);
 Ref client=create_client(0);if(!client)return fail("client",5);
 if(check){release(client);printf("{\"protocol\":1,\"status\":\"ready\",\"dispatch_count\":0}\n");return 0;}
 unsigned count=command?4:2;U32 usages[4]={key,key,0,0};Bool down[4]={1,0,0,0};
 if(command){usages[0]=0xe3;usages[1]=key;usages[2]=key;usages[3]=0xe3;down[0]=down[1]=1;}
 Ref events[4]={0,0,0,0};U64 before[4]={0,0,0,0},after[4]={0,0,0,0};
 for(unsigned i=0;i<count;i++){
  /* kIOHIDKeyboardEventOptionsNoKeyRepeat: one-shot navigation keys. */
  events[i]=make_key(0,0,7,usages[i],down[i],1U << 8);
  if(!events[i]){for(unsigned j=0;j<i;j++)release(events[j]);release(client);return fail("event_allocation",6);}
  set_integer(events[i],4,1);set_sender(events[i],0x8000000817319372ULL);
 }
 /* No allocation, formatting, IPC readback or voluntary sleep between down
  * and release. The scheduler can still preempt: record actual dispatch times. */
 for(unsigned i=0;i<count;i++){
  set_time(events[i],mach_absolute_time());before[i]=clock_ns(6);dispatch(client,events[i]);after[i]=clock_ns(6);
 }
 int waited=usleep(20000)==0;
 for(unsigned i=0;i<count;i++)release(events[i]);
 release(client);
 printf("{\"protocol\":1,\"status\":\"dispatched\",\"key\":\"%s\",\"dispatch_count\":%u,\"release_dispatched\":true,\"post_release_wait_completed\":%s,\"application_consumption_verified\":false,\"events\":[",argv[2],count,waited?"true":"false");
 for(unsigned i=0;i<count;i++){if(i)printf(",");printf("{\"usage_page\":7,\"usage\":%u,\"down\":%u,\"before_ns\":%llu,\"after_ns\":%llu}",usages[i],down[i],before[i],after[i]);}
 printf("]}\n");return waited?0:7;
}
