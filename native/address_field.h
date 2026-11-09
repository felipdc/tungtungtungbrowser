/* SPDX-License-Identifier: MIT
 * Read one complete native application element list. A partial traversal must
 * never establish that the URL field is unique. The caller owns the AX guard.
 */
struct AddressFieldAPI {
    int (*copy)(const void *, const void *, const void **);
    int (*owner)(const void *, int *);
    int (*timeout)(const void *, float);
    unsigned long (*type)(const void *), (*array_type)(void), (*element_type)(void), (*string_type)(void);
    long (*count)(const void *);
    const void *(*at)(const void *, long), *(*retain)(const void *);
    void (*release)(const void *);
    unsigned char (*string)(const void *, char *, long, unsigned);
    double (*now)(void);
};
struct AddressFieldScan { long elements, visited, matches, foreign; int complete; };

static int address_field_name_equal(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return *a == *b;
}

static int address_field_find(struct AddressFieldAPI *api, const void *root, int pid,
                              const void **found, struct AddressFieldScan *scan) {
    *found = 0;
    *scan = (struct AddressFieldScan){0};
    double start = api->now();
    if (start < 0) return -1;
    const void *list = 0, *match = 0;
    int owner = 0, error = api->owner(root, &owner);
    if (error || owner != pid) return error ? error : -2;
    error = api->timeout(root, 1.0f);
    if (!error) error = api->copy(root, (const void *)3015, &list);
    if (error || !list) { if (list) api->release(list); return error ? error : -3; }
    if (api->type(list) != api->array_type()) { error = -4; goto done; }
    scan->elements = api->count(list);
    if (scan->elements < 0 || scan->elements > 512) { error = -5; goto done; }
    for (long i = 0; i < scan->elements; i++) {
        double now = api->now();
        if (now < start || now - start >= 8.0) { error = -6; goto done; }
        const void *item = api->at(list, i), *identifier = 0;
        if (!item || api->type(item) != api->element_type()) { error = -7; goto done; }
        error = api->owner(item, &owner);
        if (error || owner <= 1) { if (!error) error = -2; goto done; }
        scan->visited++;
        /* The application list includes WebContent-owned page elements.
         * Only the requested Safari process can own its native address control. */
        if (owner != pid) { scan->foreign++; continue; }
        error = api->timeout(item, 0.25f);
        if (error) goto done;
        error = api->copy(item, (const void *)5019, &identifier);
        if (error == -25212 && !identifier) { error = 0; continue; }
        if (error || !identifier) { if (identifier) api->release(identifier); if (!error) error = -8; goto done; }
        char name[256] = {0};
        int valid = api->type(identifier) == api->string_type()
            && api->string(identifier, name, sizeof(name), 0x08000100);
        api->release(identifier);
        if (!valid) { error = -9; goto done; }
        if (!address_field_name_equal(name, "URL")
                && !address_field_name_equal(name, "TabBarItemTitle")) continue;
        scan->matches++;
        if (scan->matches != 1) { error = -10; goto done; }
        match = item;
    }
    double end = api->now();
    if (end < start || end - start >= 8.0) { error = -6; goto done; }
    scan->complete = 1;
    if (match) *found = api->retain(match);
done:
    api->release(list);
    return error;
}
