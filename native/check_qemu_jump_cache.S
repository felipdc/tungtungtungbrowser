// SPDX-License-Identifier: MIT
// Two CPUs verify newly written instructions after each acknowledged update.
// No firmware, devices, filesystem or network are required.
.text
.global _start
_start:
    mrs x0, mpidr_el1
    and x28, x0, #0xff
    adrp x20, control
    add x20, x20, :lo12:control
    adrp x24, target
    add x24, x24, :lo12:target
    cbnz x28, reader

    // Populate the writer's jump cache before the first code modification.
    blr x24
    cbnz x0, failed
    mov x0, #3
    movk x0, #0xc400, lsl #16
    mov x1, #1
    adr x2, _start
    mov x3, #0
    hvc #0
    cbnz x0, failed
    mov x22, #1
writer:
    // Encode MOVZ X0, #iteration; retain the following RET.
    lsl w9, w22, #5
    mov w10, #0xd2800000
    orr w9, w9, w10
    str w9, [x24]
    dc cvau, x24
    dsb ish
    ic ivau, x24
    dsb ish
    isb
    blr x24
    cmp x0, x22
    b.ne failed
    stlr x22, [x20]
    add x21, x20, #8
writer_wait:
    ldar x0, [x21]
    cmp x0, x22
    b.eq acknowledged
    ldr x0, [x20, #16]
    cbnz x0, failed
    yield
    b writer_wait
acknowledged:
    add x22, x22, #1
    cmp x22, #257
    b.ne writer
    adr x1, passed_message
    mov x0, #4
    hlt #0xf000
    adr x1, passed_exit
    mov x0, #0x20
    hlt #0xf000
    b parked

reader:
    mov x23, #0
reader_wait:
    ldar x22, [x20]
    cmp x22, x23
    b.eq reader_yield
    isb
    blr x24
    cmp x0, x22
    b.ne reader_failed
    mov x23, x22
    add x21, x20, #8
    stlr x23, [x21]
    cmp x23, #256
    b.eq parked
reader_yield:
    yield
    b reader_wait
reader_failed:
    mov x0, #1
    str x0, [x20, #16]
parked:
    wfi
    b parked
failed:
    adr x1, failed_message
    mov x0, #4
    hlt #0xf000
    adr x1, failed_exit
    mov x0, #0x20
    hlt #0xf000
    b parked
.balign 64
control: .quad 0, 0, 0
passed_exit: .quad 0x20026, 0
failed_exit: .quad 0x20026, 1
passed_message: .asciz "SMC_TWO_CPU_PASS\n"
failed_message: .asciz "SMC_TWO_CPU_FAIL\n"
.balign 4096
target:
    mov x0, #0
    ret
