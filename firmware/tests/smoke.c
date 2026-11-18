/* SoC smoke test: exercises the CPU, memory and system-control block, and
 * reports through GPIO and SCRATCH so the host (or cocotb) can check it.
 *
 * Result: main() returns 0x600D0000 | number_of_checks on success, or
 * 0xBAD00000 | first_failed_check. crt0 writes it to SCRATCH. */
#include "pemu.h"

static const uint8_t table[8] = {3, 1, 4, 1, 5, 9, 2, 6};   /* .rodata */
static uint32_t counter = 7;                                /* .data */
static uint32_t zeroed[4];                                  /* .bss */
static volatile uint8_t bytes[4];

static uint32_t fib(uint32_t n) { return n < 2 ? n : fib(n - 1) + fib(n - 2); }

#define CHECK(n, cond)                       \
  do {                                       \
    if (!(cond)) return 0xBAD00000u | (n);   \
  } while (0)

int main(void) {
  SYS_GPIO_OUT = 0x01;                              /* stage 1: running */

  CHECK(1, SYS_ID == PEMU_ID_VALUE);
  CHECK(2, counter == 7 && zeroed[0] == 0 && zeroed[3] == 0);

  uint32_t sum = 0;
  for (int i = 0; i < 8; i++) sum += table[i];
  CHECK(3, sum == 31);

  CHECK(4, fib(12) == 144);                         /* recursion: stack */

  volatile uint32_t a = 1000003, b = 977;
  CHECK(5, a * b == 977002931u);                    /* M extension */
  CHECK(6, a / b == 1023 && a % b == 532);

  bytes[0] = 0x11; bytes[1] = 0x22; bytes[2] = 0x33; bytes[3] = 0x44;
  CHECK(7, *(volatile uint32_t *)bytes == 0x44332211u);   /* byte enables */

  uint32_t t0 = SYS_CYCLE;
  uint32_t t1 = SYS_CYCLE;
  CHECK(8, t1 > t0);

  SYS_SCRATCH = 0x12345678;                         /* peripheral read-back */
  CHECK(9, SYS_SCRATCH == 0x12345678);

  SYS_UIO_OE = 0xF0;
  SYS_UIO_OUT = 0xA0;
  SYS_GPIO_OUT = 0x55;                              /* final pattern for the host */
  return 0x600D0000 | 9;
}
