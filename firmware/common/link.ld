/* Protocol emulator firmware layout: everything in the 2 KiB SRAM.
 * The host loads the whole image over SPI, so .data needs no copy. */
OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY { RAM (rwx) : ORIGIN = 0x00000000, LENGTH = 2K }
SECTIONS {
  .vectors 0x00 : { KEEP(*(.vectors)) } > RAM      /* Ibex mtvec = boot_addr, vectored */
  .text 0x80 : { KEEP(*(.text.start)) *(.text .text.*) } > RAM   /* reset vector 0x80 */
  .rodata : { *(.rodata .rodata.* .srodata .srodata.*) } > RAM
  .data : { *(.data .data.* .sdata .sdata.*) } > RAM
  .bss (NOLOAD) : { __bss_start = .; *(.bss .bss.* .sbss .sbss.* COMMON) . = ALIGN(4); __bss_end = .; } > RAM
  __image_end = __bss_start;
  __stack_top = ORIGIN(RAM) + LENGTH(RAM);
  /DISCARD/ : { *(.comment) *(.riscv.attributes) *(.note*) *(.eh_frame*) }
}
