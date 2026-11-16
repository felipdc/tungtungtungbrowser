/* SPDX-License-Identifier: MIT
 * The independent guardian holds one ownership lock and one original snapshot.
 * A/P delimit complete native navigations; H is read-only. Neither requests nor
 * transitions renew the absolute lifetime. EOF/deadline still restore once.
 * Include after focus_guard.h; the initial R and final D keep its pipe ABI.
 */
#ifndef TTT_LEASE_LIFETIME_MS
#define TTT_LEASE_LIFETIME_MS 1900000
#endif

static int lease_values(struct FocusGuardState *s, int active,
                        unsigned char (*app)(void), unsigned char (*automation)(void)) {
    s->after_app = !!app(); s->after_auto = !!automation();
    return s->after_app == (active ? 1 : s->before_app)
        && s->after_auto == (active ? 1 : s->before_auto);
}

static int lease_restore(struct FocusGuardState *s,
                         unsigned char (*app)(void), unsigned char (*automation)(void),
                         void (*set_app)(unsigned char), void (*set_auto)(unsigned char)) {
    if (!s->before_auto && automation()) set_auto(0);
    if (!s->before_app && app()) set_app(0);
    return lease_values(s, 0, app, automation);
}

static int lease_guard_run(const char *path, unsigned char (*app)(void),
                           unsigned char (*automation)(void), void (*set_app)(unsigned char),
                           void (*set_auto)(unsigned char)) {
    signal(SIGPIPE, SIG_IGN);
    if (setsid() < 0) return 8;
    char command = 0;
    if (!focus_guard_receive(0, &command, 1, TTT_FOCUS_GUARD_READY_WAIT_MS) || command != 'R') return 9;
    struct FocusGuardState s = {1, 1, 0, 0, 0, 0};
    int lock = open(path, O_RDWR | O_CREAT | O_NOFOLLOW, 0600);
    if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB)) {
        if (lock >= 0) close(lock);
        return write(1, &s, sizeof(s)) == sizeof(s) ? 10 : 12;
    }
    double start = focus_guard_now();
    if (start < 0) { close(lock); return write(1, &s, sizeof(s)) == sizeof(s) ? 10 : 12; }
    double deadline = start + TTT_LEASE_LIFETIME_MS / 1000.0;
    s.before_app = !!app(); s.before_auto = !!automation();
    if (!s.before_app) set_app(1);
    if (!s.before_auto) set_auto(1);
    s.status = lease_values(&s, 1, app, automation) ? 0 : 2;
    int ready = write(1, &s, sizeof(s)) == sizeof(s);
    int active = 1, completed = 0, restore_attempted = 0, failure = s.status ? s.status : 4;
    while (ready && !s.status) {
        double now = focus_guard_now(), remaining = deadline - now;
        if (now < 0 || remaining <= 0) break;
        int wait_ms = (int)(remaining * 1000) + 1;
        if (!focus_guard_receive(0, &command, 1, wait_ms)) break;
        now = focus_guard_now(); if (now < 0 || now >= deadline) break;
        if (command == 'D') { completed = 1; break; }
        if (!lease_values(&s, active, app, automation)) { failure = 5; break; }
        if (command == 'P' && active) {
            restore_attempted = 1;
            if (!lease_restore(&s, app, automation, set_app, set_auto)) { failure = 3; break; }
            active = 0;
        } else if (command == 'A' && !active) {
            restore_attempted = 0;
            if (!s.before_app) set_app(1);
            if (!s.before_auto) set_auto(1);
            if (!lease_values(&s, 1, app, automation)) { failure = 2; break; }
            active = 1;
        } else if (command != 'H') { failure = 6; break; }
        ready = write(1, &s, sizeof(s)) == sizeof(s);
    }
    /* A failed pause has already attempted restoration. Preserve that failure;
     * do not turn its missing acknowledgement into another write attempt. */
    int restored = restore_attempted ? lease_values(&s, 0, app, automation)
        : lease_restore(&s, app, automation, set_app, set_auto);
    s.status = !restored ? 3 : completed ? 0 : failure;
    int sent = write(1, &s, sizeof(s)) == sizeof(s);
    close(lock);
    return !sent ? 12 : s.status ? 11 : 0;
}

static int lease_guard_command(struct FocusGuard *guard, char command,
                               struct FocusGuardState *state, int before_app, int before_auto,
                               int active) {
    /* A closed pipe carries no new state. Never expose a cached success. */
    *state = (struct FocusGuardState){0, -1, 0, 0, 0, 0};
    return write(guard->input, &command, 1) == 1
        && focus_guard_receive(guard->output, state, sizeof(*state), 10000)
        && state->protocol == 1 && !state->status
        && state->before_app == before_app && state->before_auto == before_auto
        && state->after_app == (active ? 1 : before_app)
        && state->after_auto == (active ? 1 : before_auto);
}
