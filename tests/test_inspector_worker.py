from pathlib import Path
import json,subprocess,tempfile,unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'native/inspector_bridge.c').read_text()
WORKER=SOURCE[SOURCE.index('union ThreadAttributes'):SOURCE.index('/* The owner carries')]
PREFIX=r'''
#include <assert.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static int mode, initialized, destroyed, created, joined, core_calls, exited;
static unsigned active_qos=21;
static void *(*pending)(void *);
static void *pending_argument;
static jmp_buf escape;
static void *open_inspector_core(int *native,const char **failure) {
    assert(active_qos==25); assert(joined); ++core_calls;
    if(mode==9) { *native=-1; *failure="native_fixture_failure"; return 0; }
    *native=7; *failure=0; return (void *)0x1234;
}
static int fake_init(void *p) { ++initialized; memset(p,0,64);return mode==3?1:0; }
static int fake_destroy(void *p) { (void)p; ++destroyed;return mode==10?1:0; }
static int fake_qos(void *p,unsigned qos,int priority) { (void)p;assert(qos==25 && priority==0);return mode==4?1:0; }
static unsigned fake_self(void) {return active_qos;}
static int fake_create(void **thread,const void *p,void *(*run)(void *),void *argument) {
    (void)p; ++created;assert(core_calls==0 && active_qos==21);
    if(mode==5)return 1;
    pending=run;pending_argument=argument;*thread=(void *)0x1111;return 0;
}
static int fake_join(void *thread,void **result) {
    assert(thread==(void *)0x1111 && result==0); ++joined;
    if(mode==7)return 1;
    active_qos=mode==6?21:25;pending(pending_argument);active_qos=21;return 0;
}
static void fake_exit(int status) {assert(status==10);++exited;longjmp(escape,1);}
void *dlopen(const char *path,int flags) {
    assert(strcmp(path,"/usr/lib/libSystem.B.dylib")==0 && flags==2);
    return mode==1?0:(void *)0x8888;
}
void *dlsym(void *library,const char *name) {
    assert(library==(void *)0x8888);
    if(mode==2 && strcmp(name,"pthread_join")==0)return 0;
    if(strcmp(name,"pthread_attr_init")==0)return (void *)fake_init;
    if(strcmp(name,"pthread_attr_destroy")==0)return (void *)fake_destroy;
    if(strcmp(name,"pthread_attr_set_qos_class_np")==0)return (void *)fake_qos;
    if(strcmp(name,"pthread_create")==0)return (void *)fake_create;
    if(strcmp(name,"pthread_join")==0)return (void *)fake_join;
    if(strcmp(name,"qos_class_self")==0)return (void *)fake_self;
    if(strcmp(name,"_exit")==0)return (void *)fake_exit;
    abort();
}
'''
SUFFIX=r'''
int main(int argc,char **argv) {
    assert(argc==2);mode=atoi(argv[1]);int native=99;const char *failure="initial";
    if(setjmp(escape)) { assert(mode==7 && exited==1 && joined==1 && core_calls==0);return 0; }
    void *connection=open_inspector(&native,&failure);
    assert(mode!=7 && active_qos==21);
    if(mode==0 || mode==10) {
        assert(connection==(void *)0x1234 && native==7 && failure==0);
        assert(core_calls==1 && initialized==1 && destroyed==1 && created==1 && joined==1);
    } else if(mode==9) {
        assert(!connection && native==-1 && strcmp(failure,"native_fixture_failure")==0 && core_calls==1 && joined==1);
    } else {
        assert(!connection && native==-1 && failure);
        assert(core_calls==0);
        assert(strcmp(failure,mode==6?"startup_qos_not_applied":"startup_worker_failed")==0);
        if(mode<=2)assert(initialized==0 && created==0 && joined==0);
        if(mode==3)assert(initialized==1 && destroyed==0 && created==0 && joined==0);
        if(mode==4)assert(destroyed==1 && created==0 && joined==0);
        if(mode==5)assert(destroyed==1 && created==1 && joined==0);
        if(mode==6)assert(destroyed==1 && created==1 && joined==1);
    }
    return 0;
}
'''

class HandoffWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();p=Path(cls.temp.name)
        (p/'harness.c').write_text(PREFIX+WORKER+SUFFIX)
        cls.binary=p/'harness'
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',str(p/'harness.c'),'-o',str(cls.binary)],check=True)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def run_mode(self,mode):
        result=subprocess.run([str(self.binary),str(mode)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        return [json.loads(s) for s in result.stdout.splitlines()]
    def test_worker_owns_priority_and_joins_before_result_transfer(self):
        records=self.run_mode(0);self.assertEqual(len(records),1)
        self.assertEqual({k:records[0][k] for k in ['caller_qos','worker_qos','caller_qos_after']},
                         {'caller_qos':21,'worker_qos':25,'caller_qos_after':21})
    def test_setup_failures_do_not_start_native_handoff(self):
        for mode in (1,2,3,4,5):
            with self.subTest(mode=mode):self.assertEqual(self.run_mode(mode),[])
    def test_wrong_worker_priority_never_starts_native_handoff(self):
        self.assertEqual(self.run_mode(6)[0]['worker_qos'],21)
    def test_uncertain_join_exits_instead_of_releasing_stack_request(self):self.assertEqual(self.run_mode(7),[])
    def test_native_failure_retains_ownership_and_error(self):self.assertEqual(self.run_mode(9)[0]['caller_qos_after'],21)
    def test_attribute_cleanup_error_still_joins_active_worker(self):self.assertEqual(self.run_mode(10)[0]['attributes_destroy_error'],1)

if __name__=='__main__':unittest.main()
