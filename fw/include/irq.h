/*
 * Interrupts on Ibex (Ibex docs, "Exceptions and Interrupts"):
 *   - vectored: interrupt ID n enters at mtvec + 4n; exceptions at mtvec + 0
 *   - IDs: 3 software, 7 timer, 11 external, 16..30 fast (irq_fast_i[0..14]),
 *     31 NMI. Fast interrupts have priority over the others, lowest ID first.
 *   - all lines are level-sensitive: a handler must clear the source before
 *     returning or it is re-entered at once.
 *   - no nesting: the core clears mstatus.MIE on entry.
 *
 * An interrupt that fires with no registered handler is disabled (in mie)
 * and counted in fw_status.spurious_irqs; it does not stop the firmware.
 */
#ifndef FW_IRQ_H
#define FW_IRQ_H

#include <stdint.h>

#define IRQ_ID_SOFTWARE 3u
#define IRQ_ID_TIMER    7u
#define IRQ_ID_EXTERNAL 11u
#define IRQ_ID_FAST(n)  (16u + (n))   /* n = 0..14 */
#define IRQ_ID_NMI      31u
#define IRQ_NUM_IDS     32u

typedef void (*irq_handler_t)(uint32_t id);

/* Returns 0, or -1 if id cannot be a maskable interrupt on Ibex. Registering
 * does not enable; enabling an ID with no handler is allowed (it will then be
 * counted as spurious and disabled when it fires). */
int irq_register(uint32_t id, irq_handler_t fn);
int irq_enable(uint32_t id);
int irq_disable(uint32_t id);

/* Global enable. irq_save() disables and returns the previous state for
 * irq_restore(); use the pair around short critical sections. */
void irq_global_enable(void);
uint32_t irq_save(void);
void irq_restore(uint32_t saved);

#endif /* FW_IRQ_H */
