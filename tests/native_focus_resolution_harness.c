#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdio.h>
#include "../native/address_field.h"
#include "../native/focus_resolution.h"
struct Reply { int error, present; double seconds; struct AddressFieldScan scan; };
struct Test { int focused_calls, address_calls, settles, released, settle_error; double clock; struct Reply first, address[3]; };
static struct Test test;
static int token;
static int focused(void *c, const void **out) { (void)c;test.focused_calls++;test.clock+=test.first.seconds;*out=test.first.present?&token:0;return test.first.error; }
static int address(void *c, const void **out, struct AddressFieldScan *scan) { (void)c;struct Reply r=test.address[test.address_calls++];test.clock+=r.seconds;*out=r.present?&token:0;*scan=r.scan;return r.error; }
static void release(const void *v) { assert(v==&token);test.released++; }
static double now(void) { return test.clock; }
static int settle(void *c) { (void)c;test.settles++;test.clock+=0.4;return test.settle_error; }
static struct Reply match={0,1,0.1,{18,18,1,0,1}}, absent={0,0,0.1,{0,0,0,0,1}}, unavailable={-25216,0,0.1,{0,0,0,0,0}};
static void reset(void) { test=(struct Test){0};test.first=(struct Reply){0,0,0.1,{0}};for(int i=0;i<3;i++)test.address[i]=match; }
static int run(const void **out, struct FocusResolution *trace) { struct FocusResolutionAPI api={focused,address,release,now,settle};struct AddressFieldScan scan;int error=focus_resolve(&api,0,out,&scan,trace);assert(test.focused_calls==1);assert(test.address_calls<=3);assert(test.settles<=2);return error; }
int main(void) {
 (void)&address_field_find;
 const void *out;struct FocusResolution trace;
 reset();test.first=match;assert(run(&out,&trace)==0&&out==&token&&trace.source==1&&test.address_calls==0);
 reset();test.first.error=-777;assert(run(&out,&trace)==-777&&!out&&test.address_calls==0);
 reset();test.first.error=-25216;test.first.present=1;assert(run(&out,&trace)==-25216&&out&&test.address_calls==0);
 for(int i=0;i<3;i++){reset();test.first.error=(int[]){0,-25212,-25216}[i];assert(run(&out,&trace)==0&&out&&trace.source==2&&test.address_calls==1);}
 reset();test.first.error=-25216;test.address[0]=unavailable;assert(run(&out,&trace)==0&&out&&trace.attempts==2&&test.settles==1&&trace.address[0].error==-25216);
 reset();test.address[0]=absent;assert(run(&out,&trace)==0&&out&&trace.attempts==2);
 reset();for(int i=0;i<3;i++)test.address[i]=unavailable;assert(run(&out,&trace)==-25216&&!out&&trace.attempts==3);
 reset();for(int i=0;i<3;i++)test.address[i]=absent;assert(run(&out,&trace)==0&&!out&&trace.attempts==3);
 reset();test.address[0]=unavailable;test.address[0].scan.elements=3;test.address[0].scan.visited=1;assert(run(&out,&trace)==-25216&&!out&&trace.attempts==1);
 reset();test.address[0]=(struct Reply){-10,0,0.1,{18,5,2,0,0}};assert(run(&out,&trace)==-10&&!out&&trace.attempts==1);
 reset();test.first=match;test.first.seconds=10;assert(run(&out,&trace)==-30&&!out&&test.released==1&&test.address_calls==0);
 reset();test.address[0].seconds=10;assert(run(&out,&trace)==-30&&!out&&test.released==1&&trace.attempts==1);
 reset();test.first.seconds=0.9;test.address[0]=unavailable;test.address[0].seconds=9;assert(run(&out,&trace)==-30&&!out&&trace.attempts==1&&test.settles==1);
 reset();test.first.seconds=-1;assert(run(&out,&trace)==-30&&!out&&test.address_calls==0);
 reset();test.address[0]=unavailable;test.settle_error=1;assert(run(&out,&trace)==-31&&!out&&trace.attempts==1);
 reset();test.address[0]=(struct Reply){0,0,0.1,{6,6,0,0,1}};assert(run(&out,&trace)==0&&out&&trace.attempts==2);
 reset();test.address[0]=(struct Reply){-25212,0,0.1,{0}};assert(run(&out,&trace)==-25212&&!out&&trace.attempts==1);
 reset();for(int i=0;i<3;i++)test.address[i]=(struct Reply){0,0,0.1,{24,24,0,0,1}};assert(run(&out,&trace)==0&&!out&&trace.attempts==3);
 reset();test.address[0]=(struct Reply){0,0,0.1,{24,23,0,0,1}};assert(run(&out,&trace)==0&&!out&&trace.attempts==1);
 puts("21 guarded resolution scenarios passed");
 return 0;
}
