/*
 * UART echo on the Protocol Engines (F4). TX on uo_out[0] (pad 16), RX on
 * ui_in[0] (pin 0); engines 0 (TX) and 1 (RX). VARIANT is the baud rate.
 *
 * Sends "PEMU\n", echoes UART_ECHO_BYTES bytes, sends 0x04 (end marker: the
 * exit's safe state then drives the pad low, which the far end sees as a
 * break), then exits 0. The
 * host side (test/fw/cocotb/test_uart.py) drives and checks the line with
 * cocotbext-uart. Exit codes: 10 + HAL error on init, 20 TX timeout, 30 RX
 * timeout, 40 framing error, 41 TX FIFO overflow / RX underflow flags.
 */
#include <stdint.h>

#include "csr.h"
#include "fw.h"
#include "pe_hal.h"
#include "pe_regs.h"
#include "pe_uart.h"
#include "platform.h"
#include "uart_cfg.h"

#define UART_ECHO_BYTES 16
/* generous: one byte at 115200 is ~5,200 cycles */
#define BYTE_TIMEOUT (FW_SYS_HZ / VARIANT * 10u * 50u)

static const pe_uart_t uart = {.tx_pe = 0, .rx_pe = 1, .tx_pad = 16,
                               .tx = &uart_tx_cfg, .rx = &uart_rx_cfg};

int main(void)
{
    pe_hal_init();
    int r = pe_uart_init(&uart);
    if (r != PE_OK)
        return 10 - r;
    for (const char *p = "PEMU\n"; *p; p++)
        if (pe_uart_putc(&uart, (uint8_t)*p, BYTE_TIMEOUT) != PE_OK)
            return 20;
    for (int i = 0; i < UART_ECHO_BYTES; i++) {
        uint8_t c;
        if (pe_uart_getc(&uart, &c, BYTE_TIMEOUT * 20u) != PE_OK)
            return 30;
        if (pe_uart_putc(&uart, c, BYTE_TIMEOUT) != PE_OK)
            return 20;
    }
    if (pe_uart_putc(&uart, 0x04, BYTE_TIMEOUT) != PE_OK)
        return 20;
    if (pe_uart_framing_error(&uart))
        return 40;
    uint32_t err = (1u << PE_STATUS_TXOVF_LSB) | (1u << PE_STATUS_RXUNF_LSB);
    if ((pe_status(uart.tx_pe) | pe_status(uart.rx_pe)) & err)
        return 41;
    /* let the last byte leave before exit stops the engines */
    while (PE_STATUS_TXLEVEL(pe_status(uart.tx_pe)) != 0) {
    }
    uint32_t t0 = fw_cycles32();
    while (!fw_elapsed(t0, FW_SYS_HZ / VARIANT * 12u)) {
    }
    return 0;
}
