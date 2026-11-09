#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../native/address_field.h"

struct Object { unsigned long kind; int index; };
static struct Object root={2,-1}, list={1,0}, items[4], names[4];
static int mode, releases, copies, retains, clocks;
static unsigned long type(const void *p){return ((const struct Object *)p)->kind;}
static unsigned long array_type(void){return 1;}
static unsigned long element_type(void){return 2;}
static unsigned long string_type(void){return 3;}
static int owner(const void *p,int *pid){
    *pid=((mode==4&&p==&items[2])||((mode==13||mode==14)&&p==&items[0])||(mode==18&&p==&root))?302:mode==17&&p==&items[2]?0:301;return 0;
}
static int timeout_value(const void *p,float seconds){assert(seconds==0.25f||seconds==1.0f);return mode==5&&p==&items[2]?-25216:0;}
static int copy(const void *p,const void *key,const void **out){
    *out=0;
    if(key==(const void *)3015){if(mode==6)return -25216;*out=&list;copies++;return 0;}
    assert(key==(const void *)5019);
    assert(!((mode==4&&p==&items[2])||((mode==13||mode==14)&&p==&items[0])));
    int i=((const struct Object *)p)->index;
    if(i==1)return -25212;
    if(mode==7&&i==2)return -25216;
    *out=&names[i];copies++;return 0;
}
static long count(const void *p){assert(p==&list);return mode==8?513:4;}
static const void *at(const void *p,long i){assert(p==&list);assert(i>=0&&i<4);return &items[i];}
static const void *retain(const void *p){retains++;return p;}
static void release(const void *p){assert(p);releases++;}
static unsigned char string_value(const void *p,char *text,long capacity,unsigned encoding){
    assert(capacity==256&&encoding==0x08000100);
    int i=((const struct Object *)p)->index;
    if(mode==9&&i==2)return 0;
    const char *value=mode==1?"Else":i==0||(mode==2&&i==3)?"URL":"Other";
    if (mode==14&&i==2) value="URL";
    if ((mode==15&&i==0)||(mode==16&&i==3)) value="TabBarItemTitle";
    strcpy(text,value);return 1;
}
static double now(void){clocks++;return mode==10&&clocks>=3?9.0:mode==11&&clocks>=6?9.0:1.0;}

int main(void){
    struct AddressFieldAPI api={copy,owner,timeout_value,type,array_type,element_type,string_type,count,at,retain,release,string_value,now};
    for(mode=0;mode<19;mode++){
        releases=copies=retains=clocks=0;
        list.kind=mode==12?3:1;
        for(int i=0;i<4;i++){items[i]=(struct Object){mode==3&&i==2?3:2,i};names[i]=(struct Object){3,i};}
        const void *found=(void *)1;struct AddressFieldScan scan;
        int error=address_field_find(&api,&root,301,&found,&scan);
        if(mode==0||mode==4||mode==15){assert(error==0&&found==&items[0]&&retains==1);assert(scan.complete&&scan.visited==4&&scan.matches==1);}
        else if(mode==1||mode==13){assert(error==0&&!found&&!retains);assert(scan.complete&&scan.visited==4&&!scan.matches);}
        else if(mode==14){assert(error==0&&found==&items[2]&&scan.complete&&scan.foreign==1&&retains==1);}
        else{assert(error!=0&&!found&&!retains&&!scan.complete);}
        assert(releases==copies);
    }
    puts("19 address lookup scenarios passed");
    return 0;
}
