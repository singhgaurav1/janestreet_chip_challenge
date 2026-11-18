# Firmware build. Targets:
#   make                     all apps for ARCH (default rv32imc)
#   make ARCH=rv32ec         same for the Ibex "micro" fallback (RV32EC)
#   make host-test           HAL unit tests on the build machine
#   make sizes               size report for every app
#   make clean
# Variables: RAM_SIZE (bytes, default 4096), SIM=1 (testbench console and
# pass/fail device; default for the apps here), CROSS (toolchain prefix).

ARCH     ?= rv32imc
RAM_SIZE ?= 4096
SIM      ?= 1
CROSS    ?= riscv64-unknown-elf-
CC       := $(CROSS)gcc
OBJCOPY  := $(CROSS)objcopy
NM       := $(CROSS)nm
HOSTCC   ?= cc
PYTHON   ?= python3

ifeq ($(ARCH),rv32imc)
  MARCH := rv32imc_zicsr
  MABI  := ilp32
else ifeq ($(ARCH),rv32ec)
  MARCH := rv32ec_zicsr
  MABI  := ilp32e
else
  $(error ARCH must be rv32imc or rv32ec)
endif

BUILD := build/$(ARCH)_ram$(RAM_SIZE)
.DEFAULT_GOAL := all
WARN  := -Wall -Wextra -Werror -Wshadow -Wundef -Wcast-align -Wstrict-prototypes \
         -Wmissing-prototypes -Wconversion -Wno-sign-conversion -Wvla
CFLAGS := -march=$(MARCH) -mabi=$(MABI) -Os -g -std=c11 -ffreestanding \
          -fno-common -ffunction-sections -fdata-sections -msmall-data-limit=8 \
          -fno-tree-loop-distribute-patterns -fstack-usage $(WARN) \
          -Iinclude -Isrc -DFW_RAM_SIZE=$(RAM_SIZE)u $(CFLAGS_EXTRA)
ifeq ($(SIM),1)
  CFLAGS += -DFW_SIM
endif
LDFLAGS := -march=$(MARCH) -mabi=$(MABI) -nostdlib -nostartfiles -T link/fw.ld \
           -Wl,--defsym=FW_RAM_SIZE=$(RAM_SIZE) -Wl,--gc-sections \
           -Wl,--no-warn-rwx-segments
LIBS    := -lgcc

RT_SRC  := src/start.S src/runtime.c src/irq.c src/libc.c
HAL_SRC := src/pe_hal.c
APPS    := $(notdir $(wildcard apps/*))

# Per-app extras: apps/<app>/app.mk may set <app>_SRC_EXTRA, <app>_CFLAGS,
# <app>_HAL (=1 to link the PE HAL) and <app>_VARIANTS (builds <app>-<v>
# with -DVARIANT=<v>).
-include $(wildcard apps/*/app.mk)

define app_rules
# $(1) image name, $(2) app dir, $(3) extra cflags
$(BUILD)/$(1).elf: $(RT_SRC) $(if $($(2)_HAL),$(HAL_SRC)) $(wildcard apps/$(2)/*.c) $($(2)_SRC_EXTRA) \
                   link/fw.ld $(wildcard include/*.h src/*.h) Makefile
	@mkdir -p $(BUILD)/obj-$(1)
	@echo "CC  $(1) [$(ARCH), RAM $(RAM_SIZE)]"
	@cd $(BUILD)/obj-$(1) && $(CC) $(CFLAGS) $($(2)_CFLAGS) $(3) -I$(CURDIR)/include -I$(CURDIR)/src \
	  -c $(addprefix $(CURDIR)/,$(RT_SRC) $(if $($(2)_HAL),$(HAL_SRC)) $(wildcard apps/$(2)/*.c) $($(2)_SRC_EXTRA))
	@$(CC) $(LDFLAGS) -Wl,-Map=$(BUILD)/$(1).map -o $$@ $(BUILD)/obj-$(1)/*.o $(LIBS)
$(BUILD)/$(1).hex: $(BUILD)/$(1).elf scripts/mkimage.py
	@$(PYTHON) scripts/mkimage.py --objcopy $(OBJCOPY) --nm $(NM) $$< $(BUILD)/$(1)
IMAGES += $(BUILD)/$(1).hex
endef

$(foreach a,$(APPS),$(if $($(a)_VARIANTS),\
  $(foreach v,$($(a)_VARIANTS),$(eval $(call app_rules,$(a)-$(v),$(a),-DVARIANT=$(v)))),\
  $(eval $(call app_rules,$(a),$(a),))))

.PHONY: all clean host-test sizes
all: $(IMAGES)

sizes: $(IMAGES)
	@for f in $(IMAGES); do $(PYTHON) scripts/mkimage.py --objcopy $(OBJCOPY) --nm $(NM) $${f%.hex}.elf $${f%.hex} ; done

# ---- Host unit tests (HAL logic and register packing, mock MMIO) ----
HOST_BUILD := build/host
HOST_CFLAGS := -std=c11 -O1 -g -Wall -Wextra -Werror -Wshadow -Wconversion -Wno-sign-conversion \
               -fsanitize=address,undefined -fno-sanitize-recover=all \
               -DFW_HOST_TEST -Iinclude -Isrc -Itests/host
$(HOST_BUILD)/test_pe_hal: $(HAL_SRC) $(wildcard tests/host/*.c tests/host/*.h include/*.h) Makefile
	@mkdir -p $(HOST_BUILD)
	$(HOSTCC) $(HOST_CFLAGS) -o $@ $(HAL_SRC) $(wildcard tests/host/*.c)
host-test: $(HOST_BUILD)/test_pe_hal
	$(HOST_BUILD)/test_pe_hal

clean:
	rm -rf build
