/* Functions called only from start.S (declared here for -Wmissing-prototypes). */
#ifndef FW_INTERNAL_H
#define FW_INTERNAL_H
#include <stdint.h>
void fw_boot_checks(void);
void fw_runtime_init(void);
void fw_irq_dispatch(uint32_t mcause);
__attribute__((noreturn)) void fw_trap_exception(uint32_t sp, uint32_t ra);
__attribute__((noreturn)) void fw_trap_nmi(uint32_t sp, uint32_t ra);
__attribute__((noreturn)) void fw_trap_stack_overflow(uint32_t sp, uint32_t ra);
int main(void);
#endif
