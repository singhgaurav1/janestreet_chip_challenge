/*
 * System validation: SoC smoke test (cocotb: test/test.py, test_sysval_smoke).
 *
 * Exercises the CPU, memory and system-control block from software, then
 * hands over to the host through the pins:
 *   1. drives uo_out[6:0] = 0x55, uio[7:4] = 0xA (output), uio[3:0] input
 *   2. waits until the host drives ui_in[4:0] = 0x15 (proves the input path;
 *      ui_in[7:5] are the host SPI pins)
 *   3. returns 0
 * The runtime (fw/src/runtime.c) reports the result in sysctrl SCRATCH:
 * 1 = pass, (n << 1) | 1 = check n failed; then puts the pins in their safe
 * state and sleeps.
 *
 * Register addresses come from fw/include/platform.h (docs/soc.md); the checks
 * themselves are this test's own.
 */
#include <stdint.h>

#include "fw.h"
#include "mmio.h"
#include "platform.h"

#define CHECK(n, cond) do { if (!(cond)) return (n); } while (0)

static const uint8_t table[8] = {3, 1, 4, 1, 5, 9, 2, 6};   /* .rodata */
static uint32_t counter = 7;                                /* .data */
static uint32_t zeroed[4];                                  /* .bss */
static volatile uint8_t bytes[4];

static uint32_t fib(uint32_t n) { return n < 2 ? n : fib(n - 1) + fib(n - 2); }

int main(void)
{
    CHECK(1, mmio_read32(FW_SYSCTRL_ID) == FW_SYSCTRL_ID_VALUE);
    CHECK(2, counter == 7 && zeroed[0] == 0 && zeroed[3] == 0);

    uint32_t sum = 0;
    for (unsigned i = 0; i < sizeof table; i++)
        sum += table[i];
    CHECK(3, sum == 31);

    CHECK(4, fib(12) == 144);                               /* recursion: stack */

    volatile uint32_t a = 1000003, b = 977;
    CHECK(5, a * b == 977002931u);                          /* M extension */
    CHECK(6, a / b == 1023 && a % b == 532);

    bytes[0] = 0x11; bytes[1] = 0x22; bytes[2] = 0x33; bytes[3] = 0x44;
    CHECK(7, *(volatile uint32_t *)(uintptr_t)bytes == 0x44332211u);   /* byte enables */

    uint32_t t0 = mmio_read32(FW_SYSCTRL_CYCLE);
    uint32_t t1 = mmio_read32(FW_SYSCTRL_CYCLE);
    CHECK(8, t1 > t0);

    /* top of the 4 KiB SRAM is real memory (the status block lives there) */
    CHECK(9, fw_status.magic == FW_STATUS_MAGIC);
    CHECK(10, (uintptr_t)&fw_status + sizeof fw_status == FW_RAM_BASE + FW_RAM_SIZE);

    /* pins: outputs for the host, then wait for its answer on ui_in */
    mmio_write32(FW_SYSCTRL_UIO_OE, 0xF0);
    mmio_write32(FW_SYSCTRL_UIO_OUT, 0xA0);
    mmio_write32(FW_SYSCTRL_GPIO_OUT, 0x55);
    uint32_t t = mmio_read32(FW_SYSCTRL_CYCLE);
    while ((mmio_read32(FW_SYSCTRL_GPIO_IN) & 0x1Fu) != 0x15u) {
        fw_heartbeat();
        if (mmio_read32(FW_SYSCTRL_CYCLE) - t > 200000u)
            return 11;                                      /* host never answered */
    }
    return 0;
}
