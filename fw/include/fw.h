/*
 * Firmware runtime: boot checks, status block, panic, exit.
 *
 * Safety model (see fw/README.md, "Safety"):
 *   - The image carries a CRC32; startup refuses to run a corrupted image.
 *   - Startup refuses to run twice from one load (initialised data would be
 *     stale): the host must reload the image before every reset release.
 *   - Any unexpected exception, NMI or failed check calls fw_panic(): the PE
 *     block is put in its safe state (engines stopped, pads released), the
 *     cause is recorded in the status block and the core halts.
 *   - The status block sits at a fixed address (top of RAM) so the host can
 *     read it through the SPI debug bridge at any time.
 */
#ifndef FW_FW_H
#define FW_FW_H

/* ---- Image header: fixed at boot_addr + 0x84, right after the reset jump.
 * mkimage.py fills length and crc32 after linking. ---- */
#define FW_IMAGE_MAGIC   0x57464550u  /* "PEFW" little-endian */
#define FW_IMAGE_HDR_OFF 0x84u
#define FW_STATUS_MAGIC  0x53544657u  /* "WFTS" */
#define FW_EXIT_PANIC_BASE 1000u  /* result code for a panic = base + reason */

#ifndef __ASSEMBLER__
#include <stdint.h>

typedef struct {
    uint32_t magic;
    uint32_t length;   /* bytes covered by crc32, from boot_addr */
    uint32_t crc32;    /* CRC-32 (IEEE) of [0, length) with this field taken as 0 */
    uint32_t version;  /* free-form build id */
} fw_image_hdr_t;

/* ---- Status block, top of RAM. Host-readable at any time. ---- */

typedef enum {
    FW_STATE_BOOTING = 1,
    FW_STATE_RUNNING = 2,
    FW_STATE_PANIC   = 3,
    FW_STATE_EXITED  = 4,
} fw_state_t;

typedef enum {
    FW_PANIC_NONE           = 0,
    FW_PANIC_EXCEPTION      = 1,  /* detail = mcause */
    FW_PANIC_NMI            = 2,
    FW_PANIC_IMAGE_CRC      = 3,  /* detail = computed CRC */
    FW_PANIC_DIRTY_RESTART  = 4,  /* started twice without a reload */
    FW_PANIC_STACK_OVERFLOW = 5,
    FW_PANIC_ASSERT         = 6,  /* detail = line number */
    FW_PANIC_HAL            = 7,  /* detail = HAL-specific */
    FW_PANIC_USER           = 16, /* first application-defined code */
} fw_panic_t;

typedef struct {
    uint32_t magic;          /* FW_STATUS_MAGIC once startup has run */
    uint32_t state;          /* fw_state_t */
    uint32_t reason;         /* fw_panic_t, or exit code when EXITED */
    uint32_t detail;
    uint32_t mcause, mepc, mtval;
    uint32_t ra;             /* at the trap or panic call */
    uint32_t sp;
    uint32_t cycles_lo, cycles_hi;
    uint32_t spurious_irqs;  /* unhandled interrupts (each was then disabled) */
    uint32_t last_spurious_id;
    uint32_t stack_free_min; /* bytes never used, valid after fw_stack_free() */
    uint32_t heartbeat;      /* incremented by fw_heartbeat() */
    uint32_t reserved;
} fw_status_t;

extern volatile fw_status_t fw_status;

/* Stop: put the hardware in its safe state, record why, halt forever.
 * The result code 1000+reason goes to the host mailbox (platform.h). */
__attribute__((noreturn)) void fw_panic(uint32_t reason, uint32_t detail);

/* Normal end of a program (main returning calls this). code 0 = success.
 * Hardware is put in the safe state, then the core sleeps. */
__attribute__((noreturn)) void fw_exit(uint32_t code);

/* Bytes of stack never touched since boot (stack is painted at startup).
 * Also checks the guard word; panics with FW_PANIC_STACK_OVERFLOW if hit. */
uint32_t fw_stack_free(void);

/* Liveness counter for the host. Cheap; call from the main loop. */
static inline void fw_heartbeat(void) { fw_status.heartbeat++; }

/* Hook: put every output the firmware controls into a safe state. Called by
 * fw_panic and fw_exit with interrupts disabled. The PE HAL provides it
 * (engines off, pads released); the default does nothing. */
void fw_safe_state(void);

/* Pins outside the PE block (sysctrl GPIO on the chip; nothing in FW_SIM
 * builds, where the testbench has no sysctrl). Called by fw_safe_state(). */
void fw_platform_safe_state(void);

#define FW_ASSERT(c) do { if (!(c)) fw_panic(FW_PANIC_ASSERT, __LINE__); } while (0)

/* Console output: testbench only (FW_SIM); a no-op on the chip. */
void fw_putc(char c);
void fw_puts(const char *s);
void fw_puthex(uint32_t v);   /* testbench "DBG 0x..." line */

uint32_t fw_crc32(uint32_t crc, const void *data, uint32_t len);

#endif /* __ASSEMBLER__ */
#endif /* FW_FW_H */
