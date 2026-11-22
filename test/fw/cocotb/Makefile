# Firmware protocol tests (F4): the full chip in Icarus with cocotb and
# third-party VIPs. Images come from fw/build (make -C fw).
#   make                      all test modules
#   make MODULE=test_uart     one module;  TESTCASE=test_uart_1m  one test
SIM ?= icarus
TOPLEVEL_LANG ?= verilog
ROOT := $(abspath $(CURDIR)/../../..)
TEST_MK := $(ROOT)/test/Makefile
# RTL sources and the SRAM model exactly as test/Makefile lists them
# (same approach as ../build_tb.sh)
mkvar = $(shell sed -n '/^$(1) *=/,/[^\\]$$/p' $(TEST_MK) | sed 's/^$(1) *=//; s/\\//g')
PROJECT_SOURCES := $(call mkvar,PROJECT_SOURCES)
SRAM_MODEL := $(subst $$(PWD),$(ROOT)/test,$(call mkvar,SRAM_MODEL))
ifeq ($(words $(PROJECT_SOURCES)),0)
  $(error could not read PROJECT_SOURCES from $(TEST_MK))
endif
VERILOG_SOURCES := $(addprefix $(ROOT)/src/,$(PROJECT_SOURCES)) $(SRAM_MODEL) $(CURDIR)/tb_proto.v
COMPILE_ARGS += -DSIM -DFUNCTIONAL -I$(ROOT)/src
TOPLEVEL := tb_proto
MODULE ?= test_uart
COCOTB_TEST_MODULES := $(MODULE)
SIM_BUILD := sim_build
export PYTHONPATH := $(CURDIR):$(PYTHONPATH)
include $(shell cocotb-config --makefiles)/Makefile.sim
