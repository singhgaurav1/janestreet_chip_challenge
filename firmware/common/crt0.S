/* Startup: vector table at 0x00, reset entry at 0x80 (Ibex boot_addr = 0). */
    .section .vectors, "ax"
    .option push
    .option norvc               /* every vector entry is one 4-byte jump */
    .global __vectors
__vectors:
    .rept 32
    j   __trap
    .endr
    .option pop

    .section .text.start, "ax"
    .global _start
_start:
    la    sp, __stack_top
    la    t0, __bss_start
    la    t1, __bss_end
1:  bgeu  t0, t1, 2f
    sw    zero, 0(t0)
    addi  t0, t0, 4
    j     1b
2:  call  main
    /* main returned: report its value in SCRATCH, then sleep forever */
    li    t0, 0x10000018
    sw    a0, 0(t0)
3:  wfi
    j     3b

/* Any trap: write 0xDEAD0000 | mcause to SCRATCH and stop. */
    .text
__trap:
    csrr  t1, mcause
    li    t0, 0xDEAD0000
    or    t1, t1, t0
    li    t0, 0x10000018
    sw    t1, 0(t0)
4:  j     4b
