/* Exercise the installed library on real connected and disconnected sockets. */
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

extern int usbmuxd_send(int, const char *, uint32_t, uint32_t *);

int main(void)
{
    int pair[2];
    uint32_t sent = 0;
    char received[4] = {0};
    signal(SIGPIPE, SIG_IGN);
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, pair)) return 2;
    int result = usbmuxd_send(pair[0], "test", 4, &sent);
    if (result || sent != 4 || read(pair[1], received, 4) != 4 ||
        memcmp(received, "test", 4)) return 3;
    close(pair[1]);
    result = usbmuxd_send(pair[0], "test", 4, &sent);
    close(pair[0]);
    printf("Disconnected send: result=%d bytes=%u\n", result, sent);
    return result < 0 && sent == 0 ? 0 : 1;
}
