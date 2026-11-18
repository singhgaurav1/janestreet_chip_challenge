/*
 * Memory-mapped register access. All hardware access in the firmware goes
 * through these two functions, so host unit tests can replace them with a
 * recording mock (FW_HOST_TEST) and nothing else changes.
 *
 * Ibex is in order with no caches or store buffer visible to software, so
 * volatile accesses are performed in program order; no fences are needed
 * between MMIO accesses.
 */
#ifndef FW_MMIO_H
#define FW_MMIO_H

#include <stdint.h>

#ifdef FW_HOST_TEST
uint32_t mmio_read32(uint32_t addr);
void mmio_write32(uint32_t addr, uint32_t value);
#else
static inline uint32_t mmio_read32(uint32_t addr)
{
    return *(volatile uint32_t *)(uintptr_t)addr;
}

static inline void mmio_write32(uint32_t addr, uint32_t value)
{
    *(volatile uint32_t *)(uintptr_t)addr = value;
}
#endif

/* Field helpers. width must be 1..32. */
#define FW_FIELD_MASK(width) ((width) >= 32 ? 0xFFFFFFFFu : ((1u << (width)) - 1u))
#define FW_FIELD_GET(reg, lsb, width) (((reg) >> (lsb)) & FW_FIELD_MASK(width))

#endif /* FW_MMIO_H */
