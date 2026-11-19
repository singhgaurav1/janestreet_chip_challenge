# System-validation images, built with the firmware tree's own infrastructure.
#
# Run from fw/ (system_validation/Makefile does this): it includes fw/Makefile,
# so the toolchain, flags, startup code (src/start.S), runtime, linker script
# (link/fw.ld), image header/CRC (scripts/mkimage.py) and stack check
# (scripts/stack_check.py) are exactly the ones the product firmware uses.
# Only the test sources live in system_validation/tests/<name>/*.c.
#
# Output: $(SV)/build/<arch>_<target>/<name>.{elf,bin,hex,map}; the chip image
# is also copied to $(SV)/images/<name>.bin (committed; cocotb loads it).

SV ?= ../system_validation
include Makefile

SV_BUILD := $(SV)/build/$(ARCH)_$(TARGET)
SV_TESTS := $(notdir $(wildcard $(SV)/tests/*))

define sv_rules
$(SV_BUILD)/$(1).elf: $(RT_SRC) $(wildcard $(SV)/tests/$(1)/*.c) link/fw.ld \
                      $(wildcard include/*.h src/*.h) Makefile $(SV)/sysval.mk
	@mkdir -p $(SV_BUILD)/obj-$(1)
	@echo "CC  system_validation/$(1) [$(ARCH), $(TARGET), RAM $(RAM_SIZE)]"
	@cd $(SV_BUILD)/obj-$(1) && $(CC) $(CFLAGS) -I$(CURDIR)/include -I$(CURDIR)/src \
	  -c $(addprefix $(CURDIR)/,$(RT_SRC)) $(abspath $(wildcard $(SV)/tests/$(1)/*.c))
	@$(CC) $(LDFLAGS) -Wl,-Map=$(SV_BUILD)/$(1).map -o $$@ $(SV_BUILD)/obj-$(1)/*.o $(LIBS)
$(SV_BUILD)/$(1).hex: $(SV_BUILD)/$(1).elf scripts/mkimage.py
	@$(PYTHON) scripts/stack_check.py --objdump $(CROSS)objdump --nm $(NM) $$< $(SV_BUILD)/obj-$(1)
	@$(PYTHON) scripts/mkimage.py --objcopy $(OBJCOPY) --nm $(NM) $$< $(SV_BUILD)/$(1)
	@if [ "$(TARGET)" = chip ]; then mkdir -p $(SV)/images && cp $(SV_BUILD)/$(1).bin $(SV)/images/$(1).bin; fi
SV_IMAGES += $(SV_BUILD)/$(1).hex
endef

$(foreach t,$(SV_TESTS),$(eval $(call sv_rules,$(t))))

.PHONY: sysval
sysval: $(SV_IMAGES)
