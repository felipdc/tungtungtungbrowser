// SPDX-License-Identifier: MIT
// Two CPUs execute distinct banks of blocks and publish checked results.
// The host changes control.epoch only while the VM is paused.
.text
.global _start
.global control
.equ BLOCKS, 32768
_start:
    mrs x0, mpidr_el1
    and x28, x0, #0xff
    adrp x20, control
    add x20, x20, :lo12:control
    mov x22, #0
    cbnz x28, .Lworker
    mov x0, #3
    movk x0, #0xc400, lsl #16
    mov x1, #1
    adrp x2, _start
    add x2, x2, :lo12:_start
    mov x3, #0
    hvc #0
    cbz x0, .Lworker
    str x0, [x20, #24]
    b .
.Lworker:
    add x21, x20, #8
    add x21, x21, x28, lsl #3
.Lwait:
    yield
    ldar x23, [x20]
    cmp x23, x22
    b.eq .Lwait
    cmp x23, #255
    b.eq .Lexit
    mov x19, x23
    cbnz x28, .Lbank1
    bl bank0
    b .Lcheck
.Lbank1:
    bl bank1
.Lcheck:
    mov x24, #BLOCKS
    add x24, x23, x24
    cmp x19, x24
    b.eq .Lgood
    mov x0, #1
    add x1, x20, #24
    str x0, [x1, x28, lsl #3]
.Lgood:
    add x1, x20, #40
    str x19, [x1, x28, lsl #3]
    mov x22, x23
    stlr x22, [x21]
    b .Lwait
.Lexit:
    cbnz x28, .Lpark
    adr x1, exit_block
    mov x0, #0x20
    hlt #0xf000
.Lpark:
    wfi
    b .Lpark
.balign 64
control: .quad 1, 0, 0, 0, 0, 0, 0
exit_block: .quad 0x20026, 0
.balign 4096
bank0:
    .rept BLOCKS
    add x19, x19, #1
    b .+4
    .endr
    ret
.balign 4096
bank1:
    .rept BLOCKS
    add x19, x19, #1
    b .+4
    .endr
    ret
