// SPDX-License-Identifier: MIT
// Two-core handoffs with and without SEV, followed by unpaired WFE instructions.
.text
.global _start
_start:
.Lentry:
    mrs x0, mpidr_el1
    and x0, x0, #0xff
    cbnz x0, worker
    adr x20, shared
    str wzr, [x20]
    str wzr, [x20, #4]
    dsb sy
    mov x0, #3
    movk x0, #0xc400, lsl #16
    mov x1, #1
    adr x2, .Lentry
    mov x3, #0
    hvc #0
    cbnz x0, failed
    // Delay while both cores repeatedly execute WFE.
    mrs x21, cntfrq_el0
    lsr x21, x21, #1
    mrs x22, cntpct_el0
    add x22, x22, x21
1:  wfe
    mrs x0, cntpct_el0
    cmp x0, x22
    b.lo 1b
    mov w0, #1
    stlr w0, [x20]
    sev
    add x23, x20, #4
2:  ldar w0, [x23]
    cmp w0, #1
    b.eq 3f
    wfe
    b 2b
3:  mrs x22, cntpct_el0
    add x22, x22, x21
4:  wfe
    mrs x0, cntpct_el0
    cmp x0, x22
    b.lo 4b
    // A changed value without SEV must also eventually make progress.
    mov w0, #2
    stlr w0, [x20]
5:  ldar w0, [x23]
    cmp w0, #2
    b.eq passed
    wfe
    b 5b
worker:
    adr x20, shared
    add x23, x20, #4
    mov w21, #1
6:  ldxr w0, [x20]
    cmp w0, w21
    b.eq 7f
    wfe
    b 6b
7:  clrex
    stlr w21, [x23]
    sev
    add w21, w21, #1
    cmp w21, #3
    b.lo 6b
8:  wfi
    b 8b
passed:
    // Bounded backoff must not need a future SEV to return.
    wfe
    wfe
    wfe
    mov w2, #0
    b exit
failed:
    mov w2, #1
exit:
    adr x1, exit_block
    str x2, [x1, #8]
    mov x0, #0x20
    hlt #0xf000
    b .
.balign 8
shared: .quad 0
exit_block: .quad 0x20026, 0
