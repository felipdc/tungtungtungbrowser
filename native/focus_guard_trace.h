static void print_guard_trace(void) {
 const struct FocusGuardTrace *t=&focus_guard_trace;
 printf("{\"begin_seconds\":%.6f,\"query_seconds\":%.6f,\"finish_receive_seconds\":%.6f,\"reap_seconds\":%.6f,",t->begin_finished-t->begin_started,t->finish_started-t->begin_finished,t->received_at-t->finish_started,t->reaped_at-t->received_at);
 printf("\"begin_write\":%ld,\"begin_errno\":%d,\"begin_received\":%d,\"begin_valid\":%d,",t->begin_write,t->begin_errno,t->begin_received,t->begin_valid);
 printf("\"finish_write\":%ld,\"finish_errno\":%d,\"finish_received\":%d,\"finish_valid\":%d,",t->finish_write,t->finish_errno,t->finish_received,t->finish_valid);
 printf("\"final_protocol\":%d,\"final_status\":%d,\"wait_pid\":%d,\"wait_errno\":%d,\"child_status\":%d,",t->final_protocol,t->final_status,t->wait_pid,t->wait_errno,t->child_status);
 printf("\"finish_poll_result\":%d,\"finish_revents\":%d,\"finish_read_bytes\":%ld,\"finish_read_errno\":%d}",t->finish_read.poll_result,t->finish_read.revents,t->finish_read.bytes,t->finish_read.read_errno);
}
