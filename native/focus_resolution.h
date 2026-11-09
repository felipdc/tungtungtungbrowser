/* SPDX-License-Identifier: MIT
 * Resolve native focus and, only for a missing/unavailable first responder,
 * discover a complete unique address control while the caller holds one guard.
 * No UI input is sent. The process alarm remains the hard execution bound.
 */
struct FocusResolutionAPI {
    int (*focused)(void *, const void **);
    int (*address)(void *, const void **, struct AddressFieldScan *);
    void (*release)(const void *);
    double (*now)(void);
    int (*settle)(void *);
};
struct FocusResolutionAttempt { int error; double seconds; struct AddressFieldScan scan; };
struct FocusResolution {
    int source, first_error, first_present, attempts;
    double first_seconds, total_seconds;
    struct FocusResolutionAttempt address[3];
};

static int focus_resolve(struct FocusResolutionAPI *api, void *context,
                         const void **found, struct AddressFieldScan *scan,
                         struct FocusResolution *trace) {
    *found = 0; *scan = (struct AddressFieldScan){0};
    *trace = (struct FocusResolution){0};
    double start = api->now(), now = start;
    if (start < 0) return -30;
    trace->source = 1;
    int error = api->focused(context, found);
    now = api->now();
    trace->first_error = error; trace->first_present = *found != 0;
    trace->first_seconds = now - start; trace->total_seconds = now - start;
    if (now < start || now - start >= 10.0) goto budget;
    /* A returned responder, even with an error or another identifier, must be
     * validated as that result. It cannot authorize discovery of another field. */
    if (*found || (error && error != -25212 && error != -25216)) return error;
    trace->source = 2;
    for (int attempt = 0; attempt < 3; attempt++) {
        if (attempt && api->settle(context)) return -31;
        now = api->now();
        trace->total_seconds = now - start;
        if (now < start || now - start >= 10.0) goto budget;
        double before = now;
        error = api->address(context, found, scan);
        now = api->now();
        trace->address[attempt] = (struct FocusResolutionAttempt){error, now - before, *scan};
        trace->attempts++; trace->total_seconds = now - start;
        if (now < before || now - start >= 10.0) goto budget;
        if (*found) return error;
        /* Repeat only a complete scan with no matching control or the observed unavailable root
         * list. Partial traversal, ambiguity and other failures remain errors. */
        int absent = error == 0 && scan->complete && scan->elements >= 0 && scan->elements <= 512
            && scan->visited == scan->elements && scan->matches == 0
            && scan->foreign >= 0 && scan->foreign <= scan->visited;
        int unavailable = error == -25216 && !scan->complete && scan->elements == 0
            && scan->visited == 0 && scan->matches == 0 && scan->foreign == 0;
        if (!absent && !unavailable) return error;
    }
    return error;
 budget:
    if (*found) { api->release(*found); *found = 0; }
    return -30;
}
