/*
 * System control block driver (docs/soc.md "sysctrl registers").
 * Pins (info.yaml): uo_out[6:0] GPIO out, uo_out[7] host MISO;
 * ui_in[4:0] GPIO in, ui_in[7:5] host SPI; uio[7:0] bidirectional GPIO.
 */
#ifndef FW_SYSCTRL_H
#define FW_SYSCTRL_H

#include <stdint.h>

#include "mmio.h"
#include "platform.h"

#define SYSCTRL_GPIO_OUT_MASK 0x7Fu   /* uo_out[7] belongs to the host bridge */
#define SYSCTRL_UI_GPIO_MASK  0x1Fu   /* ui_in[7:5] belong to the host bridge */

static inline uint32_t sysctrl_id(void) { return mmio_read32(FW_SYSCTRL_ID); }
static inline uint32_t sysctrl_cycle(void) { return mmio_read32(FW_SYSCTRL_CYCLE); }

static inline void sysctrl_gpio_out(uint32_t v)
{
    mmio_write32(FW_SYSCTRL_GPIO_OUT, v & SYSCTRL_GPIO_OUT_MASK);
}

/* {uio_in[7:0], ui_in[7:0]}; the GPIO inputs are ui_in[4:0]. */
static inline uint32_t sysctrl_gpio_in(void) { return mmio_read32(FW_SYSCTRL_GPIO_IN) & 0xFFFFu; }
static inline uint32_t sysctrl_ui_in(void) { return sysctrl_gpio_in() & SYSCTRL_UI_GPIO_MASK; }
static inline uint32_t sysctrl_uio_in(void) { return sysctrl_gpio_in() >> 8; }

static inline void sysctrl_uio_out(uint32_t v) { mmio_write32(FW_SYSCTRL_UIO_OUT, v & 0xFFu); }
static inline void sysctrl_uio_oe(uint32_t v) { mmio_write32(FW_SYSCTRL_UIO_OE, v & 0xFFu); }

static inline void sysctrl_scratch(uint32_t v) { mmio_write32(FW_SYSCTRL_SCRATCH, v); }

/* Safe state for the pins sysctrl drives: bidirectional pins released
 * (OE off first, so they never drive a new level), outputs low. Part of
 * fw_safe_state() on the chip. */
static inline void sysctrl_safe_state(void)
{
    mmio_write32(FW_SYSCTRL_UIO_OE, 0);
    mmio_write32(FW_SYSCTRL_UIO_OUT, 0);
    mmio_write32(FW_SYSCTRL_GPIO_OUT, 0);
}

#endif /* FW_SYSCTRL_H */
