#include <string.h>
#include "../native/resident/owned_key.h"
int main(void) {
    const char *prefix = "key 123 12345678-1234-4234-8234-123456789abc ";
    const char *valid = "key 123 12345678-1234-4234-8234-123456789abc cmd+l";
    if (!owned_key(valid, prefix) || strcmp(owned_key(valid, prefix), "cmd+l")) return 1;
    const char *invalid[] = {"key cmd+l", "key 124 12345678-1234-4234-8234-123456789abc cmd+l",
        "key 123 12345678-1234-4234-8234-123456789abd cmd+l", "key 123", "", "key 123 12345678-1234-4234-8234-123456789abcXcmd+l"};
    for (unsigned i = 0; i < sizeof(invalid) / sizeof(*invalid); i++)
        if (owned_key(invalid[i], prefix)) return 2;
    return 0;
}
