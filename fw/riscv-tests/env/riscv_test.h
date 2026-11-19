// riscv-tests environment for Ibex on the firmware testbench (test/fw).
//
// Same pass/fail protocol as the upstream "p" environment (riscv-tests
// env/p/riscv_test.h), with the boot sequence changed for Ibex:
//   - Ibex fetches its first instruction at boot_addr + 0x80, and mtvec is
//     always vectored with a 256-byte aligned base (Ibex docs, "Exceptions
//     and Interrupts"). So the image starts with a 32-entry vector table.
//   - No satp, medeleg/mideleg, PMP or mnstatus writes: our Ibex config has
//     no S-mode, no PMP and no Smrnmi, and the upstream trick of pointing
//     mtvec at the next instruction does not work with a vectored mtvec.
// User-level tests (RVTEST_RV32U) run in U-mode, as upstream.

#ifndef FW_IBEX_RISCV_TEST_H
#define FW_IBEX_RISCV_TEST_H

#include "encoding.h"

#define RVTEST_RV32U .macro init; .endm
#define RVTEST_RV64U RVTEST_RV32U
#define RVTEST_RV32M .macro init; li a0, MSTATUS_MPP; csrs mstatus, a0; .endm
#define RVTEST_RV64M RVTEST_RV32M

#define TESTNUM gp

#ifdef RVTEST_CHIP
/* Full chip: no debug device. Fail code = (mcause << 7) | testnum-bits;
 * tohost = (mcause << 8) | 1, so mcause = code >> 7. */
#define UNEXPECTED_TRAP                                                 \
        csrr t5, mcause; slli TESTNUM, t5, 8; ori TESTNUM, TESTNUM, 1;
#else
/* Standalone testbench: print mcause, mepc, mtval on its debug device. */
#define UNEXPECTED_TRAP                                                 \
        li t6, 0x20000024;                                              \
        csrr t5, mcause; sw t5, 0(t6);                                  \
        csrr t5, mepc;   sw t5, 0(t6);                                  \
        csrr t5, mtval;  sw t5, 0(t6);                                  \
        ori TESTNUM, TESTNUM, 1337;
#endif

#define INIT_XREG                                                       \
  li x1, 0;  li x2, 0;  li x3, 0;  li x4, 0;  li x5, 0;  li x6, 0;      \
  li x7, 0;  li x8, 0;  li x9, 0;  li x10, 0; li x11, 0; li x12, 0;     \
  li x13, 0; li x14, 0; li x15, 0; li x16, 0; li x17, 0; li x18, 0;     \
  li x19, 0; li x20, 0; li x21, 0; li x22, 0; li x23, 0; li x24, 0;     \
  li x25, 0; li x26, 0; li x27, 0; li x28, 0; li x29, 0; li x30, 0;     \
  li x31, 0;

#define RVTEST_CODE_BEGIN                                               \
        .section .text.init;                                            \
        .align  8;                                                      \
        .weak mtvec_handler;                                            \
        .globl _start;                                                  \
        .option push;                                                   \
        .option norvc;                                                  \
_vectors:                                                               \
        /* 32 four-byte entries: exceptions at +0, IRQ n at +4n */      \
        .rept 32;                                                       \
        j trap_vector;                                                  \
        .endr;                                                          \
_start:                                                                 \
        /* boot_addr + 0x80 */                                          \
        j reset_vector;                                                 \
        .option pop;                                                    \
        .align 2;                                                       \
trap_vector:                                                            \
        csrr t5, mcause;                                                \
        li t6, CAUSE_USER_ECALL;                                        \
        beq t5, t6, write_tohost;                                       \
        li t6, CAUSE_MACHINE_ECALL;                                     \
        beq t5, t6, write_tohost;                                       \
        la t5, mtvec_handler;                                           \
        beqz t5, 1f;                                                    \
        jr t5;                                                          \
  1:    UNEXPECTED_TRAP                                                 \
  write_tohost:                                                         \
        sw TESTNUM, tohost, t5;                                         \
        j write_tohost;                                                 \
reset_vector:                                                           \
        INIT_XREG;                                                      \
        li TESTNUM, 0;                                                  \
        la t0, _vectors;                                                \
        csrw mtvec, t0;                                                 \
        csrwi mie, 0;                                                   \
        csrwi mstatus, 0;                                               \
        init;                                                           \
        la t0, 1f;                                                      \
        csrw mepc, t0;                                                  \
        csrr a0, mhartid;                                               \
        mret;                                                           \
1:

#define RVTEST_CODE_END unimp

#define RVTEST_PASS                                                     \
        fence;                                                          \
        li TESTNUM, 1;                                                  \
        li a7, 93;                                                      \
        li a0, 0;                                                       \
        ecall

#define RVTEST_FAIL                                                     \
        fence;                                                          \
1:      beqz TESTNUM, 1b;                                               \
        sll TESTNUM, TESTNUM, 1;                                        \
        or TESTNUM, TESTNUM, 1;                                         \
        li a7, 93;                                                      \
        addi a0, TESTNUM, 0;                                            \
        ecall

#define EXTRA_DATA

#define RVTEST_DATA_BEGIN                                               \
        EXTRA_DATA                                                      \
        .pushsection .tohost,"aw",@nobits;                              \
        .align 3; .global tohost; tohost: .dword 0; .size tohost, 8;    \
        .global fromhost; fromhost: .dword 0; .size fromhost, 8;        \
        .popsection;                                                    \
        .align 4; .global begin_signature; begin_signature:

#define RVTEST_DATA_END .align 4; .global end_signature; end_signature:

#endif
