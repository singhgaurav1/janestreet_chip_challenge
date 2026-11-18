# Firmware testbench (standalone Ibex)

Owner: firmware chat. `tb_ibex_fw.sv` runs firmware on Ibex alone, before the
full chip exists. It uses only Ibex's documented top-level interface. It is
**not** the chip: there are no engines, and the memory map is the
testbench's own.

```sh
test/fw/build_tb.sh conv        # src/ibex_small.v (the converted core)   -> obj/Vtb
test/fw/build_tb.sh ref         # upstream Ibex SV, same parameters      -> obj/Vtb_ref
test/fw/build_tb.sh ref_micro   # upstream Ibex SV, micro (RV32EC)       -> obj/Vtb_ref_micro
obj/Vtb +img=fw/build/rv32imc_ram4096/selftest.hex +verilator+rand+reset+2
```

The plusargs are documented at the top of `tb_ibex_fw.sv`: RAM size limit,
random wait states, single-port arbitration, memory dump, reset without
reload, and a PE-address register sink.

Design points:

- **Real reset edge.** `rst_n` starts high and falls at t=1. Ibex resets
  asynchronously behind a gated clock, so a reset held from t=0 is never
  seen in simulation. That bug started the hart in U-mode here.
- **Random initial state.** Built with `--x-initial unique`; runs pass
  `+verilator+rand+reset+2`. RAM is pre-filled with a non-zero pattern, so
  firmware cannot rely on zeroed memory.
- **Bus.** Grants and responses follow Ibex's protocol with optional random
  stalls and in-order responses. `+single_port` makes instruction fetch wait
  whenever the data port is busy, as with the chip's single-port SRAM.
- **Sim device** at `0x2000_0000`: `tohost` pass/fail, console, interrupt
  lines (fast 0–14, external, timer, software, NMI), cycle count.
