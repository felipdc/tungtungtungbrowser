typedef unsigned long OutSize;
extern int vsnprintf(char *,OutSize,const char *,__builtin_va_list);
extern long write(int,const void *,OutSize);
static char output[65536];static OutSize used;static int output_fd=1,output_failed,capturing;
static int emit(const char *format,...){
 if(output_failed)return -1;__builtin_va_list ap;__builtin_va_start(ap,format);int n=vsnprintf(output+used,sizeof(output)-used,format,ap);__builtin_va_end(ap);
 if(n<0||(OutSize)n>=sizeof(output)-used){output_failed=1;return -1;}used+=(OutSize)n;return n;
}
static int flush_output(void *unused){
 (void)unused;if(capturing)return 0;if(output_failed)return -1;OutSize done=0;
 while(done<used){long n=write(output_fd,output+done,used-done);if(n<=0){output_failed=1;return -1;}done+=(OutSize)n;}used=0;return 0;
}
#define printf emit
#define fflush flush_output
