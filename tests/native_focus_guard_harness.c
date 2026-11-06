/* SPDX-License-Identifier: MIT
 * Exercise the production process/pipe/lock lifecycle with file-backed
 * preferences. No Apple frameworks or guest binaries are used in these tests.
 */
#define _POSIX_C_SOURCE 200809L
#define TTT_FOCUS_GUARD_WAIT_MS 1000
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../native/focus_guard.h"

static unsigned char setting(const char *path) {
    int fd = open(path, O_RDONLY);
    char value = '0';
    if (fd < 0 || read(fd, &value, 1) != 1) _exit(70);
    close(fd);
    return value == '1';
}
static void set_setting(const char *path, unsigned char value) {
    if (!value && access("fail-restoration", F_OK) == 0) return;
    int fd = open(path, O_WRONLY | O_TRUNC);
    char text = value ? '1' : '0';
    if (fd < 0 || write(fd, &text, 1) != 1) _exit(71);
    close(fd);
}
static unsigned char app(void) { return setting("application"); }
static unsigned char automation(void) { return setting("automation"); }
static void set_app(unsigned char value) { set_setting("application", value); }
static void set_auto(unsigned char value) { set_setting("automation", value); }

int main(int argc, char **argv) {
    if (!focus_guard_reset_signals()) return 9;
    if (argc != 2) return 2;
    if (!strcmp(argv[1], "guard")) return focus_guard_run("settings.lock", app, automation, set_app, set_auto);
    signal(SIGPIPE, SIG_IGN);
    struct FocusGuard guard;
    struct FocusGuardState state;
    if (!focus_guard_spawn(argv[0], &guard)) return 3;
    if (!focus_guard_begin(&guard, &state)) return 4;
    printf("{\"ready\":true,\"guard_pid\":%d,\"before\":[%d,%d]}\n", guard.pid, state.before_app, state.before_auto);
    fflush(stdout);
    if (!strcmp(argv[1], "alarm")) {
        alarm(1);
        for (;;) pause();
    } else if (!strcmp(argv[1], "hold")) {
        char command;
        if (read(0, &command, 1) != 1) return 5;
    } else if (!strcmp(argv[1], "invalid")) {
        if (write(guard.input, "X", 1) != 1) return 6;
        close(guard.input);
        (void)waitpid(guard.pid, 0, 0);
        return 6;
    } else if (!strcmp(argv[1], "restore-fail")) {
        int fd = open("fail-restoration", O_WRONLY | O_CREAT, 0600);
        if (fd < 0) return 7;
        close(fd);
    }
    if (!focus_guard_finish(&guard, &state)) return 8;
    printf("{\"restored\":true,\"after\":[%d,%d]}\n", state.after_app, state.after_auto);
    return 0;
}
