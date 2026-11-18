# Firmware testbenches

Owner: firmware chat. Both treat the hardware as a black box behind
documented interfaces: the Ibex top-level ports for the standalone bench, and
the chip pins plus `docs/soc.md` for the full-chip bench.

```sh
test/fw/build_tb.sh conv        # Ibex alone: src/ibex_small.v             -> obj/Vtb
test/fw/build_tb.sh ref         # Ibex alone: upstream SV, same parameters -> obj/Vtb_ref
test/fw/build_tb.sh ref_micro   # Ibex alone: upstream SV, micro (RV32EC)  -> obj/Vtb_ref_micro
test/fw/build_tb.sh chip        # full chip + RP2350 loader model           -> obj/Vtb_chip
```

## Standalone Ibex (`tb_ibex_fw.sv`)

Firmware on Ibex alone, with its own memory map (RAM at 0, a simulation-only
device at `0x2000_0000`). The plusargs are documented in the file: RAM size
limit, random wait states, single-port arbitration, memory dump, reset
without reload, and a register sink at the PE address.

## Full chip (`tb_chip_fw.sv`)

The Tiny Tapeout top level, with the RTL source list taken from
`test/Makefile`, so new blocks are picked up automatically, plus the IHP SRAM
behavioural model. The loader model does what the RP2350 must:

1. Read the ID and check that the CPU is held.
2. Write the image over SPI, read it back and verify it.
3. Clear `SCRATCH`, set `CTRL.run`.
4. Poll `SCRATCH` while the CPU runs.

It then reads the firmware status block back over SPI and reports the pins.
Scenario plusargs: truncated load, restart without reload, reload and rerun,
slower SCK. GPIO is wired back as documented at the top of the file.

## Simulation rules learned the hard way

- **Real reset edges.** Ibex resets asynchronously behind a gated clock. A
  reset that is low from time 0 never produces a `negedge` in simulation, so
  the flops are never reset. In the standalone bench the hart started in
  U-mode.
- **`--x-initial-edge` for the chip.** The SoC's internal CPU reset (behind
  `CTRL.run`) can power up low. In Verilator's two-state simulation it then
  never falls, Ibex is never reset, and ~24% of random power-up states hung
  at boot (seeds 20–69). Event-driven simulators such as Icarus see X→0 as an
  edge, and silicon resets on the level, so this is a simulation artefact.
  Any Verilator full-chip bench needs this flag. With it, 60/60 seeds pass.
- **Random initial state.** All benches are built with `--x-initial unique`
  and run with `+verilator+rand+reset+2`. RAM starts non-zero.
