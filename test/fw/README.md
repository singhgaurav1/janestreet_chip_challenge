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

It then reads the firmware status block back over SPI and reports the pins,
plus up to two register ranges (`+dump2`/`+dump3` with word counts, e.g.
PE `ENGINE_CTRL` and `PAD_OWNER` after a panic); a bridge error during
those reads fails the run. Scenario plusargs: truncated load, restart
(refused, with a one-word poke), reload and rerun, slower SCK, bridge frame
tests while the CPU runs. GPIO is wired back as documented at the top of
the file.

## Protocol tests: full chip in Icarus with cocotb (`cocotb/`)

F4 tests: real firmware drives a protocol through the chip's pins and a
**third-party protocol model** checks the line (`cocotbext-uart`; SPI/I2C
next). `tb_proto.v` is a plain top with one named signal per pin;
`host.py` is the RP2350 side (SPI loader/bridge, result polling), written
from `docs/soc.md`. RTL sources come from `test/Makefile` as for the
Verilator benches. Run: `fw/ci_proto.sh` (or `make` here). Icarus runs
~3k cycles/s, so a test takes 1–3 minutes; Verilator 5.020 here is too old
for cocotb 2.

| Test | Result |
|---|---|
| `test_uart`: banner, then 16 bytes echoed back-to-back (incl. 0x00, 0xFF, 0x55, 0xAA) | 115200, 1 M, 3 Mbaud: 3/3 pass (a corrupted echo is caught) |

Notes:
- `cocotbext-uart` 0.1.4 `UartSink.read(n)` returns once one byte is
  queued and then fails if fewer than n are there; the test reads one byte
  at a time.
- `uo_out` pins are low from reset until the firmware runs, so a UART sink
  on `uo_out` sees a break during the image load; the test attaches it when
  the line first goes high (`fw/QUESTIONS.md` A18). The exit safe state
  drives the line low again, so "no extra bytes" is checked before the exit.
- cocotb 2 needs an even clock period: 16,666 ps (60.0024 MHz).

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
