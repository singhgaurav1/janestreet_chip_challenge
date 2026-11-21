/*
 * Platform description: memory map and chip-level constants.
 *
 * Source: docs/soc.md. Nothing outside this file and pe_regs.h may hard-code
 * an address; both move to the generated regs.h when F1 lands.
 *
 * Two build targets (fw/Makefile TARGET=):
 *   chip  the SoC of docs/soc.md: 4 KiB SRAM, results to sysctrl SCRATCH
 *   tb    the standalone-Ibex testbench (test/fw/tb_ibex_fw.sv): same map
 *         for RAM, plus the simulation-only device at 0x2000_0000 (FW_SIM)
 */
#ifndef FW_PLATFORM_H
#define FW_PLATFORM_H

/* ---- Firmware RAM: SRAM macro at 0, boot address 0 (soc.md "Bus") ---- */
#ifndef FW_RAM_BASE
#define FW_RAM_BASE   0x00000000u
#endif
#ifndef FW_RAM_SIZE
#define FW_RAM_SIZE   4096u         /* 1024x32 macro (soc.md). Aliases above 0xFFF. */
#endif

/* ---- System control (soc.md "sysctrl registers") ---- */
#define FW_SYSCTRL_BASE       0x10000000u
#define FW_SYSCTRL_ID         (FW_SYSCTRL_BASE + 0x00u)  /* RO "PEMU" */
#define FW_SYSCTRL_GPIO_OUT   (FW_SYSCTRL_BASE + 0x04u)  /* [6:0] -> uo_out[6:0] */
#define FW_SYSCTRL_GPIO_IN    (FW_SYSCTRL_BASE + 0x08u)  /* {uio_in, ui_in}, synchronized */
#define FW_SYSCTRL_UIO_OUT    (FW_SYSCTRL_BASE + 0x0Cu)  /* [7:0] */
#define FW_SYSCTRL_UIO_OE     (FW_SYSCTRL_BASE + 0x10u)  /* [7:0] */
#define FW_SYSCTRL_CYCLE      (FW_SYSCTRL_BASE + 0x14u)  /* RO free-running */
#define FW_SYSCTRL_SCRATCH    (FW_SYSCTRL_BASE + 0x18u)  /* RW mailbox to the host */
#define FW_SYSCTRL_ID_VALUE   0x50454D55u                /* "PEMU" */

/* ---- Protocol Engine block (docs/soc.md, docs/pe_isa.md section 6) ---- */
#ifndef FW_PE_BASE
#define FW_PE_BASE    0x10001000u
#endif

/* ---- Interrupt wiring (docs/soc.md "Pads and interrupts") ----
 * Ibex fast interrupts: event flag n -> ID 16 + n; engine n FIFO -> ID 24 + n.
 * IDs 28-30, external, timer, software and NMI are tied off. */
#define FW_IRQ_ID_FAST0      16u
#define FW_IRQ_ID_PE_EVT(n)  (FW_IRQ_ID_FAST0 + (n))
#define FW_IRQ_ID_PE_FIFO(n) (FW_IRQ_ID_FAST0 + 8u + (n))

/* ---- Clock ---- */
#define FW_SYS_HZ     60000000u     /* fixed decision: 60 MHz */

/* ---- Result mailbox ----
 * fw_exit()/fw_panic() write a riscv-tests style code here: 1 = pass,
 * (code << 1) | 1 = fail with code (panics use 1000 + reason). The host
 * polls it; the status block at the top of RAM has the details. */
#ifdef FW_SIM
#define FW_RESULT_REG FW_SIM_TOHOST
#else
#define FW_RESULT_REG FW_SYSCTRL_SCRATCH
#endif

/* ---- Simulation-only control device (test/fw/tb_ibex_fw.sv) ----
 * Present only in the standalone testbench, never on the chip. Code that
 * uses it must be guarded by FW_SIM. */
#define FW_SIM_BASE        0x20000000u
#define FW_SIM_TOHOST      (FW_SIM_BASE + 0x00u)
#define FW_SIM_PUTC        (FW_SIM_BASE + 0x10u)
#define FW_SIM_IRQ_SET     (FW_SIM_BASE + 0x14u)
#define FW_SIM_IRQ_CLR     (FW_SIM_BASE + 0x18u)
#define FW_SIM_CYCLES      (FW_SIM_BASE + 0x1Cu)
#define FW_SIM_ERRCNT      (FW_SIM_BASE + 0x20u)
#define FW_SIM_PUTHEX      (FW_SIM_BASE + 0x24u)

#endif /* FW_PLATFORM_H */
