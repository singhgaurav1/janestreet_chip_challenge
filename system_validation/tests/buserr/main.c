/*
 * System validation: bus decode errors (cocotb: test_sysval_buserr).
 *
 * Every address the bus does not map must fault instead of aliasing or
 * reading silently (docs/soc.md "Memory map"):
 *   - SRAM ends at 4 KiB: loads/stores/fetches above it fault;
 *   - sysctrl answers only its 7 registers; other offsets fault;
 *   - peripheral windows with no peripheral fault;
 *   - addresses outside 0x0... and 0x1000_xxxx fault.
 * Mapped addresses (including writes to read-only registers) must not.
 *
 * The test installs its own vectored trap table for the probes, so one run
 * covers every case: a probe returns mcause (0 = no fault). Load and store
 * probes resume after the faulting instruction; a fetch probe resumes at its
 * return address. The runtime's table is restored afterwards.
 */
#include <stdint.h>

#include "csr.h"
#include "fw.h"
#include "mmio.h"
#include "platform.h"

extern const uint32_t sv_vectors[];

__asm__(
    "  .section .text.sv_probe, \"ax\"\n"
    "  .option push\n"
    "  .option norvc\n"                         /* fixed 4-byte instructions */
    "  .balign 256\n"                           /* Ibex mtvec: 256-byte aligned */
    "  .globl sv_vectors\n"
    "sv_vectors:\n"
    "  .rept 32\n"
    "  j sv_exc\n"
    "  .endr\n"
    "sv_exc:\n"                                 /* a1 = mcause; t1 != 0: resume at ra */
    "  csrr a1, mcause\n"
    "  bnez t1, 1f\n"
    "  csrr t0, mepc\n"
    "  addi t0, t0, 4\n"
    "  csrw mepc, t0\n"
    "  mret\n"
    "1:\n"
    "  csrw mepc, ra\n"
    "  mret\n"
    "  .option pop\n"
    "  .text\n");

/* Probes: naked C functions (so the static stack check sees them; no frame).
 * Fixed 4-byte instructions; the handler clobbers only a1 and t0. */
#define PROBE_ATTR __attribute__((naked, noinline))

static PROBE_ATTR uint32_t sv_probe_load(uint32_t addr)
{
    (void)addr;
    __asm__ volatile(".option push\n.option norvc\n"
                     "li a1, 0\n li t1, 0\n lw a2, 0(a0)\n mv a0, a1\n ret\n"
                     ".option pop\n");
}

static PROBE_ATTR uint32_t sv_probe_store(uint32_t addr)   /* writes back what it read */
{
    (void)addr;
    __asm__ volatile(".option push\n.option norvc\n"
                     "li t1, 0\n lw a2, 0(a0)\n li a1, 0\n sw a2, 0(a0)\n mv a0, a1\n ret\n"
                     ".option pop\n");
}

static PROBE_ATTR uint32_t sv_probe_fetch(uint32_t addr)
{
    (void)addr;
    __asm__ volatile(".option push\n.option norvc\n"
                     "mv t2, ra\n li a1, 0\n li t1, 1\n jalr ra, 0(a0)\n"
                     "li t1, 0\n mv ra, t2\n mv a0, a1\n ret\n"
                     ".option pop\n");
}

enum { NONE = 0, FETCH = 1, LOAD = 5, STORE = 7 };   /* mcause values */

struct probe { uint32_t addr; uint8_t load, store; };

static const struct probe probes[] = {
    /* SRAM: last word maps, first word above faults */
    {0x00000FFCu, NONE, NONE},
    {0x00001000u, LOAD, STORE},
    {0x00002000u, LOAD, STORE},
    {0x0FFFFFFCu, LOAD, STORE},
    /* sysctrl: registers 0x00..0x18; ID and CYCLE are read-only (writes ignored) */
    {0x10000000u, NONE, NONE},
    {0x10000014u, NONE, NONE},
    {0x10000018u, NONE, NONE},
    {0x1000001Cu, LOAD, STORE},
    {0x10000040u, LOAD, STORE},
    {0x10000FFCu, LOAD, STORE},
    /* peripheral windows with nothing behind them */
    {0x10002000u, LOAD, STORE},
    {0x1000F000u, LOAD, STORE},
    /* outside both regions */
    {0x10010000u, LOAD, STORE},
    {0x20000000u, LOAD, STORE},
    {0xFFFFFFFCu, LOAD, STORE},
};

static const uint32_t fetches[][2] = {
    {0x00001000u, FETCH},          /* above RAM */
    {0x10000000u, FETCH},          /* peripherals are not executable */
    {0x40000000u, FETCH},
};

int main(void)
{
    uint32_t saved = csr_read(mtvec);
    csr_write(mtvec, (uint32_t)(uintptr_t)sv_vectors | 1u);   /* vectored */
    int fail = 0;
    for (unsigned i = 0; i < sizeof probes / sizeof probes[0] && !fail; i++) {
        if (sv_probe_load(probes[i].addr) != probes[i].load)
            fail = 2 + 3 * i;
        else if (sv_probe_store(probes[i].addr) != probes[i].store)
            fail = 3 + 3 * i;
    }
    for (unsigned i = 0; i < sizeof fetches / sizeof fetches[0] && !fail; i++)
        if (sv_probe_fetch(fetches[i][0]) != fetches[i][1])
            fail = 100 + i;
    csr_write(mtvec, saved);
    /* the SCRATCH mailbox still works after all that */
    mmio_write32(FW_SYSCTRL_SCRATCH, 0x12345678u);
    if (!fail && mmio_read32(FW_SYSCTRL_SCRATCH) != 0x12345678u)
        fail = 1;
    return fail;
}
