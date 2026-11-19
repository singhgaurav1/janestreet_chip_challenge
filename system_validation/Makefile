# System validation images (see README.md).
#   make            build every test for the chip and refresh images/*.bin
#   make clean
# Needs the same toolchain as fw/ (riscv64-unknown-elf-gcc 13).
# chip SRAM size (docs/soc.md)
RAM_SIZE ?= 4096

.PHONY: all clean
all:
	$(MAKE) --no-print-directory -C ../fw -f $(CURDIR)/sysval.mk SV=$(CURDIR) \
	  TARGET=chip RAM_SIZE=$(RAM_SIZE) sysval

clean:
	rm -rf build
