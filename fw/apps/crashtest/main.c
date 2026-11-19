/*
 * Fault injection for the runtime's safety paths. Each VARIANT triggers one
 * failure; fw/tests/run_fw_tests.py checks the testbench exit code and the
 * status block the host would read.
 */
#include <stdint.h>

#include "csr.h"
#include "fw.h"
#include "irq.h"
#include "mmio.h"
#include "platform.h"
#include "sysctrl.h"

extern uint32_t __stack_limit[];

__attribute__((unused)) static void on_irq(uint32_t id) { mmio_write32(FW_SIM_IRQ_CLR, 1u << (id - IRQ_ID_FAST(0))); }

int main(void)
{
#if VARIANT == V_illegal
    __asm__ volatile(".word 0");                 /* all-zero: illegal (mcause 2) */
#elif VARIANT == V_loadfault
    (void)mmio_read32(0x30000000u);              /* unmapped: load access fault (5) */
#elif VARIANT == V_fetchfault
    ((void (*)(void))0x30000000u)();             /* unmapped: fetch access fault (1) */
#elif VARIANT == V_ecall
    __asm__ volatile("ecall");                   /* M-mode ecall (11): unexpected */
#elif VARIANT == V_nmi
    mmio_write32(FW_SIM_IRQ_SET, 1u << 18);      /* testbench drives irq_nm_i */
    for (;;) {
    }
#elif VARIANT == V_stackovf
    /* Take an interrupt with sp just above the guard word. */
    irq_register(IRQ_ID_FAST(3), on_irq);
    irq_enable(IRQ_ID_FAST(3));
    __asm__ volatile(
        "mv   s1, sp\n"
        "addi sp, %0, 32\n"                      /* frame (64 B) would cross the limit */
        "csrsi mstatus, 8\n"
        "sw   %1, 0(%2)\n"                        /* raise fast IRQ 3 */
        "1: j 1b\n"
        :: "r"(__stack_limit), "r"(1u << 3), "r"(FW_SIM_IRQ_SET) : "s1", "memory");
#elif VARIANT == V_assert
    volatile int x = 1;
    FW_ASSERT(x == 2);
#elif VARIANT == V_exitcode
    return 42;
#elif VARIANT == V_spin
    /* Runs until the testbench pulses reset without reloading the image. */
    for (;;)
        fw_heartbeat();
#elif VARIANT == V_pinpanic
    /* Chip only: drive every sysctrl pin, then panic. The safe state must
     * release them (testbench checks uio_oe == 0, uo_out[6:0] == 0). */
    sysctrl_gpio_out(0x7F);
    sysctrl_uio_out(0xFF);
    sysctrl_uio_oe(0xFF);
    fw_panic(FW_PANIC_USER, 0x9100);
#elif VARIANT == V_beyondram
    /* Chip only: read just past the end of the SRAM. If it aliases (current
     * docs/soc.md), word 0x84 (image magic) appears there: exit 50. Other
     * data: exit 51. A bus error panics (EXCEPTION, mcause 5). */
    uint32_t v = mmio_read32(FW_RAM_BASE + FW_RAM_SIZE + FW_IMAGE_HDR_OFF);
    return v == FW_IMAGE_MAGIC ? 50 : 51;
#else
#error unknown VARIANT
#endif
    return 0x7E;  /* not reached in fault variants */
}
