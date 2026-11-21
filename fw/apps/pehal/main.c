/*
 * PE HAL on the chip's Protocol Engine block, checked against
 * docs/pe_isa.md section 6 (register map v1) and docs/soc.md. Run by
 * test/fw/tb_chip_fw.sv; GPIO pins are wired back by the bench:
 *   ui_in[4:0] = uo_out[4:0] ^ 5'h15
 *   uio_in     = (uio_out & uio_oe) | (8'hC3 & ~uio_oe)   (external pulls)
 *
 * Three images (together they do not fit 4 KiB; VARIANT selects):
 *   pehal-fifo     reset values, forced instructions, FIFO levels and
 *                  sticky errors
 *   pehal-engines  two engines started together, busy program memory
 *   pehal-pads     CPU-owned pads through the pins, open drain, pad 23, then
 *                  the safe-state panic below
 *
 * A failed check returns its number. When all pass, pehal-pads leaves engines
 * running and pads driven (one pulling an open-drain line low) and panics
 * with FW_PANIC_USER + 1: the safe state must then release everything but
 * the system's pads; the bench checks the pins and reads PAD_OWNER and
 * ENGINE_CTRL back over the host bridge.
 */
#include <stdint.h>

#include "csr.h"
#include "fw.h"
#include "mmio.h"
#include "pe_hal.h"
#include "pe_regs.h"
#include "progs_pe.h"
#include "sysctrl.h"

#define CHECK(n, cond) do { if (!(cond)) return (n); } while (0)

/* Direct register access, for reset values and for what the HAL refuses to
 * do on purpose (overfilling a FIFO). */
__attribute__((unused)) static uint32_t sh_rd(uint32_t off) { return mmio_read32(PE_SHARED_BASE + off); }
__attribute__((unused)) static uint32_t st(uint32_t pe) { return pe_status(pe); }
__attribute__((unused)) static uint32_t bit(uint32_t v, uint32_t b) { return (v >> b) & 1u; }

__attribute__((unused)) static void settle(void)
{
    uint32_t t0 = fw_cycles32();
    while (!fw_elapsed(t0, 32)) {   /* > synchronizers + a few engine cycles */
    }
}

#if VARIANT == V_fifo
static int check_reset_values(void)
{
    /* pe_isa.md 6.2 / docs/soc.md: engines disabled, IRQ_EN and flags 0,
     * every pad owned by the system after reset, FIFOs empty */
    CHECK(1, (sh_rd(PE_SH_ENGINE_CTRL_OFF) & 0xFu) == 0);
    CHECK(2, sh_rd(PE_SH_IRQ_EN_OFF) == 0 && pe_evt_read() == 0);
    for (uint32_t p = 8; p <= 23; p++)
        CHECK(3, pe_pad_owner(p) == PE_OWNER_SYSTEM);
    for (uint32_t e = 0; e < PE_NUM_ENGINES; e++)
        CHECK(4, PE_STATUS_TXLEVEL(st(e)) == 0 && PE_STATUS_RXLEVEL(st(e)) == 0);
    return 0;
}

static int check_forced_and_fifos(void)
{
    pe_cfg_t c;
    pe_cfg_init(&c);
    CHECK(10, pe_configure(0, &c) == PE_OK);
    /* forced instructions on a disabled engine (6.1 INSTR) */
    CHECK(11, pe_exec(0, forced_program[0], 1000) == PE_OK);   /* set y, 21 */
    CHECK(12, pe_exec(0, forced_program[1], 1000) == PE_OK);   /* mov isr, y */
    CHECK(13, pe_exec(0, forced_program[2], 1000) == PE_OK);   /* push noblock */
    CHECK(14, PE_STATUS_RXLEVEL(st(0)) == 1);
    uint32_t w = 0;
    CHECK(15, pe_rx_try(0, &w) == PE_OK && w == 21);

    /* RX empty: the HAL refuses; a raw pop reads 0 and sets RXUNF (sticky) */
    CHECK(16, pe_rx_try(0, &w) == PE_EAGAIN && !bit(st(0), PE_STATUS_RXUNF_LSB));
    CHECK(17, mmio_read32(PE_ENGINE_BASE(0) + PE_RXF_OFF) == 0 && bit(st(0), PE_STATUS_RXUNF_LSB));
    CHECK(18, pe_clear_errors(0) == PE_OK && !bit(st(0), PE_STATUS_RXUNF_LSB));

    /* TX: two words fit (FIFOs are 2 deep); the HAL refuses a third; a raw
     * third push is dropped and sets TXOVF */
    CHECK(20, pe_tx_try(0, 0x11) == PE_OK && pe_tx_try(0, 0x22) == PE_OK);
    CHECK(21, PE_STATUS_TXLEVEL(st(0)) == 2 && pe_tx_try(0, 0x33) == PE_EAGAIN);
    CHECK(22, !bit(st(0), PE_STATUS_TXOVF_LSB));
    mmio_write32(PE_ENGINE_BASE(0) + PE_TXF_OFF, 0x33);
    CHECK(23, bit(st(0), PE_STATUS_TXOVF_LSB) && PE_STATUS_TXLEVEL(st(0)) == 2);
    CHECK(24, pe_clear_errors(0) == PE_OK && !bit(st(0), PE_STATUS_TXOVF_LSB));
    CHECK(25, pe_exec(0, forced_program[4], 1000) == PE_OK);   /* pull noblock */
    CHECK(26, PE_STATUS_TXLEVEL(st(0)) == 1);

    /* A forced blocking push into a full RX FIFO stays pending
     * (FORCE_PENDING), and STALLED (the program's) stays clear; one pop
     * lets it complete. */
    for (int i = 0; i < 2; i++) {
        CHECK(30, pe_exec(0, forced_program[1], 1000) == PE_OK);
        CHECK(31, pe_exec(0, forced_program[2], 1000) == PE_OK);
    }
    CHECK(32, PE_STATUS_RXLEVEL(st(0)) == 2);
    CHECK(33, pe_exec(0, forced_program[1], 1000) == PE_OK);
    CHECK(34, pe_exec(0, forced_program[3], 200) == PE_ETIMEDOUT);  /* push block */
    uint32_t s = st(0);
    CHECK(35, bit(s, PE_STATUS_FORCE_PENDING_LSB) && !bit(s, PE_STATUS_STALLED_LSB));
    CHECK(36, pe_rx_try(0, &w) == PE_OK && w == 21);
    settle();
    CHECK(37, !bit(st(0), PE_STATUS_FORCE_PENDING_LSB) && PE_STATUS_RXLEVEL(st(0)) == 2);
    while (pe_rx_try(0, &w) == PE_OK) {
    }
    return 0;
}

#endif

#if VARIANT == V_engines
static int check_two_engines(void)
{
    pe_cfg_t c;
    pe_cfg_from_regs(&c, FILLER_CLKDIV, FILLER_EXECCTRL, FILLER_SHIFTCTRL, FILLER_PINCTRL,
                     FILLER_ASSIST, FILLER_CRC_POLY, FILLER_TIMING);
    CHECK(40, pe_cfg_set(&c, PE_F_ORIGIN, 32) == PE_OK);
    CHECK(41, pe_load_program(32, filler_program, FILLER_LEN) == PE_OK);
    CHECK(42, pe_configure(1, &c) == PE_OK && pe_configure(2, &c) == PE_OK);
    /* ENGINE_CTRL: both start in the same cycle */
    CHECK(43, pe_start_mask(0x6) == PE_OK && pe_enabled_mask() == 0x6);
    settle();
    for (uint32_t e = 1; e <= 2; e++) {
        uint32_t s = st(e);
        CHECK(44, PE_STATUS_RXLEVEL(s) == 2 && bit(s, PE_STATUS_STALLED_LSB));
        uint32_t w = 0;
        CHECK(45, pe_rx_get(e, &w, 1000) == PE_OK && w == 5);
    }
    /* program memory used by a running engine cannot be rewritten */
    CHECK(46, pe_load_program(33, filler_program, 1) == PE_EBUSY);
    CHECK(47, pe_configure(1, &c) == PE_EBUSY);
    CHECK(48, pe_stop_mask(0x4) == PE_OK && pe_enabled_mask() == 0x2);
    CHECK(49, pe_load_program(33, filler_program, 1) == PE_EBUSY);   /* engine 1 still runs */
    return 0;
}

#endif

#if VARIANT == V_pads
static int check_pads(void)
{
    /* pad 16 = uo_out[0], looped to ui_in[0] inverted (^1) */
    CHECK(50, pe_pad_set_owner(16, PE_OWNER_CPU) == PE_OK && pe_pad_owner(16) == PE_OWNER_CPU);
    CHECK(51, pe_gpio_write(16, 1) == PE_OK);
    settle();
    CHECK(52, (sysctrl_ui_in() & 1u) == 0 && bit(pe_pad_state(), 16 - 8));
    pe_gpio_write(16, 0);
    settle();
    CHECK(53, (sysctrl_ui_in() & 1u) == 1);
    pe_gpio_write(16, 1);
    CHECK(54, pe_pad_set_owner(16, PE_OWNER_NONE) == PE_OK);   /* released: drives 0 */
    settle();
    CHECK(55, (sysctrl_ui_in() & 1u) == 1);

    /* pad 8 = uio[0]: push-pull with OE */
    CHECK(56, pe_pad_set_owner(8, PE_OWNER_CPU) == PE_OK);
    pe_gpio_write(8, 1);
    pe_gpio_set_oe(8, 1);
    settle();
    CHECK(57, (sysctrl_uio_in() & 1u) == 1);
    pe_gpio_write(8, 0);
    settle();
    CHECK(58, (sysctrl_uio_in() & 1u) == 0);
    pe_gpio_set_oe(8, 0);   /* off: the pull wins (0xC3 bit 0 = 1) */
    settle();
    CHECK(59, (sysctrl_uio_in() & 1u) == 1);

    /* pad 9 = uio[1], open drain: setting OD presets the level to 1 (released) */
    CHECK(60, pe_pad_set_owner(9, PE_OWNER_CPU) == PE_OK);
    CHECK(61, pe_pad_set_od(9, 1) == PE_OK);
    settle();
    CHECK(62, (sysctrl_uio_in() >> 1 & 1u) == 1 && bit(pe_pad_state(), 9 - 8));
    pe_gpio_write(9, 0);    /* pulls low */
    settle();
    CHECK(63, (sysctrl_uio_in() >> 1 & 1u) == 0);
    pe_gpio_write(9, 1);    /* released again */
    settle();
    CHECK(64, (sysctrl_uio_in() >> 1 & 1u) == 1);

    /* pad 23 (host MISO) stays with the system */
    CHECK(65, pe_pad_set_owner(23, PE_OWNER_CPU) == PE_EINVAL && pe_pad_owner(23) == PE_OWNER_SYSTEM);
    return 0;
}

#endif

int main(void)
{
#if VARIANT == V_fifo
    int r;
    if ((r = check_reset_values()) != 0)
        return r;
    pe_hal_init();
    return check_forced_and_fifos();
#elif VARIANT == V_engines
    pe_hal_init();
    return check_two_engines();
#else
    int r;
    pe_hal_init();
    if ((r = check_pads()) != 0)
        return r;

    /* Leave things driven: engine 1 running, pads 16 (CPU, high), 8 (CPU,
     * OE on, high) and 9 (open drain pulling low). The panic's safe state
     * must release them all. */
    pe_cfg_t c;
    pe_cfg_from_regs(&c, FILLER_CLKDIV, FILLER_EXECCTRL, FILLER_SHIFTCTRL, FILLER_PINCTRL,
                     FILLER_ASSIST, FILLER_CRC_POLY, FILLER_TIMING);
    pe_load_program(0, filler_program, FILLER_LEN);
    pe_configure(1, &c);
    pe_start(1);
    pe_pad_set_owner(16, PE_OWNER_CPU);
    pe_gpio_write(16, 1);
    pe_gpio_set_oe(8, 1);
    pe_gpio_write(8, 1);
    pe_gpio_write(9, 0);
    fw_panic(FW_PANIC_USER + 1, 0x5AFE);
#endif
}
