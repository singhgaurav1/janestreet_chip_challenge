/*
 * Firmware linker script: one RAM, loaded in place by the host over the SPI
 * boot bridge (no ROM, no copy of .data). Pass the RAM size with
 * -Wl,--defsym=FW_RAM_SIZE=<bytes> (default 4 KiB).
 *
 *   RAM_BASE ............ vectors, reset jump, image header, code, data
 *   ...      ............ .bss
 *   __stack_limit ....... guard word, then the stack (grows down)
 *   __stack_top ......... = __panic_stack_bottom
 *   __panic_stack_top ... 128 B used only by fatal trap handlers
 *   RAM_END - 64 ........ fw_status (host-readable status block)
 */
OUTPUT_ARCH("riscv")
ENTRY(_start)

FW_RAM_BASE = DEFINED(FW_RAM_BASE) ? FW_RAM_BASE : 0x00000000;
FW_RAM_SIZE = DEFINED(FW_RAM_SIZE) ? FW_RAM_SIZE : 4096;
FW_STACK_MIN = DEFINED(FW_STACK_MIN) ? FW_STACK_MIN : 512;
FW_STATUS_SIZE = 64;
FW_PANIC_STACK = 128;

MEMORY
{
  RAM (rwx) : ORIGIN = 0x00000000, LENGTH = 1M  /* bounds enforced by ASSERTs below */
}

SECTIONS
{
  . = FW_RAM_BASE;
  .vectors : {
    KEEP(*(.vectors))
    ASSERT(. == FW_RAM_BASE + 0x94, "vectors + reset jump + 16-byte header must end at 0x94");
  } > RAM
  .text : {
    KEEP(*(.text.start))
    *(.text.boot)             /* boot checks first: see runtime.c */
    *(.rodata.boot)
    . = ALIGN(4);
    __boot_end = .;
    *(.text .text.*)
  } > RAM
  ASSERT(__boot_end <= FW_RAM_BASE + 0x400, "boot path must end below 1 KiB")
  .rodata : ALIGN(4) {
    *(.srodata .srodata.*)
    *(.rodata .rodata.*)
  } > RAM
  .data : ALIGN(4) {
    __data_start = .;
    *(.data .data.*)
    __global_pointer$ = . + 0x800;
    *(.sdata .sdata.*)
    . = ALIGN(4);
    __image_end = .;           /* everything the host loads ends here */
  } > RAM
  .bss (NOLOAD) : ALIGN(4) {
    __bss_start = .;
    *(.sbss .sbss.*)
    *(.bss .bss.*)
    *(COMMON)
    . = ALIGN(4);
    __bss_end = .;
  } > RAM

  __stack_limit = ALIGN(__bss_end, 16);
  fw_status = FW_RAM_BASE + FW_RAM_SIZE - FW_STATUS_SIZE;
  __panic_stack_top = fw_status;
  __stack_top = __panic_stack_top - FW_PANIC_STACK;

  ASSERT(__stack_limit + FW_STACK_MIN <= __stack_top,
         "firmware too large: less than FW_STACK_MIN bytes left for the stack")
  ASSERT((FW_RAM_BASE & 0xFF) == 0, "boot address must be 256-byte aligned (Ibex mtvec)")

  /DISCARD/ : { *(.comment) *(.note .note.*) *(.riscv.attributes) *(.eh_frame*) }
}
