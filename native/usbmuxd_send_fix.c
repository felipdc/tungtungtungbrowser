/* Host-side restore workaround for libusbmuxd returning positive errno from
 * usbmuxd_send. libimobiledevice treats only negative results as failures and
 * otherwise loops forever with zero bytes sent on a disconnected device.
 * Scope: firmware preparation only, never Safari or the iOS guest.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <pthread.h>
#include <stdint.h>

typedef int (*send_fn)(int, const char *, uint32_t, uint32_t *);
static send_fn original_send;
static pthread_once_t initialized = PTHREAD_ONCE_INIT;

static void resolve_original(void)
{
    original_send = (send_fn)dlsym(RTLD_NEXT, "usbmuxd_send");
}

int usbmuxd_send(int fd, const char *data, uint32_t length, uint32_t *sent)
{
    pthread_once(&initialized, resolve_original);
    if (!original_send) {
        if (sent) *sent = 0;
        return -ENOSYS;
    }
    int result = original_send(fd, data, length, sent);
    return result > 0 ? -result : result;
}
