/* UART on the Protocol Engines; see pe_uart.h. Written from
 * docs/pe_isa.md and examples/pe/uart.pe. */
#include "pe_uart.h"

#include "pe_regs.h"
#include "uart_pe.h"

int pe_uart_init(const pe_uart_t *u)
{
    if (u->tx_pe >= PE_NUM_ENGINES || u->rx_pe >= PE_NUM_ENGINES || u->tx_pe == u->rx_pe ||
        u->tx_pad < 8 || u->tx_pad >= PE_PAD_HOST_MISO)
        return PE_EINVAL;
    uint32_t mask = (1u << u->tx_pe) | (1u << u->rx_pe);
    int r;
    pe_stop_mask(mask);
    /* Idle high before the engine owns the pad: the CPU sets the pad's
     * level register, and a new owner starts from it (pe_isa.md 6.2). */
    if ((r = pe_pad_set_owner(u->tx_pad, PE_OWNER_CPU)) != PE_OK)
        return r;
    pe_gpio_write(u->tx_pad, 1);
    if ((r = pe_load_program(pe_cfg_origin(u->tx), uart_tx_program, UART_TX_LEN)) != PE_OK ||
        (r = pe_load_program(pe_cfg_origin(u->rx), uart_rx_program, UART_RX_LEN)) != PE_OK ||
        (r = pe_configure_prechecked(u->tx_pe, u->tx)) != PE_OK ||
        (r = pe_configure_prechecked(u->rx_pe, u->rx)) != PE_OK)
        return r;
    pe_evt_clear(1u << (4 + u->rx_pe));
    if ((r = pe_start_mask(mask)) != PE_OK)
        return r;
    return pe_pad_set_owner(u->tx_pad, u->tx_pe);
}

int pe_uart_putc(const pe_uart_t *u, uint8_t c, uint32_t timeout_cycles)
{
    return pe_tx_put(u->tx_pe, c, timeout_cycles);
}

int pe_uart_getc(const pe_uart_t *u, uint8_t *c, uint32_t timeout_cycles)
{
    uint32_t w = 0;
    int r = pe_rx_get(u->rx_pe, &w, timeout_cycles);
    if (r == PE_OK)
        *c = (uint8_t)(w >> 24);   /* shifted in from the left */
    return r;
}

uint32_t pe_uart_framing_error(const pe_uart_t *u)
{
    uint32_t m = 1u << (4 + u->rx_pe);
    uint32_t hit = pe_evt_read() & m;
    if (hit)
        pe_evt_clear(m);
    return hit != 0;
}

void pe_uart_stop(const pe_uart_t *u)
{
    pe_stop_mask((1u << u->tx_pe) | (1u << u->rx_pe));
    pe_pad_set_owner(u->tx_pad, PE_OWNER_NONE);
}
