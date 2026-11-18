/* Interrupt registration and dispatch. See irq.h. */
#include <stdint.h>

#include "csr.h"
#include "fw.h"
#include "fw_internal.h"
#include "irq.h"

static irq_handler_t handlers[IRQ_NUM_IDS];

static int maskable(uint32_t id)
{
    return id == IRQ_ID_SOFTWARE || id == IRQ_ID_TIMER || id == IRQ_ID_EXTERNAL ||
           (id >= IRQ_ID_FAST(0) && id <= IRQ_ID_FAST(14));
}

int irq_register(uint32_t id, irq_handler_t fn)
{
    if (id >= IRQ_NUM_IDS || !maskable(id))
        return -1;
    uint32_t s = irq_save();
    handlers[id] = fn;
    irq_restore(s);
    return 0;
}

int irq_enable(uint32_t id)
{
    if (id >= IRQ_NUM_IDS || !maskable(id))
        return -1;
    csr_set(mie, 1u << id);
    return 0;
}

int irq_disable(uint32_t id)
{
    if (id >= IRQ_NUM_IDS || !maskable(id))
        return -1;
    csr_clear(mie, 1u << id);
    return 0;
}

void irq_global_enable(void) { csr_set(mstatus, CSR_MSTATUS_MIE); }

uint32_t irq_save(void) { return csr_read_clear(mstatus, CSR_MSTATUS_MIE) & CSR_MSTATUS_MIE; }

void irq_restore(uint32_t saved)
{
    if (saved & CSR_MSTATUS_MIE)
        csr_set(mstatus, CSR_MSTATUS_MIE);
}

/* Called from start.S with interrupts disabled (hardware cleared MIE). */
void fw_irq_dispatch(uint32_t mcause)
{
    uint32_t id = mcause & 31u;
    irq_handler_t fn = handlers[id];
    if (fn) {
        fn(id);
        return;
    }
    /* Level-sensitive line with nobody to clear it: disable it so it cannot
     * starve the firmware, and record it for the host. */
    csr_clear(mie, 1u << id);
    fw_status.spurious_irqs++;
    fw_status.last_spurious_id = id;
}
