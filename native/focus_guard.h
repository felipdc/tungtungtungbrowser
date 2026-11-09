/* SPDX-License-Identifier: MIT
 * A separate process owns temporary accessibility settings. The querying
 * process never writes them. EOF, an invalid completion, or a deadline restores
 * the saved values, even if the querying process dies on SIGALRM/SIGKILL.
 * No framework function is called from a signal handler or after fork().
 */
#ifdef __APPLE__
typedef void *posix_spawn_file_actions_t;
typedef unsigned int sigset_t;
struct pollfd { int fd; short events, revents; };
struct timespec { long tv_sec, tv_nsec; };
extern int *__error(void);
#define focus_guard_errno (*__error())
extern int pipe(int *), close(int), poll(struct pollfd *, unsigned, int);
extern long read(int, void *, unsigned long), write(int, const void *, unsigned long);
extern int open(const char *, int, ...), flock(int, int), fcntl(int, int, ...);
extern int setsid(void), waitpid(int, int *, int), clock_gettime(int, struct timespec *);
extern void (*signal(int, void (*)(int)))(int);
extern int sigprocmask(int, const sigset_t *, sigset_t *);
extern int posix_spawn_file_actions_init(posix_spawn_file_actions_t *);
extern int posix_spawn_file_actions_destroy(posix_spawn_file_actions_t *);
extern int posix_spawn_file_actions_adddup2(posix_spawn_file_actions_t *, int, int);
extern int posix_spawn_file_actions_addclose(posix_spawn_file_actions_t *, int);
extern int posix_spawn_file_actions_addopen(posix_spawn_file_actions_t *, int, const char *, int, unsigned short);
extern int posix_spawn(int *, const char *, const posix_spawn_file_actions_t *, const void *, char *const *, char *const *);
#define O_RDWR 2
#define O_CREAT 0x0200
#define O_NOFOLLOW 0x0100
#define LOCK_EX 2
#define LOCK_NB 4
#define F_SETFD 2
#define FD_CLOEXEC 1
#define POLLIN 1
#define CLOCK_MONOTONIC 6
#define SIGPIPE 13
#define SIGALRM 14
#define SIGCHLD 20
#define SIG_SETMASK 3
#define SIG_DFL ((void (*)(int))0)
#define SIG_ERR ((void (*)(int))-1)
#define SIG_IGN ((void (*)(int))1)
#else
#include <errno.h>
#define focus_guard_errno errno
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <sys/file.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#endif

#ifndef TTT_FOCUS_GUARD_WAIT_MS
#define TTT_FOCUS_GUARD_WAIT_MS 22000
#endif

struct FocusGuard { int pid, input, output; };
struct FocusGuardState { int protocol, status, before_app, before_auto, after_app, after_auto; };
_Static_assert(sizeof(struct FocusGuardState) == 24, "focus guard protocol ABI");

/* Observation only. The pipe ABI and all deadlines stay unchanged. */
struct FocusGuardReadTrace { int polls, poll_result, revents, read_errno; long bytes; };
static struct FocusGuardReadTrace focus_guard_last_read;
struct FocusGuardTrace {
    double begin_started, begin_finished, finish_started, received_at, reaped_at;
    long begin_write, finish_write;
    int begin_received, finish_received, begin_valid, finish_valid;
    int begin_errno, finish_errno, wait_pid, wait_errno, child_status;
    int final_protocol, final_status;
    struct FocusGuardReadTrace begin_read, finish_read;
};
static struct FocusGuardTrace focus_guard_trace;


static int focus_guard_reset_signals(void) {
    /* The administrative process launcher can inherit blocked SIGALRM. An
     * alarm alone then never terminates a stuck query. This standalone helper
     * owns its signal state; no daemon or browser process is modified. */
    sigset_t empty = {0};
    if (signal(SIGALRM, SIG_DFL) == SIG_ERR || signal(SIGCHLD, SIG_DFL) == SIG_ERR) return 0;
    return sigprocmask(SIG_SETMASK, &empty, 0) == 0;
}

static double focus_guard_now(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) return -1;
    return (double)now.tv_sec + now.tv_nsec / 1000000000.0;
}

static int focus_guard_receive(int fd, void *data, unsigned long size, int milliseconds) {
    focus_guard_last_read = (struct FocusGuardReadTrace){0, 0, 0, 0, -2};
    double start = focus_guard_now();
    if (start < 0) return 0;
    double end = start + milliseconds / 1000.0;
    for (;;) {
        double now = focus_guard_now();
        if (now < 0 || now >= end) return 0;
        struct pollfd descriptor = {fd, POLLIN, 0};
        int result = poll(&descriptor, 1, (int)((end - now) * 1000) + 1);
        focus_guard_last_read.polls++; focus_guard_last_read.poll_result = result;
        focus_guard_last_read.revents = descriptor.revents;
        if (result < 0) continue;
        if (!result) return 0;
        /* Every protocol write fits in PIPE_BUF. Partial frames are errors. */
        long bytes = read(fd, data, size);
        focus_guard_last_read.bytes = bytes;
        focus_guard_last_read.read_errno = bytes < 0 ? focus_guard_errno : 0;
        return bytes == (long)size;
    }
}

static int focus_guard_spawn(const char *executable, struct FocusGuard *guard) {
    int to_guard[2], from_guard[2];
    guard->pid = 0; guard->input = guard->output = -1;
    if (executable[0] != '/' || pipe(to_guard)) return 0;
    if (pipe(from_guard)) { close(to_guard[0]); close(to_guard[1]); return 0; }
    /* Do not inherit either endpoint through a subsequent exec in the caller. */
    int error = 0;
    for (unsigned i = 0; i < 2; i++)
        if (fcntl(to_guard[i], F_SETFD, FD_CLOEXEC) || fcntl(from_guard[i], F_SETFD, FD_CLOEXEC)) error = 1;
    posix_spawn_file_actions_t actions;
    int initialized = !posix_spawn_file_actions_init(&actions);
    if (!initialized) error = 1;
    if (!error) error = posix_spawn_file_actions_adddup2(&actions, to_guard[0], 0);
    if (!error) error = posix_spawn_file_actions_adddup2(&actions, from_guard[1], 1);
    if (!error) error = posix_spawn_file_actions_addopen(&actions, 2, "/dev/null", O_RDWR, 0);
    int descriptors[] = {to_guard[0], to_guard[1], from_guard[0], from_guard[1]};
    for (unsigned i = 0; !error && i < 4; i++) error = posix_spawn_file_actions_addclose(&actions, descriptors[i]);
    char *arguments[] = {(char *)executable, "guard", 0};
    char *environment[] = {"PATH=/usr/bin:/bin:/usr/sbin:/sbin", 0};
    if (!error) error = posix_spawn(&guard->pid, executable, &actions, 0, arguments, environment);
    if (initialized) posix_spawn_file_actions_destroy(&actions);
    close(to_guard[0]); close(from_guard[1]);
    if (error) { close(to_guard[1]); close(from_guard[0]); guard->pid = 0; return 0; }
    guard->input = to_guard[1]; guard->output = from_guard[0];
    return 1;
}

/* Called only after switching to the mobile user, in the independently spawned
 * guard. The lock covers snapshot, temporary enablement, query and restoration.
 * An overlapping query fails without changing settings or inheriting its peer's
 * temporary baseline. The lock file contains no data and is deliberately kept.
 */
static int focus_guard_run(const char *lock_path, unsigned char (*app)(void),
                           unsigned char (*automation)(void), void (*set_app)(unsigned char),
                           void (*set_auto)(unsigned char)) {
    signal(SIGPIPE, SIG_IGN);
    if (setsid() < 0) return 8;
    char command = 0;
    if (!focus_guard_receive(0, &command, 1, TTT_FOCUS_GUARD_WAIT_MS) || command != 'R') return 9;
    struct FocusGuardState state = {1, 1, 0, 0, 0, 0};
    int lock = open(lock_path, O_RDWR | O_CREAT | O_NOFOLLOW, 0600);
    if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB)) {
        if (lock >= 0) close(lock);
        return write(1, &state, sizeof(state)) == sizeof(state) ? 10 : 12;
    }
    state.before_app = !!app(); state.before_auto = !!automation();
    if (!state.before_app) set_app(1);
    if (!state.before_auto) set_auto(1);
    state.after_app = !!app(); state.after_auto = !!automation();
    state.status = state.after_app && state.after_auto ? 0 : 2;
    int ready = write(1, &state, sizeof(state)) == sizeof(state);
    int completed = ready && !state.status
        && focus_guard_receive(0, &command, 1, TTT_FOCUS_GUARD_WAIT_MS) && command == 'D';
    if (!state.before_auto) set_auto(0);
    if (!state.before_app) set_app(0);
    state.after_app = !!app(); state.after_auto = !!automation();
    state.status = state.after_app != state.before_app || state.after_auto != state.before_auto ? 3
        : completed ? 0 : 4;
    int sent = write(1, &state, sizeof(state)) == sizeof(state);
    close(lock);
    return !sent ? 12 : state.status ? 11 : 0;
}

static int focus_guard_begin(struct FocusGuard *guard, struct FocusGuardState *state) {
    focus_guard_last_read = (struct FocusGuardReadTrace){0, 0, 0, 0, -2};
    focus_guard_trace.begin_started = focus_guard_now();
    focus_guard_trace.begin_write = write(guard->input, "R", 1);
    focus_guard_trace.begin_errno = focus_guard_trace.begin_write < 0 ? focus_guard_errno : 0;
    focus_guard_trace.begin_received = focus_guard_trace.begin_write == 1
        && focus_guard_receive(guard->output, state, sizeof(*state), TTT_FOCUS_GUARD_WAIT_MS);
    focus_guard_trace.begin_read = focus_guard_last_read;
    focus_guard_trace.begin_valid = focus_guard_trace.begin_received
        && state->protocol == 1 && !state->status
        && (state->before_app == 0 || state->before_app == 1)
        && (state->before_auto == 0 || state->before_auto == 1)
        && state->after_app == 1 && state->after_auto == 1;
    focus_guard_trace.begin_finished = focus_guard_now();
    return focus_guard_trace.begin_valid;
}

static int focus_guard_finish(struct FocusGuard *guard, struct FocusGuardState *state) {
    focus_guard_last_read = (struct FocusGuardReadTrace){0, 0, 0, 0, -2};
    focus_guard_trace.finish_started = focus_guard_now();
    focus_guard_trace.finish_write = write(guard->input, "D", 1);
    focus_guard_trace.finish_errno = focus_guard_trace.finish_write < 0 ? focus_guard_errno : 0;
    close(guard->input); guard->input = -1;
    focus_guard_trace.finish_received = focus_guard_trace.finish_write == 1
        && focus_guard_receive(guard->output, state, sizeof(*state), 4000);
    focus_guard_trace.finish_read = focus_guard_last_read;
    focus_guard_trace.received_at = focus_guard_now();
    focus_guard_trace.final_protocol = state->protocol;
    focus_guard_trace.final_status = state->status;
    int complete = focus_guard_trace.finish_received && state->protocol == 1 && !state->status;
    focus_guard_trace.finish_valid = complete;
    close(guard->output); guard->output = -1;
    int status = -1;
    focus_guard_trace.wait_pid = waitpid(guard->pid, &status, 0);
    focus_guard_trace.wait_errno = focus_guard_trace.wait_pid < 0 ? focus_guard_errno : 0;
    focus_guard_trace.child_status = status;
    focus_guard_trace.reaped_at = focus_guard_now();
    if (focus_guard_trace.wait_pid != guard->pid || status != 0) complete = 0;
    return complete;
}
