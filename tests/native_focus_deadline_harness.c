/* SPDX-License-Identifier: MIT
 * Real process/pipe/lock lifecycle using production deadlines and guard code.
 * Scale caller alarms tenfold for tests; never arm an alarm in the guard.
 */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static unsigned scaled_alarm(unsigned seconds) { return alarm(seconds / 10); }
#define alarm scaled_alarm
#include "../native/focus_deadline.h"
#undef alarm
#include "../native/focus_guard.h"

static void delay(const char *name) {
    FILE *file = fopen(name, "r");
    int milliseconds = 0;
    if (!file || fscanf(file, "%d", &milliseconds) != 1) _exit(70);
    fclose(file);
    struct timespec duration = {milliseconds / 1000, (milliseconds % 1000) * 1000000L};
    while (nanosleep(&duration, &duration) && errno == EINTR) {}
}
static unsigned char setting(const char *path) {
    int fd = open(path, O_RDONLY);
    char value = '0';
    if (fd < 0 || read(fd, &value, 1) != 1) _exit(71);
    close(fd);
    return value == '1';
}
static void set_setting(const char *path, unsigned char value) {
    if (!strcmp(path, "automation")) delay(value ? "setup-ms" : "restore-ms");
    int fd = open(path, O_WRONLY | O_TRUNC);
    char text = value ? '1' : '0';
    if (fd < 0 || write(fd, &text, 1) != 1) _exit(72);
    close(fd);
}
static unsigned char app(void) { return setting("application"); }
static unsigned char automation(void) { return setting("automation"); }
static void set_app(unsigned char value) { set_setting("application", value); }
static void set_auto(unsigned char value) { set_setting("automation", value); }

int main(int argc, char **argv) {
    if (!focus_guard_reset_signals()) return 9;
    if (argc != 2) return 2;
    if (!strcmp(argv[1], "guard")) {
        int result = focus_guard_run("settings.lock", app, automation, set_app, set_auto);
        delay("reap-ms");
        int fd = open("guard-finished", O_WRONLY | O_CREAT, 0600);
        if (fd < 0) return 7;
        close(fd);
        return result;
    }
    focus_deadline_setup();
    signal(SIGPIPE, SIG_IGN);
    struct FocusGuard guard;
    struct FocusGuardState state = {0};
    if (!focus_guard_spawn(argv[0], &guard)) return 3;
    printf("{\"guard_pid\":%d}\n", guard.pid); fflush(stdout);
    if (!focus_guard_begin(&guard, &state)) return 4;
    int phased = !strcmp(argv[1], "phased");
    if (phased) focus_deadline_query();
    delay("query-ms");
    if (phased) focus_deadline_restore();
    if (!focus_guard_finish(&guard, &state)) return 8;
    printf("{\"restored\":true,\"after\":[%d,%d]}\n", state.after_app, state.after_auto);
    return 0;
}
