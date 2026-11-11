/* SPDX-License-Identifier: MIT
 * One bounded AX request for the five required attributes.
 * Returned values are borrowed from the owned result array. No partial fallback
 * or per-attribute query is performed when the batch fails. */
typedef const void *FocusAttrValue;
struct FocusAttributeAPI {
    FocusAttrValue (*number)(FocusAttrValue, int, const void *);
    FocusAttrValue (*array)(FocusAttrValue, const FocusAttrValue *, long, const void *);
    unsigned long (*type)(FocusAttrValue), (*array_type)(void), (*null_type)(void), (*value_type)(void);
    long (*count)(FocusAttrValue);
    FocusAttrValue (*at)(FocusAttrValue, long);
    unsigned char (*read_value)(FocusAttrValue, int, void *);
    void (*release)(FocusAttrValue);
    int (*copy)(FocusAttrValue, FocusAttrValue, unsigned long long, FocusAttrValue *);
};
struct FocusAttributeResult { FocusAttrValue values; int error; };
static int focus_attributes_read(const struct FocusAttributeAPI *a, FocusAttrValue element,
                                 struct FocusAttributeResult *r) {
    static const long long keys[5] = {2001, 2006, 5019, 2004, 2003};
    FocusAttrValue numbers[5] = {0}, arguments = 0;
    *r = (struct FocusAttributeResult){0, -25200};
    for (unsigned i=0; i<5; i++) {
        numbers[i] = a->number(0, 4, &keys[i]);
        if (!numbers[i]) goto done;
    }
    /* NULL callbacks: numbers remain owned here until the synchronous call
     * returns. The argument array neither retains nor releases those values. */
    arguments = a->array(0, numbers, 5, 0);
    if (!arguments) goto done;
    r->error = a->copy(element, arguments, 0, &r->values);
    if (!r->error && (!r->values || a->type(r->values) != a->array_type()
                      || a->count(r->values) != 5)) r->error = -25200;
 done:
    if (arguments) a->release(arguments);
    for (unsigned i=0; i<5; i++) if (numbers[i]) a->release(numbers[i]);
    if (r->error && r->values) { a->release(r->values); r->values = 0; }
    return r->error;
}
static int focus_attribute_value(const struct FocusAttributeAPI *a,
                                 const struct FocusAttributeResult *r,
                                 unsigned index, FocusAttrValue *value) {
    *value = 0;
    if (r->error) return r->error;
    if (!r->values || index >= 5) return -25200;
    FocusAttrValue v = a->at(r->values, index);
    if (!v || a->type(v) == a->null_type()) return -25212;
    if (a->type(v) == a->value_type()) {
        int error = 0;
        if (a->read_value(v, 5, &error)) return error ? error : -25200;
    }
    *value = v;
    return 0;
}
