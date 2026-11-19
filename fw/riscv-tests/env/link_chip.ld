/* riscv-tests on the full chip (docs/soc.md): 4 KiB SRAM at 0, tohost is the
   sysctrl SCRATCH register that the host polls over SPI. */
OUTPUT_ARCH("riscv")
ENTRY(_start)

MEMORY
{
  RAM (rwx) : ORIGIN = 0x00000000, LENGTH = 4K
  SCRATCH (rw) : ORIGIN = 0x10000018, LENGTH = 16
}

SECTIONS
{
  .text.init : { *(.text.init) } > RAM
  .text : { *(.text) } > RAM
  .data : { *(.data) } > RAM
  .bss : { *(.bss) } > RAM
  _end = .;
  .tohost (NOLOAD) : { *(.tohost) } > SCRATCH
}
