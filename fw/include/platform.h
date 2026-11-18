/*
 * Platform description: memory map and chip-level constants.
 *
 * STUB until F1 lands. The chip memory map is not specified yet (no register
 * YAML, no top level). Every value marked STUB is a placeholder chosen by the
 * firmware chat; when the generated fw/include/regs.h exists, these come from
 * it and this file shrinks to the few constants the firmware chooses itself.
 * Nothing outside this file and pe_regs.h may hard-code an address.
 */
#ifndef FW_PLATFORM_H
#define FW_PLATFORM_H

/* ---- Firmware RAM (single-port SRAM macro). Boot address = RAM_BASE. ---- */
#ifndef FW_RAM_BASE
#define FW_RAM_BASE   0x00000000u   /* STUB: Ibex boot_addr_i tie-off */
#endif
#ifndef FW_RAM_SIZE
#define FW_RAM_SIZE   4096u         /* 1024x32 macro (plan); 2048 for 512x32 */
#endif

/* ---- Protocol Engine block (layout per docs/pe_isa.md section 6) ---- */
#ifndef FW_PE_BASE
#define FW_PE_BASE    0x10000000u   /* STUB */
#endif

/* ---- Interrupt wiring (Ibex fast interrupts are IDs 16..30) ----
 * STUB: the spec has an IRQ_EN register but does not say which Ibex
 * interrupt line(s) the PE block drives. Firmware assumes one fast line per
 * event flag: event flag n -> irq_fast_i[n] -> interrupt ID 16 + n. */
#define FW_IRQ_ID_FAST0     16u
#define FW_IRQ_ID_PE_EVT(n) (FW_IRQ_ID_FAST0 + (n))

/* ---- Clock ---- */
#define FW_SYS_HZ     60000000u     /* fixed decision: 60 MHz */

/* ---- Simulation-only control device (test/fw/tb_ibex_fw.sv) ----
 * Present only in the firmware testbench, never on the chip. Code that uses
 * it must be guarded by FW_SIM. */
#define FW_SIM_BASE        0x20000000u
#define FW_SIM_TOHOST      (FW_SIM_BASE + 0x00u)
#define FW_SIM_PUTC        (FW_SIM_BASE + 0x10u)
#define FW_SIM_IRQ_SET     (FW_SIM_BASE + 0x14u)
#define FW_SIM_IRQ_CLR     (FW_SIM_BASE + 0x18u)
#define FW_SIM_CYCLES      (FW_SIM_BASE + 0x1Cu)
#define FW_SIM_ERRCNT      (FW_SIM_BASE + 0x20u)
#define FW_SIM_PUTHEX      (FW_SIM_BASE + 0x24u)

#endif /* FW_PLATFORM_H */
