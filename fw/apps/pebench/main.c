/* Cycle costs on the chip, 64 repetitions each, results in cycles x 64. */
#include <stdint.h>

#include "csr.h"
#include "fw.h"
#include "mmio.h"
#include "pe_hal.h"
#include "pe_regs.h"
#include "platform.h"

volatile uint32_t results[8];
#define N 64

#define TIME(slot, body) do { uint32_t t0 = fw_cycles32(); \
    for (int i = 0; i < N; i++) { body; } results[slot] = fw_cycles32() - t0; } while (0)

int main(void)
{
    pe_hal_init();
    volatile uint32_t sink = 0;
    uint32_t w;
    TIME(0, __asm__ volatile(""));                                       /* loop overhead */
    TIME(1, sink = mmio_read32(PE_ENGINE_BASE(0) + PE_STATUS_OFF));     /* PE read */
    TIME(2, mmio_write32(PE_SHARED_BASE + PE_SH_GPIO_OUT_OFF, 0));     /* PE write */
    TIME(3, sink = mmio_read32(FW_SYSCTRL_BASE));                       /* sysctrl read */
    TIME(4, sink = results[0]);                                         /* SRAM read */
    TIME(5, sink = pe_status(0));
    TIME(6, (void)pe_rx_try(0, &w));                                    /* empty: status only */
    TIME(7, (void)pe_tx_try(0, i); (void)pe_tx_try(0, i); pe_exec(0, 0x8040, 100); (void)pe_exec(0, 0x8040, 100)); /* 2 puts + 2 forced pulls */
    (void)sink;
    return 0;
}
