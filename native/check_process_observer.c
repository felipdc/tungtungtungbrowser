#define TTT_PROCESS_TEST
#include "process_observer.c"
#include <assert.h>
#include <string.h>
static int mode,calls,err;
static int *error_pointer(void){return &err;}
static int fake_info(int pid,int flavor,U64 arg,void *buffer,int size){
 assert(flavor==3 && arg==0 && size==136);calls++;struct BSDInfo *b=buffer;
 if(mode==1){err=3;return -1;}if(mode==2){err=1;return -1;}
 b->pid=pid;b->start_sec=100;b->start_usec=123;b->status=3;
 if(mode==3)return 120;if(mode==4)b->pid++;if(mode==5)b->start_usec=1000000;
 if(mode==6&&calls==2)b->start_sec++;if(mode==7)b->status=5;
 if(mode==8)b->flags=4;if(mode==9&&calls==2){err=3;return -1;}
 return size;
}
static int fake_path(int pid,void *buffer,U32 size){assert(pid==42&&size==4096);strcpy(buffer,"/Applications/MobileSafari.app/MobileSafari");return 43;}
int main(void){
 pid_info=fake_info;pid_path=fake_path;errno_address=error_pointer;
 assert(valid_pid("42")==42 && valid_pid("2147483647")==2147483647);
 for(const char **p=(const char *[]){"0","","-1","1x","2147483648","9999999999999999999","42\n",0};*p;p++)assert(!valid_pid(*p));
 const char *expected[]={"observed","absent","failed","failed","failed","failed","changed","not_alive","not_alive","changed"};
 for(mode=0;mode<10;mode++){calls=0;struct Observation r=observe(42);assert(equal(r.status,expected[mode]));}
 return 0;
}
