// Reset code and the exception vector table for FreeRTOS on the CSS machine.
//
// The machine resets at address 0 in EL1 with SP_EL1 selected and all interrupts masked.
// .data and .bss need no setup: the machine starts with the .data image already in RAM and
// the rest of RAM zeroed.

.section .reset, "ax"
.global _start
_start:
    ldr     x0, =__stack_top
    mov     sp, x0
    ldr     x0, =_freertos_vector_table
    msr     vbar_el1, x0
    bl      main
1:  b       1b

.section .vectors, "ax"

// The port installs this table again when the scheduler starts. Tasks run at EL1 on SP_EL0,
// so their SVCs and interrupts arrive at the "current EL with SP0" slots.
.macro slot target
    .balign 0x80
    b       \target
.endm

    .balign 0x800
.global _freertos_vector_table
_freertos_vector_table:
    slot FreeRTOS_SWI_Handler           // 0x000 sync, EL1t
    slot FreeRTOS_IRQ_Handler           // 0x080 IRQ, EL1t
    slot fault                          // 0x100 FIQ, EL1t
    slot fault                          // 0x180 SError, EL1t
    slot fault                          // 0x200 sync, EL1h
    slot FreeRTOS_IRQ_Handler           // 0x280 IRQ, EL1h
    slot fault                          // 0x300 FIQ, EL1h
    slot fault                          // 0x380 SError, EL1h

fault:
    mrs     x0, esr_el1
    mrs     x1, elr_el1
    b       vFault

// const char *term_write(const char *s, int stop): sends s to the terminal up to its NUL or the
// first `stop` character and returns where it stopped. Hand-written because every character costs
// visible time: this loop is 5 instructions with one taken branch.
.section .text.term_write, "ax"
.global term_write
term_write:
    mov     x2, #0x9000
    ldrb    w3, [x0]
    cmp     w3, w1
    ccmp    w3, #0, #4, ne              // Z set when the char is `stop` or NUL
    b.eq    2f
1:  strb    w3, [x2]
    ldrb    w3, [x0, #1]!
    cmp     w3, w1
    ccmp    w3, #0, #4, ne
    b.ne    1b
2:  ret
