/*
 * Startup and trap entry for Ibex.
 *
 * Image layout from boot_addr (Ibex docs, "Exceptions and Interrupts"):
 *   0x00        exception entry (mtvec base; all exceptions)
 *   0x04..0x78  interrupt ID n at 4n (vectored mode is the only mode)
 *   0x7C        NMI
 *   0x80        reset: Ibex starts here (boot_addr[31:8] concatenated with 0x80)
 *   0x84..0x93  image header (fw_image_hdr_t), filled by fw/scripts/mkimage.py
 * The vector entries must be 4-byte jumps, so this part is assembled with
 * .option norvc.
 */
#include "fw.h"

#if defined(__riscv_32e)
#define NSAVE 10
#else
#define NSAVE 16
#endif
#define FRAME (((NSAVE * 4) + 15) & ~15)

    .section .vectors, "ax"
    .option push
    .option norvc
    .globl _vectors
_vectors:
    j _trap_exception          /* 0x00 */
    .rept 30
    j _trap_irq                /* 0x04 .. 0x78 */
    .endr
    j _trap_nmi                /* 0x7C */
_reset:
    j _start                   /* 0x80 */
    .option pop

    .globl fw_image_hdr
    .type fw_image_hdr, @object
fw_image_hdr:                  /* 0x84 */
    .word FW_IMAGE_MAGIC
    .word 0                    /* length: mkimage.py */
    .word 0                    /* crc32:  mkimage.py */
#ifdef FW_BUILD_ID
    .word FW_BUILD_ID
#else
    .word 0
#endif
    .size fw_image_hdr, 16

    .section .text.start, "ax"
    .globl _start
_start:
    /* Interrupts are off after reset (Ibex docs); make it explicit. */
    csrci mstatus, 8
    csrw  mie, zero
    /* gp first, with relaxation off: the linker may rewrite later address
     * loads (including la sp below) as gp-relative. */
    .option push
    .option norelax
    la    gp, __global_pointer$
    .option pop
    la    sp, __stack_top
    la    t0, _vectors
    ori   t0, t0, 1            /* MODE = vectored (read-only on Ibex) */
    csrw  mtvec, t0

    /* Image CRC and dirty-restart checks. C, stack only: .bss is not
     * zeroed yet and must not be used. Panics on failure. */
    call  fw_boot_checks

    /* Zero .bss (word aligned by the linker script). */
    la    t0, __bss_start
    la    t1, __bss_end
1:  bgeu  t0, t1, 2f
    sw    zero, 0(t0)
    addi  t0, t0, 4
    j     1b
2:
    /* Paint the stack for high-water measurement, then set the guard word.
     * Nothing live is on the stack here (fw_boot_checks has returned). */
    la    t0, __stack_limit
    li    t2, 0x57ACC0DE       /* guard */
    sw    t2, 0(t0)
    addi  t0, t0, 4
    li    t2, 0xA5A5A5A5       /* paint */
3:  bgeu  t0, sp, 4f
    sw    t2, 0(t0)
    addi  t0, t0, 4
    j     3b
4:
    call  fw_runtime_init
    call  main
    call  fw_exit              /* a0 = main's return value; never returns */
5:  j     5b

/* ---- Exceptions and NMI: fatal. Switch to the panic stack first so that a
 * corrupted or overflowed sp cannot cause a second fault, and pass the
 * original sp and ra for the status block. */
    .section .text, "ax"
    .align 2
_trap_exception:
    csrw  mscratch, sp
    la    sp, __panic_stack_top
    csrr  a0, mscratch         /* sp at the trap */
    mv    a1, ra               /* ra at the trap */
    j     fw_trap_exception    /* noreturn */

    .align 2
_trap_nmi:
    csrw  mscratch, sp
    la    sp, __panic_stack_top
    csrr  a0, mscratch
    mv    a1, ra
    j     fw_trap_nmi          /* noreturn */

/* ---- Maskable interrupts: save caller-saved registers, dispatch in C. */
    .align 2
_trap_irq:
    addi  sp, sp, -FRAME
    sw    ra,  0(sp)
    sw    t0,  4(sp)
    sw    t1,  8(sp)
    sw    t2, 12(sp)
    sw    a0, 16(sp)
    sw    a1, 20(sp)
    sw    a2, 24(sp)
    sw    a3, 28(sp)
    sw    a4, 32(sp)
    sw    a5, 36(sp)
#if !defined(__riscv_32e)
    sw    a6, 40(sp)
    sw    a7, 44(sp)
    sw    t3, 48(sp)
    sw    t4, 52(sp)
    sw    t5, 56(sp)
    sw    t6, 60(sp)
#endif
    /* Stack overflow check: the frame must lie above the guard word. */
    la    t0, __stack_limit
    bleu  sp, t0, _irq_stack_overflow
    csrr  a0, mcause
    call  fw_irq_dispatch
    lw    ra,  0(sp)
    lw    t0,  4(sp)
    lw    t1,  8(sp)
    lw    t2, 12(sp)
    lw    a0, 16(sp)
    lw    a1, 20(sp)
    lw    a2, 24(sp)
    lw    a3, 28(sp)
    lw    a4, 32(sp)
    lw    a5, 36(sp)
#if !defined(__riscv_32e)
    lw    a6, 40(sp)
    lw    a7, 44(sp)
    lw    t3, 48(sp)
    lw    t4, 52(sp)
    lw    t5, 56(sp)
    lw    t6, 60(sp)
#endif
    addi  sp, sp, FRAME
    mret

_irq_stack_overflow:
    addi  a0, sp, FRAME        /* sp before the frame */
    lw    a1, 0(sp)            /* ra (may be below the limit; best effort) */
    la    sp, __panic_stack_top
    j     fw_trap_stack_overflow
