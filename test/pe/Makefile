# Lockstep tests: Protocol Engine RTL (pe_block) against the peemu golden model.
#   make                     # all seeds in test_pe_lockstep.py
#   make SEEDS=0:200         # seed range
SIM ?= icarus
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../../src
VERILOG_SOURCES += $(SRC_DIR)/pe_block.v $(SRC_DIR)/pe_engine.v $(SRC_DIR)/pe_fifo.v $(SRC_DIR)/pemu_cells.v
COMPILE_ARGS += -g2012
COCOTB_TOPLEVEL = pe_block
COCOTB_TEST_MODULES = test_pe_lockstep
include $(shell cocotb-config --makefiles)/Makefile.sim
