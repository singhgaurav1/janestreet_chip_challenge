/* Protocol emulator hardware definitions (docs/soc.md). */
#ifndef PEMU_H
#define PEMU_H
#include <stdint.h>

#define PEMU_REG(addr) (*(volatile uint32_t *)(addr))

#define SYS_BASE      0x10000000u
#define SYS_ID        PEMU_REG(SYS_BASE + 0x00)   /* "PEMU" */
#define SYS_GPIO_OUT  PEMU_REG(SYS_BASE + 0x04)   /* [6:0] -> uo_out[6:0] */
#define SYS_GPIO_IN   PEMU_REG(SYS_BASE + 0x08)   /* {uio_in, ui_in} */
#define SYS_UIO_OUT   PEMU_REG(SYS_BASE + 0x0C)
#define SYS_UIO_OE    PEMU_REG(SYS_BASE + 0x10)
#define SYS_CYCLE     PEMU_REG(SYS_BASE + 0x14)
#define SYS_SCRATCH   PEMU_REG(SYS_BASE + 0x18)   /* mailbox to the host */

#define PEMU_ID_VALUE 0x50454D55u

#endif
