/* Check the owning helper identity before dispatch, including after a reboot
 * or replacement listener. A stale client must never send a key to a new owner. */
static const char *owned_key(const char *input, const char *prefix) {
    while (*prefix && *input == *prefix) { input++; prefix++; }
    return *prefix ? 0 : input;
}
