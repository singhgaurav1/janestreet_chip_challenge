/* riscv-tests on the firmware testbench: RAM at 0 (boot address), tohost in
   the testbench's simulation-only control device at 0x2000_0000. */
OUTPUT_ARCH("riscv")
ENTRY(_start)

MEMORY
{
  RAM (rwx) : ORIGIN = 0x00000000, LENGTH = 64K
  SIM (rw)  : ORIGIN = 0x20000000, LENGTH = 256
}

SECTIONS
{
  .text.init : { *(.text.init) } > RAM
  .text : { *(.text) } > RAM
  .data : { *(.data) } > RAM
  .bss : { *(.bss) } > RAM
  _end = .;
  .tohost (NOLOAD) : { *(.tohost) } > SIM
}
