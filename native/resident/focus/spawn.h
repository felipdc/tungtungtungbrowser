static int spawn_owned(const char *executable, const char *log_path, const char *guardian) {
    if (executable[0] != '/' || log_path[0] != '/' || equal(executable, log_path)) return 2;
    void *library = dlopen("/usr/lib/libSystem.B.dylib", 2);
    if (!library) return 7;
    int (*actions_init)(void **) = dlsym(library, "posix_spawn_file_actions_init");
    int (*actions_destroy)(void **) = dlsym(library, "posix_spawn_file_actions_destroy");
    int (*add_open)(void **, int, const char *, int, unsigned short) = dlsym(library, "posix_spawn_file_actions_addopen");
    int (*add_dup)(void **, int, int) = dlsym(library, "posix_spawn_file_actions_adddup2");
    int (*add_close)(void **, int) = dlsym(library, "posix_spawn_file_actions_addclose");
    int (*attributes_init)(void **) = dlsym(library, "posix_spawnattr_init");
    int (*attributes_destroy)(void **) = dlsym(library, "posix_spawnattr_destroy");
    int (*set_flags)(void **, short) = dlsym(library, "posix_spawnattr_setflags");
    int (*set_group)(void **, int) = dlsym(library, "posix_spawnattr_setpgroup");
    int (*spawn)(int *, const char *, void **, void **, char *const *, char *const *) = dlsym(library, "posix_spawn");
    if (!actions_init || !actions_destroy || !add_open || !add_dup || !add_close
            || !attributes_init || !attributes_destroy || !set_flags || !set_group || !spawn) return 7;
    /* Darwin O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW. Never replace a log or
     * follow a pre-existing link, including after an uncertain earlier launch.
     */
    int log = open(log_path, 1 | 0x200 | 0x800 | 0x100, 0600);
    if (log < 3) { if (log >= 0) close(log); return 8; }
    void *actions = 0, *attributes = 0;
    int error = actions_init(&actions), pid = 0;
    int actions_ready = !error, attributes_ready = 0;
    if (!error) { error = attributes_init(&attributes); attributes_ready = !error; }
    if (!error) error = add_open(&actions, 0, "/dev/null", 0, 0);
    if (!error) error = add_dup(&actions, log, 1);
    if (!error) error = add_dup(&actions, log, 2);
    if (!error) error = add_close(&actions, log);
    /* POSIX_SPAWN_CLOEXEC_DEFAULT | POSIX_SPAWN_SETPGROUP; a zero process
     * group makes the child its leader, independent of the short-lived caller.
     */
    if (!error) error = set_flags(&attributes, 0x4002);
    if (!error) error = set_group(&attributes, 0);
    char *arguments[] = {(char *)executable, "serve", (char *)guardian, 0};
    char *environment[] = {"PATH=/usr/bin:/bin:/usr/sbin:/sbin", 0};
    if (!error) error = spawn(&pid, executable, &actions, &attributes, arguments, environment);
    int actions_error = actions_ready ? actions_destroy(&actions) : 0;
    int attributes_error = attributes_ready ? attributes_destroy(&attributes) : 0;
    close(log);
    printf("{\"protocol\":2,\"status\":\"%s\",\"pid\":%d,\"boot_uuid\":\"%s\",\"port\":1347,"
           "\"spawn_error\":%d,\"actions_destroy_error\":%d,\"attributes_destroy_error\":%d}\n",
           error ? "spawn_failed" : "spawned", pid, boot, error, actions_error, attributes_error);
    fflush(0);
    return error ? 9 : 0;
}
