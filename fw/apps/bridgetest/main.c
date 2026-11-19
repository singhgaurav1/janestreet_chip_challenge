/*
 * Host bridge while the CPU runs (docs/soc.md: "The bridge has bus priority,
 * so it works while the CPU runs"). Run by test/fw/tb_chip_fw.sv with
 * +bridge_tests: the host peeks the heartbeat, sends junk frames, then pokes
 * MAILBOX_GO into fw_status.reserved.
 *
 * Meanwhile this program keeps the bus busy: it re-checks the CRC of its own
 * image (every word read through the shared bus) and counts a heartbeat per
 * pass. Returns 0 once the poke arrives with every CRC intact; 1 if a CRC
 * pass ever disagrees (the host's accesses disturbed CPU loads); 2 on timeout.
 */
#include <stdint.h>

#include "csr.h"
#include "fw.h"
#include "platform.h"

#define MAILBOX_GO 0xC0FFEE01u

extern const uint8_t __data_start[];  /* linker script */

#define CHUNK 64u

/* CRC of code + rodata (everything before .data, which never changes; .data
 * holds the boot token, rewritten at boot). The header's crc field counts as
 * zero, as in the boot check. Computed in CHUNK-byte steps; between steps it
 * counts a heartbeat and (if watch_mailbox) checks the mailbox, so the host
 * sees progress every ~3k cycles. Returns 1 if the mailbox was poked before
 * the pass finished. */
static int image_crc(uint32_t *out, int watch_mailbox)
{
    const uint8_t *p = (const uint8_t *)(uintptr_t)FW_RAM_BASE;
    const uint8_t *end = __data_start;
    const uint8_t *crc_field = p + FW_IMAGE_HDR_OFF + 8u;
    uint32_t crc = 0;
    while (p < end) {
        uint32_t n = (uint32_t)(end - p) < CHUNK ? (uint32_t)(end - p) : CHUNK;
        for (uint32_t i = 0; i < n; i++, p++) {
            uint8_t b = (p >= crc_field && p < crc_field + 4) ? 0u : *p;
            crc = fw_crc32(crc, &b, 1);
        }
        fw_heartbeat();
        uint32_t m = fw_status.reserved;
        fw_status.detail = m;          /* what the CPU sees, for the host */
        if (watch_mailbox && m == MAILBOX_GO)
            return 1;
    }
    *out = crc;
    return 0;
}

int main(void)
{
    uint32_t ref = 0, crc = 0;
    fw_status.reserved = 0;
    (void)image_crc(&ref, 0);   /* reference pass; the poke may already arrive here */
    uint32_t t0 = fw_cycles32();
    for (;;) {
        if (image_crc(&crc, 1))
            return 0;
        if (crc != ref)
            return 1;
        if (fw_elapsed(t0, 3000000u))   /* 50 ms */
            return 2;
    }
}
