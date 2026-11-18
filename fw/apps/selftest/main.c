/*
 * Runtime self-test on the standalone-Ibex testbench (test/fw).
 * Checks startup (.data/.bss/stack), the cycle counter, libgcc, CRC, and the
 * interrupt path end to end using the testbench's interrupt-line register.
 * Returns 0 on success; a failing check returns its number (testbench prints
 * "FAIL code=<n>").
 */
#include <stdint.h>

#include "csr.h"
#include "fw.h"
#include "irq.h"
#include "mmio.h"
#include "platform.h"

#define CHECK(n, cond) do { if (!(cond)) { fw_puts("selftest: check " #n " failed\n"); return (n); } } while (0)

static uint32_t zeroed[16];
static uint32_t inited = 0x12345678u;

static volatile uint32_t irq_count, irq_last_id, irq_order[4];
static volatile uint32_t irq_entry_cycle;

/* Testbench line bit for Ibex fast interrupt n is bit n of SIM_IRQ. */
static void sim_irq_raise(uint32_t bits) { mmio_write32(FW_SIM_IRQ_SET, bits); }
static void sim_irq_lower(uint32_t bits) { mmio_write32(FW_SIM_IRQ_CLR, bits); }

static void on_fast(uint32_t id)
{
    if (irq_count == 0)
        irq_entry_cycle = fw_cycles32();
    if (irq_count < 4)
        irq_order[irq_count] = id;
    irq_count++;
    irq_last_id = id;
    sim_irq_lower(1u << (id - IRQ_ID_FAST(0)));  /* level-sensitive: clear the source */
}

static int wait_count(uint32_t n, uint32_t timeout)
{
    uint32_t t0 = fw_cycles32();
    while (irq_count < n)
        if (fw_elapsed(t0, timeout))
            return 0;
    return 1;
}

int main(void)
{
    fw_puts("selftest: start\n");

    /* 1. startup: .bss zeroed (the testbench pre-fills RAM with a pattern) */
    for (unsigned i = 0; i < 16; i++)
        CHECK(1, zeroed[i] == 0);
    /* 2. .data loaded */
    CHECK(2, inited == 0x12345678u);
    /* 3. status block */
    CHECK(3, fw_status.magic == FW_STATUS_MAGIC && fw_status.state == FW_STATE_RUNNING);

    /* 4. cycle counter runs, 64-bit read is monotonic */
    uint64_t c0 = fw_cycles64();
    for (volatile int i = 0; i < 10; i++) {
    }
    uint64_t c1 = fw_cycles64();
    CHECK(4, c1 > c0 && c1 - c0 < 1000);

    /* 5. M extension; 6. 64-bit multiply (inline mul/mulhu, no libgcc).
     * 64-bit divide is deliberately not used: libgcc's version is ~2.4 KB and
     * mkimage.py rejects it (see fw/README.md, "Code size"). */
    volatile uint32_t a = 0xDEADBEEFu, b = 12345u;
    CHECK(5, a * b == 0x287C5337u && a / b == 302626u && a % b == 10589u);
    volatile uint64_t big = 0x0123456789ABCDEFull;
    CHECK(6, big * 1000003u == 0x7530ECA8640F838Dull && (big >> 7) == 0x2468ACF13579Bull);

    /* 7. CRC-32 check value */
    CHECK(7, fw_crc32(0, "123456789", 9) == 0xCBF43926u);

    /* 8. one fast interrupt, handler clears the line; measure entry latency */
    irq_register(IRQ_ID_FAST(5), on_fast);
    irq_enable(IRQ_ID_FAST(5));
    irq_global_enable();
    uint32_t t_raise = fw_cycles32();
    sim_irq_raise(1u << 5);
    CHECK(8, wait_count(1, 2000) && irq_last_id == IRQ_ID_FAST(5));
    uint32_t latency = irq_entry_cycle - t_raise;
    fw_puts("selftest: irq entry latency (cycles):\n");
    fw_puthex(latency);
    CHECK(9, latency < 2000);  /* sanity only: depends on bus wait states */

    /* 10. two at once: lower ID first (Ibex fast-interrupt priority) */
    irq_count = 0;
    irq_register(IRQ_ID_FAST(2), on_fast);
    irq_register(IRQ_ID_FAST(9), on_fast);
    irq_enable(IRQ_ID_FAST(2));
    irq_enable(IRQ_ID_FAST(9));
    uint32_t s = irq_save();
    sim_irq_raise((1u << 2) | (1u << 9));
    /* 11. masked: nothing may be taken inside the critical section */
    for (volatile int i = 0; i < 50; i++) {
    }
    CHECK(11, irq_count == 0);
    irq_restore(s);
    CHECK(10, wait_count(2, 2000) && irq_order[0] == IRQ_ID_FAST(2) &&
                  irq_order[1] == IRQ_ID_FAST(9));

    /* 12. spurious: enabled with no handler -> disabled and counted */
    uint32_t sp0 = fw_status.spurious_irqs;
    irq_enable(IRQ_ID_FAST(12));
    sim_irq_raise(1u << 12);
    uint32_t t0 = fw_cycles32();
    while (fw_status.spurious_irqs == sp0 && !fw_elapsed(t0, 2000)) {
    }
    CHECK(12, fw_status.spurious_irqs == sp0 + 1 && fw_status.last_spurious_id == IRQ_ID_FAST(12));
    CHECK(13, (csr_read(mie) & (1u << IRQ_ID_FAST(12))) == 0);
    sim_irq_lower(1u << 12);

    /* 14. registration rejects non-interrupt IDs */
    CHECK(14, irq_register(0, on_fast) < 0 && irq_register(IRQ_ID_NMI, on_fast) < 0 &&
                  irq_enable(31) < 0 && irq_register(IRQ_ID_TIMER, on_fast) == 0);

    /* 15. stack: guard intact, some never used */
    uint32_t free_b = fw_stack_free();
    fw_puts("selftest: stack bytes never used:\n");
    fw_puthex(free_b);
    CHECK(15, free_b > 64);

    fw_heartbeat();
    CHECK(16, fw_status.heartbeat == 1);

    fw_puts("selftest: all checks passed\n");
    return 0;
}
