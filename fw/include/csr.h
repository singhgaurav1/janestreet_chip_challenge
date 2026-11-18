/*
 * Ibex CSR helpers and the cycle counter. Source: Ibex documentation
 * ("Control and Status Registers", "Performance Counters"): mcycle/minstret
 * are 64-bit, enabled from reset, low/high halves read separately.
 */
#ifndef FW_CSR_H
#define FW_CSR_H

#include <stdint.h>

#define CSR_MSTATUS_MIE   (1u << 3)
#define CSR_MCAUSE_IRQ    (1u << 31)

#ifdef FW_HOST_TEST
/* Host tests drive time explicitly. */
uint64_t fw_host_cycles(void);
static inline uint64_t fw_cycles64(void) { return fw_host_cycles(); }
static inline uint32_t fw_cycles32(void) { return (uint32_t)fw_host_cycles(); }
#else

#define csr_read(csr) ({ uint32_t v_; __asm__ volatile("csrr %0, " #csr : "=r"(v_)); v_; })
#define csr_write(csr, v) __asm__ volatile("csrw " #csr ", %0" ::"rK"((uint32_t)(v)) : "memory")
#define csr_set(csr, v) __asm__ volatile("csrs " #csr ", %0" ::"rK"((uint32_t)(v)) : "memory")
#define csr_clear(csr, v) __asm__ volatile("csrc " #csr ", %0" ::"rK"((uint32_t)(v)) : "memory")
/* Atomically clear bits and return the old value (csrrc). */
#define csr_read_clear(csr, v) ({ uint32_t o_; \
    __asm__ volatile("csrrc %0, " #csr ", %1" : "=r"(o_) : "rK"((uint32_t)(v)) : "memory"); o_; })

/* 32-bit cycle count: wraps every 71.6 s at 60 MHz. Use for intervals only,
 * with unsigned subtraction. */
static inline uint32_t fw_cycles32(void) { return csr_read(mcycle); }

/* 64-bit cycle count, consistent across a low-half carry. */
static inline uint64_t fw_cycles64(void)
{
    uint32_t hi, lo, hi2;
    do {
        hi = csr_read(mcycleh);
        lo = csr_read(mcycle);
        hi2 = csr_read(mcycleh);
    } while (hi != hi2);
    return ((uint64_t)hi << 32) | lo;
}
#endif

/* True once at least `cycles` have elapsed since `start` (wrap-safe for
 * intervals below 2^31 cycles, about 35 s at 60 MHz). */
static inline int fw_elapsed(uint32_t start, uint32_t cycles)
{
    return (uint32_t)(fw_cycles32() - start) >= cycles;
}

#endif /* FW_CSR_H */
