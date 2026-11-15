/* A single unused guardian may load its libraries while the reader is idle.
 * It receives no R command, takes no settings lock, and reads/writes no
 * preference until a fresh query takes ownership. The existing per-query
 * begin/finish protocol, guardian deadline, restoration and reap are unchanged.
 */
#ifndef WNOHANG
#define WNOHANG 1
#endif
#ifndef TTT_GUARD_POOL_MAX_AGE
#define TTT_GUARD_POOL_MAX_AGE 1750.0
#endif
struct FocusGuardPool {
    struct FocusGuard guard;
    double created;
    int retiring, spawned, reused, discarded, reaped, failed;
};
static int focus_pool_used_last;
static struct FocusGuardPool focus_pool = {{0, -1, -1}, 0, 0, 0, 0, 0, 0, 0};

static void focus_guard_pool_discard(void) {
    if (!focus_pool.guard.pid || focus_pool.retiring) return;
    close(focus_pool.guard.input); close(focus_pool.guard.output);
    focus_pool.guard.input = focus_pool.guard.output = -1;
    focus_pool.retiring = 1;
    focus_pool.discarded++;
}

static int focus_guard_pool_reap(void) {
    if (!focus_pool.guard.pid) return 1;
    int status = -1;
    int pid = waitpid(focus_pool.guard.pid, &status, WNOHANG);
    if (!pid || (pid < 0 && focus_guard_errno == 4)) return 0;
    if (pid != focus_pool.guard.pid || !focus_pool.retiring || status != (9 << 8))
        focus_pool.failed++;
    if (!focus_pool.retiring) {
        close(focus_pool.guard.input); close(focus_pool.guard.output);
    }
    focus_pool.guard = (struct FocusGuard){0, -1, -1};
    focus_pool.retiring = 0;
    focus_pool.reaped++;
    return 1;
}

static void focus_guard_pool_maintain(const char *executable) {
    if (focus_pool.guard.pid && !focus_pool.retiring
            && focus_guard_now() - focus_pool.created >= TTT_GUARD_POOL_MAX_AGE)
        focus_guard_pool_discard();
    if (!focus_guard_pool_reap() || focus_pool.failed >= 3) return;
    if (!focus_guard_spawn(executable, &focus_pool.guard)) {
        focus_pool.failed++; return;
    }
    focus_pool.created = focus_guard_now();
    focus_pool.spawned++;
}

static int focus_guard_pool_take(const char *executable, struct FocusGuard *guard) {
    focus_pool_used_last = 0;
    if (focus_pool.guard.pid && !focus_pool.retiring
            && focus_guard_now() - focus_pool.created < TTT_GUARD_POOL_MAX_AGE
            && !focus_guard_pool_reap()) {
        *guard = focus_pool.guard;
        focus_pool.guard = (struct FocusGuard){0, -1, -1};
        focus_pool.reused++;
        focus_pool_used_last = 1;
        return 1;
    }
    focus_guard_pool_discard();
    return focus_guard_spawn(executable, guard);
}

static int focus_guard_pool_close(void) {
    focus_guard_pool_discard();
    double end = focus_guard_now() + 10;
    while (!focus_guard_pool_reap()) {
        if (focus_guard_now() >= end) return 0;
        struct timespec delay = {0, 10000000};
        nanosleep(&delay, 0);
    }
    return !focus_pool.guard.pid;
}
