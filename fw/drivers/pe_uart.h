/*
 * UART (8N1, LSB first) on two Protocol Engines, using the programs in
 * examples/pe/uart.pe. One engine transmits on an output pad, another
 * receives from any input pin. Polled API; all waits are bounded.
 *
 * Configurations come from a .pecfg file (programs uart_tx / uart_rx, with
 * origin, pins and tick_hz = 8 * baud), checked at build time.
 */
#ifndef PE_UART_H
#define PE_UART_H

#include <stdint.h>

#include "pe_hal.h"

typedef struct {
    uint8_t tx_pe, rx_pe;       /* engines 0..3, different */
    uint8_t tx_pad;             /* = the TX config's OUT_BASE; 8..22 (23 is the host MISO) */
    const pe_cfg_t *tx, *rx;    /* from pecfg */
} pe_uart_t;

/* Loads both programs at their configured origins, configures and starts
 * the engines, then hands the TX pad to the TX engine glitch-free (the line
 * idles high throughout). PE_EINVAL for bad parameters; PE_EBUSY if the
 * program memory is in use by a running engine. */
int pe_uart_init(const pe_uart_t *u);

int pe_uart_putc(const pe_uart_t *u, uint8_t c, uint32_t timeout_cycles);
int pe_uart_getc(const pe_uart_t *u, uint8_t *c, uint32_t timeout_cycles);

/* 1 if a framing error was seen since the last call (event flag 4 + rx
 * engine, "evt set 4 rel"); clears it. */
uint32_t pe_uart_framing_error(const pe_uart_t *u);

/* Stops both engines and releases the TX pad (owner NONE: drives 0). */
void pe_uart_stop(const pe_uart_t *u);

#endif
