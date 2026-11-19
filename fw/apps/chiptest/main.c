/*
 * Self-test on the full chip (docs/soc.md), run by test/fw/tb_chip_fw.sv after
 * loading it over the host SPI bridge. Checks the sysctrl block and the GPIO
 * pins from the firmware side. The testbench wires the pins back:
 *   ui_in[4:0] = uo_out[4:0] ^ 5'h15
 *   uio_in     = (uio_out & uio_oe) | (8'hC3 & ~uio_oe)   (external pulls)
 * Returns 0 on success, else the number of the failed check.
 */
#include <stdint.h>

#include "csr.h"
#include "fw.h"
#include "mmio.h"
#include "sysctrl.h"

#define CHECK(n, cond) do { if (!(cond)) return (n); } while (0)

static void settle(void)
{
    uint32_t t0 = fw_cycles32();
    while (!fw_elapsed(t0, 16)) {   /* > 2-FF synchronizer + bus latency */
    }
}

static uint32_t loop_ui(uint32_t v) { return (v ^ 0x15u) & SYSCTRL_UI_GPIO_MASK; }

int main(void)
{
    /* 1. sysctrl answers with its ID */
    CHECK(1, sysctrl_id() == FW_SYSCTRL_ID_VALUE);

    /* 2. SoC cycle counter and Ibex mcycle run at the same rate */
    uint32_t c0 = sysctrl_cycle(), m0 = fw_cycles32();
    for (volatile int i = 0; i < 200; i++) {
    }
    uint32_t c1 = sysctrl_cycle(), m1 = fw_cycles32();
    uint32_t dc = c1 - c0, dm = m1 - m0;
    CHECK(2, dc > 200 && (dc > dm ? dc - dm : dm - dc) < 16);

    /* 3. uo_out[6:0] -> (testbench) -> ui_in[4:0] */
    static const uint8_t pat[] = {0x00, 0x1F, 0x0A, 0x15, 0x7F};
    for (unsigned i = 0; i < sizeof pat; i++) {
        sysctrl_gpio_out(pat[i]);
        settle();
        CHECK(3, sysctrl_ui_in() == loop_ui(pat[i]));
    }

    /* 4. uio: driven bits read back, released bits read the pulls */
    static const struct { uint8_t oe, out, in; } u[] = {
        {0x00, 0xFF, 0xC3}, {0xFF, 0x5A, 0x5A}, {0xF0, 0xA5, 0xA3}, {0x0F, 0x3C, 0xCC},
    };
    for (unsigned i = 0; i < sizeof u / sizeof u[0]; i++) {
        sysctrl_uio_out(u[i].out);
        sysctrl_uio_oe(u[i].oe);
        settle();
        CHECK(4, sysctrl_uio_in() == u[i].in);
    }

    /* 5. SRAM aliases above 0x7FF (docs/soc.md): 0x884 reads the image
     *    header magic at 0x84 */
    CHECK(5, mmio_read32(FW_RAM_BASE + FW_RAM_SIZE + FW_IMAGE_HDR_OFF) == FW_IMAGE_MAGIC);

    /* Leave the pins driven: fw_exit's safe state must release them (the
     * testbench checks uio_oe == 0 and uo_out[6:0] == 0 after exit). */
    sysctrl_gpio_out(0x55);
    sysctrl_uio_out(0xFF);
    sysctrl_uio_oe(0xFF);
    return 0;
}
