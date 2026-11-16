/* Transport-only adapter: run the production lease controller over a local
 * stream socket. All request parsing, ownership and guardian logic is real. */
#define _POSIX_C_SOURCE 200809L
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

struct Address { unsigned char len, family; unsigned short reserved; unsigned port, cid; };
int ttt_test_socket(int family, int type, int protocol) {
    if (family != 40 || type != 1 || protocol != 0) return -1;
    return socket(AF_UNIX, SOCK_STREAM, 0);
}
int ttt_test_bind(int fd, const void *address, unsigned size) {
    const struct Address *guest = address;
    if (size != sizeof(*guest) || guest->port != 1346 || guest->cid != 0xffffffffU) return -1;
    struct sockaddr_un local = {.sun_family = AF_UNIX};
    strcpy(local.sun_path, "controller.sock");
    return bind(fd, (struct sockaddr *)&local, sizeof(local));
}
int ttt_test_accept(int fd, void *address, unsigned *size) {
    if (*size != sizeof(struct Address)) return -1;
    int client = accept(fd, 0, 0);
    *(struct Address *)address = (struct Address){12, 40, 0, 1346, 2};
    return client;
}
int ttt_test_setsockopt(int fd, int level, int option, const void *value, unsigned size) {
    (void)fd; (void)level; (void)option; (void)value; (void)size;
    return 0;  /* Request deadlines use production poll(), not this adapter. */
}
int sysctlbyname(const char *name, void *value, unsigned long *size,
                 const void *new_value, unsigned long new_size) {
    if (strcmp(name, "kern.bootsessionuuid") || *size < 37 || new_value || new_size) return -1;
    memcpy(value, "12345678-1234-4234-8234-123456789abc", 37); *size = 37;
    return 0;
}
