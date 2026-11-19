/*
 * HAL on the real core: configure and start an engine, hand pads to it, then
 * panic. Run with the testbench's +pe_stub; fw/tests/run_fw_tests.py checks
 * the logged register writes (configuration image, start sequence, and the
 * safe-state order after the panic). The stub is not an engine model.
 */
#include <stdint.h>

#include "fw.h"
#include "pe_hal.h"

static const uint16_t prog[4] = {0x9f50, 0xf727, 0x6001, 0x0642};  /* peasm uart_tx */

int main(void)
{
    pe_hal_init();
    pe_cfg_t c;
    pe_cfg_from_regs(&c, 0x00010000u, 0x00500060u, 0x00000003u, 0x00000020u, 0x0001e140u, 0, 0);
    FW_ASSERT(pe_cfg_set_tick_hz(&c, FW_SYS_HZ, 115200u * 8u) == PE_OK);
    FW_ASSERT(pe_cfg_set(&c, PE_F_ORIGIN, 8) == PE_OK);
    FW_ASSERT(pe_cfg_set(&c, PE_F_OUT_BASE, 17) == PE_OK);
    FW_ASSERT(pe_cfg_set(&c, PE_F_SIDE_BASE, 17) == PE_OK);
    FW_ASSERT(pe_load_program(8, prog, 4) == PE_OK);
    FW_ASSERT(pe_configure(1, &c) == PE_OK);
    FW_ASSERT(pe_pad_set_owner(17, PE_OWNER_PE(1)) == PE_OK);
    FW_ASSERT(pe_pad_set_owner(9, PE_OWNER_PE(1)) == PE_OK);
    FW_ASSERT(pe_pad_set_od(9, 1) == PE_OK);
    FW_ASSERT(pe_start(1) == PE_OK);
    FW_ASSERT(pe_load_program(10, prog, 1) == PE_EBUSY);  /* engine 1 uses it now */
    fw_panic(FW_PANIC_USER, 0x5AFE);
}
