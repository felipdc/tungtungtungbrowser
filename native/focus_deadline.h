/* SPDX-License-Identifier: MIT
 * Each caller phase owns a finite alarm. The independent restoration guard
 * never calls these functions and must remain able to outlive its caller.
 */
extern unsigned alarm(unsigned);
static void focus_deadline_setup(void) { alarm(20); }
static void focus_deadline_query(void) { alarm(20); }
static void focus_deadline_restore(void) { alarm(10); }
