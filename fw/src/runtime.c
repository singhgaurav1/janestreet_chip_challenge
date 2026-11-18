/*
 * Firmware runtime: boot checks, status block, panic and exit.
 * See fw.h for the safety model.
 */
#include <stdint.h>

#include "csr.h"
#include "fw.h"
#include "mmio.h"
#include "fw_internal.h"
#include "platform.h"

/* Linker-script symbols */
extern const fw_image_hdr_t fw_image_hdr;
extern uint32_t __stack_limit[];
extern uint32_t __stack_top[];

#define BOOT_TOKEN_FRESH 0xF0E5B007u  /* as loaded */
#define BOOT_TOKEN_USED  0x0DEADB07u  /* after the first boot */
#define STACK_GUARD      0x57ACC0DEu
#define STACK_PAINT      0xA5A5A5A5u

/* In .data on purpose: the image carries FRESH; a restart without a reload
 * finds USED. volatile so the compiler keeps the read and the write. */
static volatile uint32_t fw_boot_token = BOOT_TOKEN_FRESH;

/* ---------------------------------------------------------------- CRC-32
 * IEEE 802.3 (reflected, poly 0xEDB88320), nibble table: 64 bytes of table,
 * about 12 instructions per byte. Pass crc = 0 to start, fold more data by
 * passing the previous result. */
/* Boot-path code and data live in .text.boot/.rodata.boot, linked directly
 * after the reset code, so a load that lost the tail of the image still runs
 * an intact checker (fw/link/fw.ld asserts the boot path ends below 1 KiB). */
#define BOOT_TEXT   __attribute__((section(".text.boot")))
#define BOOT_RODATA __attribute__((section(".rodata.boot")))

BOOT_RODATA static const uint32_t crc_nibble[16] = {
    0x00000000u, 0x1DB71064u, 0x3B6E20C8u, 0x26D930ACu,
    0x76DC4190u, 0x6B6B51F4u, 0x4DB26158u, 0x5005713Cu,
    0xEDB88320u, 0xF00F9344u, 0xD6D6A3E8u, 0xCB61B38Cu,
    0x9B64C2B0u, 0x86D3D2D4u, 0xA00AE278u, 0xBDBDF21Cu,
};

BOOT_TEXT uint32_t fw_crc32(uint32_t crc, const void *data, uint32_t len)
{
    const uint8_t *p = (const uint8_t *)data;
    crc = ~crc;
    while (len--) {
        crc ^= *p++;
        crc = (crc >> 4) ^ crc_nibble[crc & 15u];
        crc = (crc >> 4) ^ crc_nibble[crc & 15u];
    }
    return ~crc;
}

/* ------------------------------------------------------------- status/panic */
static uint32_t read_sp(void)
{
    uint32_t sp;
    __asm__ volatile("mv %0, sp" : "=r"(sp));
    return sp;
}

static void status_begin(fw_state_t state)
{
    fw_status.magic = FW_STATUS_MAGIC;
    fw_status.state = state;
}

__attribute__((weak)) void fw_safe_state(void) {}

BOOT_TEXT __attribute__((noreturn)) static void halt(uint32_t sim_code)
{
#ifdef FW_SIM
    mmio_write32(FW_SIM_TOHOST, sim_code == 0 ? 1u : ((sim_code << 1) | 1u));
#else
    (void)sim_code;
#endif
    csr_write(mie, 0);
    for (;;)
        __asm__ volatile("wfi");
}

/* Common fatal path. A fault while already panicking (for example in
 * fw_safe_state) halts at once instead of recursing. */
__attribute__((noreturn)) static void panic_common(uint32_t reason, uint32_t detail,
                                                   uint32_t ra, uint32_t sp)
{
    csr_clear(mstatus, CSR_MSTATUS_MIE);
    csr_write(mie, 0);
    if (fw_status.state == FW_STATE_PANIC)
        halt(FW_SIM_EXIT_PANIC_BASE + reason);
    fw_status.reason = reason;
    fw_status.detail = detail;
    fw_status.ra = ra;
    fw_status.sp = sp;
    uint64_t c = fw_cycles64();
    fw_status.cycles_lo = (uint32_t)c;
    fw_status.cycles_hi = (uint32_t)(c >> 32);
    fw_status.magic = FW_STATUS_MAGIC;
    fw_status.state = FW_STATE_PANIC;   /* last: record is complete */
    fw_safe_state();
    halt(FW_SIM_EXIT_PANIC_BASE + reason);
}

__attribute__((noreturn)) void fw_panic(uint32_t reason, uint32_t detail)
{
    fw_status.mcause = 0;
    fw_status.mepc = 0;
    fw_status.mtval = 0;
    panic_common(reason, detail, (uint32_t)(uintptr_t)__builtin_return_address(0), read_sp());
}

/* Called from start.S on the panic stack. */
__attribute__((noreturn)) void fw_trap_exception(uint32_t sp, uint32_t ra)
{
    uint32_t mcause = csr_read(mcause);
    fw_status.mcause = mcause;
    fw_status.mepc = csr_read(mepc);
    fw_status.mtval = csr_read(mtval);
    panic_common(FW_PANIC_EXCEPTION, mcause, ra, sp);
}

__attribute__((noreturn)) void fw_trap_nmi(uint32_t sp, uint32_t ra)
{
    fw_status.mcause = csr_read(mcause);
    fw_status.mepc = csr_read(mepc);
    fw_status.mtval = csr_read(mtval);
    panic_common(FW_PANIC_NMI, fw_status.mcause, ra, sp);
}

__attribute__((noreturn)) void fw_trap_stack_overflow(uint32_t sp, uint32_t ra)
{
    fw_status.mcause = csr_read(mcause);
    fw_status.mepc = csr_read(mepc);
    fw_status.mtval = 0;
    panic_common(FW_PANIC_STACK_OVERFLOW, (uint32_t)(uintptr_t)__stack_limit, ra, sp);
}

__attribute__((noreturn)) void fw_exit(uint32_t code)
{
    csr_clear(mstatus, CSR_MSTATUS_MIE);
    csr_write(mie, 0);
    fw_stack_free();                    /* records the high-water mark; checks the guard */
    fw_safe_state();
    fw_status.reason = code;
    fw_status.state = FW_STATE_EXITED;
    halt(code);
}

uint32_t fw_stack_free(void)
{
    if (__stack_limit[0] != STACK_GUARD)
        fw_panic(FW_PANIC_STACK_OVERFLOW, (uint32_t)(uintptr_t)__stack_limit);
    const uint32_t *p = &__stack_limit[1];
    while (p < __stack_top && *p == STACK_PAINT)
        p++;
    uint32_t free_bytes = (uint32_t)((uintptr_t)p - (uintptr_t)&__stack_limit[1]);
    fw_status.stack_free_min = free_bytes;
    return free_bytes;
}

/* ------------------------------------------------------------- boot */

/* Boot-time failure: record and halt. Deliberately does NOT call
 * fw_safe_state(): the firmware has configured nothing yet, and the code it
 * would call may be missing or corrupt (that is why we are here). */
BOOT_TEXT __attribute__((noreturn)) static void boot_fail(uint32_t reason, uint32_t detail)
{
    fw_status.reason = reason;
    fw_status.detail = detail;
    fw_status.state = FW_STATE_PANIC;
    halt(FW_SIM_EXIT_PANIC_BASE + reason);
}

/* Runs before .bss is zeroed: stack, image and fw_status only. */
BOOT_TEXT void fw_boot_checks(void)
{
    /* RAM powers up random: clear the whole block, then mark it valid. */
    volatile uint32_t *w = (volatile uint32_t *)&fw_status;
    for (uint32_t i = 0; i < sizeof(fw_status_t) / 4u; i++)
        w[i] = 0;
    status_begin(FW_STATE_BOOTING);

    /* 1. Started twice from one load? (checked first: a restart also
     *    changes .data, which would otherwise show up as a CRC error) */
    uint32_t token = fw_boot_token;
    if (token == BOOT_TOKEN_USED)
        boot_fail(FW_PANIC_DIRTY_RESTART, token);

    /* 2. Image intact? CRC over [base, base + length) with the crc field
     *    taken as zero. */
    const fw_image_hdr_t *h = &fw_image_hdr;
    const uint8_t *base = (const uint8_t *)(uintptr_t)FW_RAM_BASE;
    uint32_t len = h->length;
    uint32_t crc_off = FW_IMAGE_HDR_OFF + 8u;
    if (h->magic != FW_IMAGE_MAGIC || len < crc_off + 4u || len > FW_RAM_SIZE)
        boot_fail(FW_PANIC_IMAGE_CRC, 0);
    BOOT_RODATA static const uint8_t zero4[4] = {0, 0, 0, 0};
    uint32_t crc = fw_crc32(0, base, crc_off);
    crc = fw_crc32(crc, zero4, 4);
    crc = fw_crc32(crc, base + crc_off + 4u, len - crc_off - 4u);
    if (crc != h->crc32 || token != BOOT_TOKEN_FRESH)
        boot_fail(FW_PANIC_IMAGE_CRC, crc);

    fw_boot_token = BOOT_TOKEN_USED;
}

void fw_runtime_init(void)
{
    status_begin(FW_STATE_RUNNING);
}

/* ------------------------------------------------------------- console */
void fw_putc(char c)
{
#ifdef FW_SIM
    mmio_write32(FW_SIM_PUTC, (uint8_t)c);
#else
    (void)c;
#endif
}

void fw_puts(const char *s)
{
    while (*s)
        fw_putc(*s++);
}

void fw_puthex(uint32_t v)
{
#ifdef FW_SIM
    mmio_write32(FW_SIM_PUTHEX, v);
#else
    (void)v;
#endif
}
