/*
 * Protocol Engine HAL. Written from docs/pe_isa.md only (never from RTL).
 *
 * Model:
 *   - A program is loaded into the shared 64-word instruction memory at an
 *     ORIGIN; an engine runs it relative to its ORIGIN (pe_isa.md 4).
 *   - An engine is configured while disabled, from a pe_cfg_t: an image of
 *     its seven configuration registers. The assembler's C header gives the
 *     program's default image; loader fields (pins, divider, ORIGIN) are set
 *     on top with pe_cfg_set().
 *   - Every blocking call takes a timeout in CPU cycles. PE_WAIT_FOREVER
 *     exists but must not be used in code that has to stay safe if an engine
 *     or the far end stops.
 *
 * Concurrency: functions touching one engine's FIFOs are not reentrant for
 * that engine; use them from one context (main loop or one interrupt
 * handler), or wrap them in irq_save()/irq_restore().
 *
 * Every function returns PE_OK (0) or a negative pe_err_t, and validates all
 * arguments before touching hardware.
 */
#ifndef FW_PE_HAL_H
#define FW_PE_HAL_H

#include <stdint.h>

#include "pe_regs.h"

typedef enum {
    PE_OK        = 0,
    PE_EINVAL    = -1,  /* argument out of range or inconsistent config */
    PE_EBUSY     = -2,  /* engine enabled / program memory in use */
    PE_ETIMEDOUT = -3,
    PE_EAGAIN    = -4,  /* FIFO full (TX) or empty (RX) on a non-blocking call */
} pe_err_t;

#define PE_WAIT_FOREVER 0xFFFFFFFFu

/* ---- Configuration image ---- */
typedef struct {
    uint32_t clkdiv, execctrl, shiftctrl, pinctrl, assist, crc_poly, timing;
} pe_cfg_t;

/* Fields by their architectural value (pe_isa.md 6). The HAL handles the
 * storage encodings (N-1, 32 stored as 0). */
typedef enum {
    PE_F_CLKDIV_INT,     /* 0..65535, 0 = 65536 */
    PE_F_CLKDIV_FRAC,    /* 0..255 (1/256) */
    PE_F_WRAP_BOTTOM,    /* 0..31 */
    PE_F_WRAP_TOP,       /* 0..31 */
    PE_F_JMP_PIN,        /* 0..31 */
    PE_F_FILTER,         /* 0..1 */
    PE_F_SYNC_EDGE,      /* 0 any, 1 rise, 2 fall */
    PE_F_AUTOSYNC,       /* 0..1 */
    PE_F_SIDE_COUNT,     /* 0..5 */
    PE_F_SIDE_OPT,       /* 0..1 */
    PE_F_SIDE_PINDIR,    /* 0..1 */
    PE_F_ORIGIN,         /* 0..63 */
    PE_F_IN_SHIFT_RIGHT, /* 0..1 */
    PE_F_OUT_SHIFT_RIGHT,/* 0..1 */
    PE_F_AUTOPUSH,       /* 0..1 */
    PE_F_AUTOPULL,       /* 0..1 */
    PE_F_PUSH_THRESH,    /* 1..32 */
    PE_F_PULL_THRESH,    /* 1..32 */
    PE_F_OUT_BASE,       /* 0..31 */
    PE_F_OUT_COUNT,      /* 0..32 */
    PE_F_SET_BASE,       /* 0..31 */
    PE_F_SET_COUNT,      /* 0..5 */
    PE_F_SIDE_BASE,      /* 0..31 */
    PE_F_IN_BASE,        /* 0..31 */
    PE_F_CODER_MODE,     /* 0 NRZ, 1 NRZI(0), 2 NRZI(1), 3 Manchester */
    PE_F_CODER_DIFF,     /* 0..1 */
    PE_F_CODER_IDLE,     /* 0..1 */
    PE_F_STUFF_MODE,     /* 0 any, 1 ones, 2 zeros */
    PE_F_STUFF_N,        /* 1..8 */
    PE_F_BIT_TICKS,      /* 1..16 */
    PE_F_CRC_WIDTH,      /* 1..32 */
    PE_F_CRC_INIT_ONES,  /* 0..1 */
    PE_F_STAMP_EN,       /* 0..1 */
    PE_F_STAMP_COUNT,    /* 1..16 */
    PE_F_CRC_REFLECT,    /* 0..1 */
    PE_F_CRC_POLY,       /* 32 bits */
    PE_F_TIMEOUT,        /* 0..65535 ticks, 0 = off */
    PE_F_SYNC_PHASE,     /* 0..65535, must be < CLKDIV_INT */
    PE_F_COUNT
} pe_field_t;

/* Defaults: everything off, divider 1, wrap 0..31, shift right, thresholds 32,
 * STUFF_N 6, BIT_TICKS 1, CRC_WIDTH 16, STAMP_COUNT 1 (same as the golden
 * model's EngineConfig defaults). The spec gives no register reset values,
 * so the HAL always writes a full image. */
void pe_cfg_init(pe_cfg_t *cfg);
/* From the assembler's generated header (<NAME>_CLKDIV, ...). */
void pe_cfg_from_regs(pe_cfg_t *cfg, uint32_t clkdiv, uint32_t execctrl, uint32_t shiftctrl,
                      uint32_t pinctrl, uint32_t assist, uint32_t crc_poly, uint32_t timing);
int pe_cfg_set(pe_cfg_t *cfg, pe_field_t field, uint32_t value);
uint32_t pe_cfg_get(const pe_cfg_t *cfg, pe_field_t field);
/* Whole-image rules (field ranges plus cross-field rules). */
int pe_cfg_check(const pe_cfg_t *cfg);
/* Divider for a tick rate: INT + FRAC/256 = sys_hz / tick_hz, rounded to
 * the nearest 1/256. 32-bit arithmetic only. */
int pe_cfg_set_tick_hz(pe_cfg_t *cfg, uint32_t sys_hz, uint32_t tick_hz);

/* ---- Program memory and engine control ---- */
/* Write len words at origin..origin+len-1 (no wrap). Fails with PE_EBUSY if
 * an enabled engine's window [ORIGIN, ORIGIN+31] (mod 64) overlaps it: the
 * spec allows writes only while the engines using a word are disabled. */
int pe_load_program(uint32_t origin, const uint16_t *code, uint32_t len);
/* Write the configuration. The engine must be disabled. */
int pe_configure(uint32_t pe, const pe_cfg_t *cfg);
/* Restart (PC, counters, divider), then enable. */
int pe_start(uint32_t pe);
int pe_stop(uint32_t pe);
/* Bitmask of engines the HAL has enabled. */
uint32_t pe_enabled_mask(void);
/* Force-execute one instruction; waits until it no longer stalls. */
int pe_exec(uint32_t pe, uint16_t instr, uint32_t timeout_cycles);

/* ---- FIFOs ---- */
uint32_t pe_status(uint32_t pe);   /* raw STATUS; 0 for an invalid engine */
#define PE_STATUS_TXLEVEL(st) (((st) >> PE_STATUS_TXLEVEL_LSB) & 0xFu)
#define PE_STATUS_RXLEVEL(st) (((st) >> PE_STATUS_RXLEVEL_LSB) & 0xFu)
int pe_tx_try(uint32_t pe, uint32_t word);
int pe_rx_try(uint32_t pe, uint32_t *word);
int pe_tx_put(uint32_t pe, uint32_t word, uint32_t timeout_cycles);
int pe_rx_get(uint32_t pe, uint32_t *word, uint32_t timeout_cycles);
/* Clear the sticky SERR and OVF flags (W1C). */
int pe_clear_errors(uint32_t pe);

/* ---- Event flags ---- */
uint32_t pe_evt_read(void);
void pe_evt_set(uint32_t mask);
void pe_evt_clear(uint32_t mask);
/* Let event flags in mask raise their CPU interrupt (IRQ_EN) and unmask the
 * matching Ibex interrupt IDs (platform.h FW_IRQ_ID_PE_EVT). The handler must
 * clear the flag (pe_evt_clear) before returning: the line is level-sensitive. */
int pe_evt_irq_enable(uint32_t mask);
int pe_evt_irq_disable(uint32_t mask);

/* ---- Pads (indices 8..23, pe_isa.md 2.1) ---- */
/* owner: PE_OWNER_PE(0..3), PE_OWNER_CPU or PE_OWNER_NONE. The system owner
 * (host bridge) is not assignable from firmware. */
int pe_pad_set_owner(uint32_t pad, uint32_t owner);
int pe_pad_set_od(uint32_t pad, uint32_t on);          /* bidir pads 8..15 */
int pe_gpio_write(uint32_t pad, uint32_t level);       /* pad owned by the CPU */
int pe_gpio_set_oe(uint32_t pad, uint32_t on);         /* bidir pads 8..15 */

/* ---- Initialisation ----
 * Call once after boot, before anything else here: disables all engines,
 * PE interrupts and event flags, and sets the CPU's own GPIO/OD registers to
 * a known state (all released). Pad owners are left as they are (the host
 * bridge may own some); the HAL only ever changes owners it is asked to. */
void pe_hal_init(void);

/* ---- Safety ---- */
/* Engines off, PE interrupts off, and every pad the firmware has assigned
 * taken back by the CPU and released (OE off, OD off; uo_out pads driven to
 * FW_SAFE_UO_LEVEL). Called by fw_panic()/fw_exit() through fw_safe_state(). */
void pe_safe_state(void);

#ifndef FW_SAFE_UO_LEVEL
#define FW_SAFE_UO_LEVEL 0u
#endif

#endif /* FW_PE_HAL_H */
