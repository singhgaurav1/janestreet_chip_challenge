/*
 * Protocol Engine registers, transcribed from docs/pe_isa.md section 6
 * ("CPU register map (draft)"). Written from the spec only.
 *
 * This file is replaced by the generated fw/include/regs.h when F1 lands.
 * Naming convention (proposed to the RTL chat for the YAML generator, so the
 * switch is mechanical):
 *   PE_<REG>_OFF                 byte offset of a per-engine register
 *   PE_<REG>_<FIELD>_LSB/_WIDTH  field position
 *   PE_SH_<REG>_OFF              byte offset of a shared register (from PE_SHARED_BASE)
 *
 * Status of each part:
 *   per-engine offsets and fields   SPEC (pe_isa.md section 6)
 *   shared-block offsets/packing    STUB (section 6 lists the registers but
 *                                   gives no offsets or field packing)
 */
#ifndef FW_PE_REGS_H
#define FW_PE_REGS_H

#include "platform.h"

#define PE_NUM_ENGINES    4u
#define PE_IMEM_WORDS     64u          /* pe_isa.md 1.2 */
#define PE_FIFO_DEPTH     2u           /* pe_isa.md 1.1: 2 x 32 each (was 4 until 2026-11-18) */
#define PE_PC_WORDS       32u          /* PC is 5 bits (offset from ORIGIN) */

#define PE_ENGINE_STRIDE  0x100u
#define PE_ENGINE_BASE(n) (FW_PE_BASE + PE_ENGINE_STRIDE * (n))
#define PE_SHARED_BASE    (FW_PE_BASE + 0x400u)

/* ---- Per-engine registers (SPEC) ---- */
#define PE_CTRL_OFF       0x00u
#define PE_CLKDIV_OFF     0x04u
#define PE_EXECCTRL_OFF   0x08u
#define PE_SHIFTCTRL_OFF  0x0Cu
#define PE_PINCTRL_OFF    0x10u
#define PE_ASSIST_OFF     0x14u
#define PE_CRC_POLY_OFF   0x18u
#define PE_TIMING_OFF     0x1Cu
#define PE_INSTR_OFF      0x20u
#define PE_TXF_OFF        0x24u
#define PE_RXF_OFF        0x28u
#define PE_STATUS_OFF     0x2Cu

#define PE_CTRL_EN_LSB              0
#define PE_CTRL_RESTART_LSB         1   /* W1: clears counters, PC <- 0 */
#define PE_CTRL_DIV_RESTART_LSB     2   /* W1 */

#define PE_CLKDIV_FRAC_LSB          8
#define PE_CLKDIV_FRAC_WIDTH        8
#define PE_CLKDIV_INT_LSB           16
#define PE_CLKDIV_INT_WIDTH         16

#define PE_EXECCTRL_WRAP_BOTTOM_LSB 0
#define PE_EXECCTRL_WRAP_BOTTOM_WIDTH 5
#define PE_EXECCTRL_WRAP_TOP_LSB    5
#define PE_EXECCTRL_WRAP_TOP_WIDTH  5
#define PE_EXECCTRL_JMP_PIN_LSB     10
#define PE_EXECCTRL_JMP_PIN_WIDTH   5
#define PE_EXECCTRL_FILTER_LSB      15
#define PE_EXECCTRL_FILTER_WIDTH    1
#define PE_EXECCTRL_SYNC_EDGE_LSB   16
#define PE_EXECCTRL_SYNC_EDGE_WIDTH 2
#define PE_EXECCTRL_AUTOSYNC_LSB    18
#define PE_EXECCTRL_AUTOSYNC_WIDTH  1
#define PE_EXECCTRL_SIDE_COUNT_LSB  19
#define PE_EXECCTRL_SIDE_COUNT_WIDTH 3
#define PE_EXECCTRL_SIDE_OPT_LSB    22
#define PE_EXECCTRL_SIDE_OPT_WIDTH  1
#define PE_EXECCTRL_SIDE_PINDIR_LSB 23
#define PE_EXECCTRL_SIDE_PINDIR_WIDTH 1
#define PE_EXECCTRL_ORIGIN_LSB      24
#define PE_EXECCTRL_ORIGIN_WIDTH    6

#define PE_SHIFTCTRL_IN_SHIFTDIR_LSB   0
#define PE_SHIFTCTRL_IN_SHIFTDIR_WIDTH 1
#define PE_SHIFTCTRL_OUT_SHIFTDIR_LSB  1
#define PE_SHIFTCTRL_OUT_SHIFTDIR_WIDTH 1
#define PE_SHIFTCTRL_AUTOPUSH_LSB      2
#define PE_SHIFTCTRL_AUTOPUSH_WIDTH    1
#define PE_SHIFTCTRL_AUTOPULL_LSB      3
#define PE_SHIFTCTRL_AUTOPULL_WIDTH    1
#define PE_SHIFTCTRL_PUSH_THRESH_LSB   4   /* 0 = 32 */
#define PE_SHIFTCTRL_PUSH_THRESH_WIDTH 5
#define PE_SHIFTCTRL_PULL_THRESH_LSB   9   /* 0 = 32 */
#define PE_SHIFTCTRL_PULL_THRESH_WIDTH 5

#define PE_PINCTRL_OUT_BASE_LSB     0
#define PE_PINCTRL_OUT_BASE_WIDTH   5
#define PE_PINCTRL_OUT_COUNT_LSB    5   /* 0..32 */
#define PE_PINCTRL_OUT_COUNT_WIDTH  6
#define PE_PINCTRL_SET_BASE_LSB     11
#define PE_PINCTRL_SET_BASE_WIDTH   5
#define PE_PINCTRL_SET_COUNT_LSB    16
#define PE_PINCTRL_SET_COUNT_WIDTH  3
#define PE_PINCTRL_SIDE_BASE_LSB    19
#define PE_PINCTRL_SIDE_BASE_WIDTH  5
#define PE_PINCTRL_IN_BASE_LSB      24
#define PE_PINCTRL_IN_BASE_WIDTH    5

#define PE_ASSIST_CODER_MODE_LSB    0
#define PE_ASSIST_CODER_MODE_WIDTH  2
#define PE_ASSIST_CODER_DIFF_LSB    2
#define PE_ASSIST_CODER_DIFF_WIDTH  1
#define PE_ASSIST_CODER_IDLE_LSB    3
#define PE_ASSIST_CODER_IDLE_WIDTH  1
#define PE_ASSIST_STUFF_MODE_LSB    4
#define PE_ASSIST_STUFF_MODE_WIDTH  2
#define PE_ASSIST_STUFF_N_LSB       6   /* stored as N-1 */
#define PE_ASSIST_STUFF_N_WIDTH     3
#define PE_ASSIST_BIT_TICKS_LSB     9   /* stored as BIT_TICKS-1 */
#define PE_ASSIST_BIT_TICKS_WIDTH   4
#define PE_ASSIST_CRC_WIDTH_LSB     13  /* stored as CRC_WIDTH-1 */
#define PE_ASSIST_CRC_WIDTH_WIDTH   5
#define PE_ASSIST_CRC_INIT_ONES_LSB 18
#define PE_ASSIST_CRC_INIT_ONES_WIDTH 1
#define PE_ASSIST_STAMP_EN_LSB      19
#define PE_ASSIST_STAMP_EN_WIDTH    1
#define PE_ASSIST_STAMP_COUNT_LSB   20  /* stored as STAMP_COUNT-1 */
#define PE_ASSIST_STAMP_COUNT_WIDTH 4
#define PE_ASSIST_CRC_REFLECT_LSB   24
#define PE_ASSIST_CRC_REFLECT_WIDTH 1

#define PE_CRC_POLY_LSB             0
#define PE_CRC_POLY_WIDTH           32

#define PE_TIMING_TIMEOUT_LSB       0
#define PE_TIMING_TIMEOUT_WIDTH     16
#define PE_TIMING_SYNC_PHASE_LSB    16
#define PE_TIMING_SYNC_PHASE_WIDTH  16

#define PE_STATUS_PC_LSB            0
#define PE_STATUS_PC_WIDTH          5
#define PE_STATUS_T_LSB             5
#define PE_STATUS_SERR_LSB          6   /* W1C */
#define PE_STATUS_OVF_LSB           7   /* W1C */
#define PE_STATUS_TXLEVEL_LSB       8
#define PE_STATUS_TXLEVEL_WIDTH     4
#define PE_STATUS_RXLEVEL_LSB       12
#define PE_STATUS_RXLEVEL_WIDTH     4
#define PE_STATUS_STALLED_LSB       16

/* ---- Shared block (STUB offsets and packing; register list is SPEC) ---- */
#define PE_SH_IMEM_OFF        0x000u   /* 64 x 32-bit slots, low 16 bits used */
#define PE_SH_EVT_OFF         0x100u   /* R/W: 8 event flags */
#define PE_SH_EVT_SET_OFF     0x104u   /* W1S alias */
#define PE_SH_EVT_CLR_OFF     0x108u   /* W1C alias */
#define PE_SH_IRQ_EN_OFF      0x10Cu   /* [7:0]: event flag n raises its IRQ */
/* One owner register per pad: changing one pad never rewrites another (no
 * read-modify-write of a register the spec does not say is readable). */
#define PE_SH_PAD_OWNER_OFF(pad) (0x140u + 4u * ((pad) - 8u))  /* pads 8..23, [2:0] */
#define PE_SH_PAD_OD_OFF      0x118u   /* [7:0]: OD for pads 8..15 */
#define PE_SH_GPIO_OUT_OFF    0x11Cu   /* [15:0]: CPU GPIO level, pads 8..23 */
#define PE_SH_GPIO_OE_OFF     0x120u   /* [7:0]: CPU GPIO OE, pads 8..15 */

#define PE_PAD_FIRST_DRIVEABLE 8u
#define PE_PAD_LAST_DRIVEABLE  23u
#define PE_PAD_LAST_BIDIR      15u

/* Pad owner codes (SPEC, pe_isa.md 2.2) */
#define PE_OWNER_PE(n)   (n)
#define PE_OWNER_CPU     4u
#define PE_OWNER_SYSTEM  5u
#define PE_OWNER_NONE    7u

#endif /* FW_PE_REGS_H */
