/* Exercise the production pipe/ownership/deadline logic with file preferences. */
#define _POSIX_C_SOURCE 200809L
#define TTT_FOCUS_GUARD_READY_WAIT_MS 1000
#ifndef TTT_LEASE_LIFETIME_MS
#define TTT_LEASE_LIFETIME_MS 1500
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../native/focus_guard.h"
#include "../native/resident/lease/reusable_guard.h"

static unsigned char setting(const char *path) {
    int fd = open(path, O_RDONLY); char v = '0';
    if (fd < 0 || read(fd, &v, 1) != 1) _exit(70);
    close(fd); return v == '1';
}
static void set_setting(const char *path, unsigned char value) {
    int log = open("writes", O_WRONLY | O_APPEND | O_CREAT, 0600);
    char v = value ? '1' : '0';
    if (log < 0 || write(log, path, strlen(path)) < 0 || write(log, &v, 1) != 1 || write(log, "\n", 1) != 1) _exit(72);
    close(log);
    if (access(value ? "fail-enable" : "fail-restore", F_OK) == 0) return;
    int fd = open(path, O_WRONLY | O_TRUNC);
    if (fd < 0 || write(fd, &v, 1) != 1) _exit(71);
    close(fd);
}
static unsigned char app(void) { return setting("application"); }
static unsigned char automation(void) { return setting("automation"); }
static void set_app(unsigned char v) { set_setting("application", v); }
static void set_auto(unsigned char v) { set_setting("automation", v); }

int main(int argc, char **argv) {
    if (argc != 2 || !focus_guard_reset_signals()) return 2;
    if (!strcmp(argv[1], "guard")) return lease_guard_run("settings.lock", app, automation, set_app, set_auto);
    signal(SIGPIPE, SIG_IGN);
    struct FocusGuard g; struct FocusGuardState s = {0};
    if (!focus_guard_spawn(argv[0], &g)) return 3;
    if (!focus_guard_begin(&g, &s)) {
        close(g.input); close(g.output); waitpid(g.pid, 0, 0); return 4;
    }
    int before_app = s.before_app, before_auto = s.before_auto, active = 1;
    printf("{\"guard_pid\":%d,\"before\":[%d,%d]}\n", g.pid, before_app, before_auto); fflush(stdout);
    char command;
    while (read(0, &command, 1) == 1) {
        if (command == 'D') {
            int done = focus_guard_finish(&g, &s);
            printf("{\"closed\":%s,\"status\":%d,\"after\":[%d,%d]}\n", done ? "true" : "false", s.status, s.after_app, s.after_auto); fflush(stdout);
            return done ? 0 : 8;
        }
        int next = command == 'A' ? 1 : command == 'P' ? 0 : active;
        int ok = lease_guard_command(&g, command, &s, before_app, before_auto, next);
        printf("{\"ok\":%s,\"status\":%d,\"after\":[%d,%d]}\n", ok ? "true" : "false", s.status, s.after_app, s.after_auto); fflush(stdout);
        if (!ok) { close(g.input); close(g.output); waitpid(g.pid, 0, 0); return 7; }
        active = next;
    }
    return 5;  /* Owner loss: the independently running guardian sees pipe EOF. */
}
