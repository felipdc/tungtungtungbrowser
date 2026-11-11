#include <assert.h>
#include <stddef.h>
#include <string.h>
#include "focus_attributes.h"
struct Obj { unsigned long type; long long number; int live; const void *items[5]; long count; };
static struct Obj numbers[5], args, result, fields[5], nullv, errv;
static int allocated, released, copy_calls, fail_number, fail_array, mode;
static unsigned long typ(FocusAttrValue p){return ((const struct Obj*)p)->type;}
static unsigned long array_type(void){return 2;}
static unsigned long null_type(void){return 3;}
static unsigned long value_type(void){return 4;}
static FocusAttrValue number(FocusAttrValue a,int type,const void *v){
 assert(!a && type==4);int i=allocated++;if(i==fail_number)return NULL;
 numbers[i]=(struct Obj){.type=1,.number=*(const long long*)v,.live=1};return &numbers[i];
}
static FocusAttrValue array(FocusAttrValue a,const FocusAttrValue *items,long n,const void *callbacks){
 assert(!a && !callbacks && n==5);if(fail_array)return NULL;
 args=(struct Obj){.type=2,.live=1,.count=n};memcpy(args.items,items,sizeof(args.items));return &args;
}
static void release(FocusAttrValue p){struct Obj *v=(struct Obj*)p;assert(v->live==1);v->live=0;released++;}
static long count(FocusAttrValue p){return ((const struct Obj*)p)->count;}
static FocusAttrValue at(FocusAttrValue p,long i){assert(i>=0&&i<5);return ((const struct Obj*)p)->items[i];}
static unsigned char read_value(FocusAttrValue p,int type,void *out){assert(type==5);if(p!=&errv)return 0;*(int*)out=(int)errv.number;return 1;}
static int copy(FocusAttrValue element,FocusAttrValue attributes,unsigned long long options,FocusAttrValue *out){
 static const long long expected[]={2001,2006,5019,2004,2003};assert(element==&fields[0]&&!options);copy_calls++;
 assert(attributes==&args && args.live);for(int i=0;i<5;i++){assert(numbers[i].live==1 && numbers[i].number==expected[i]);assert(args.items[i]==&numbers[i]);}
 result=(struct Obj){.type=mode==3?1:2,.live=1,.count=mode==4?4:mode==5?6:5};for(int i=0;i<5;i++)result.items[i]=&fields[i];
 *out=mode==2?NULL:&result;return mode==1?-25216:0;
}
static struct FocusAttributeAPI api={number,array,typ,array_type,null_type,value_type,count,at,read_value,release,copy};
static void reset(void){memset(numbers,0,sizeof(numbers));allocated=released=copy_calls=fail_array=mode=0;fail_number=-1;nullv=(struct Obj){.type=3};errv=(struct Obj){.type=4,.number=-25212};for(int i=0;i<5;i++)fields[i]=(struct Obj){.type=1};}
int main(void){
 struct FocusAttributeResult r;FocusAttrValue v;
 reset();assert(focus_attributes_read(&api,&fields[0],&r)==0);assert(copy_calls==1&&released==6&&result.live);
 for(unsigned i=0;i<5;i++)assert(focus_attribute_value(&api,&r,i,&v)==0&&v==&fields[i]);
 result.items[0]=&nullv;assert(focus_attribute_value(&api,&r,0,&v)==-25212&&!v);
 result.items[1]=&errv;assert(focus_attribute_value(&api,&r,1,&v)==-25212&&!v);
 errv.number=-25216;assert(focus_attribute_value(&api,&r,1,&v)==-25216&&!v);
 result.items[2]=NULL;assert(focus_attribute_value(&api,&r,2,&v)==-25212&&!v);
 fields[3].type=4;assert(focus_attribute_value(&api,&r,3,&v)==0&&v==&fields[3]);
 assert(focus_attribute_value(&api,&r,5,&v)==-25200&&!v);release(r.values);assert(released==7);
 for(int m=1;m<=5;m++){reset();mode=m;assert(focus_attributes_read(&api,&fields[0],&r)==(m==1?-25216:-25200));assert(copy_calls==1&&!r.values);assert(released==(m==2?6:7));assert(focus_attribute_value(&api,&r,0,&v)==r.error&&!v);}
 for(int i=0;i<5;i++){reset();fail_number=i;assert(focus_attributes_read(&api,&fields[0],&r)==-25200);assert(copy_calls==0&&released==i&&!r.values);}
 reset();fail_array=1;assert(focus_attributes_read(&api,&fields[0],&r)==-25200);assert(copy_calls==0&&released==5&&!r.values);
 return 0;
}
