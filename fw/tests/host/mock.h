#ifndef FW_TEST_MOCK_H
#define FW_TEST_MOCK_H
#include <stdint.h>

#include "mmio.h"
#include "pe_regs.h"

#define MOCK_LOG_MAX 4096

typedef struct {
    char op;            /* 'R' or 'W' */
    uint32_t addr, value;
} mock_access_t;

typedef struct {
    mock_access_t log[MOCK_LOG_MAX];
    int nlog;
    uint32_t status[4];        /* value returned for STATUS reads */
    uint32_t stall_reads[4];   /* clear STATUS.stalled after this many reads */
    uint32_t rx_value[4];
    uint32_t rx_pops[4];
    uint32_t evt;
    uint32_t mie;              /* Ibex interrupt enables set by the HAL */
    uint64_t cycles, cycle_step;
} mock_t;

extern mock_t mock;
void mock_reset(void);
int mock_count_writes(uint32_t addr);
int mock_last_write(uint32_t addr, uint32_t *value);  /* log index or -1 */

#endif
