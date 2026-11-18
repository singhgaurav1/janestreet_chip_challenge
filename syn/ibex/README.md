# Ibex area check

Pre-layout synthesis of `ibex_top` (flip-flop register file) on IHP SG13CMOS5L
standard cells, typical corner (1.2 V, 25 °C). Reproduce with:

```sh
pip install --break-system-packages yowasp-yosys
./scripts/fetch_deps.sh
./syn/ibex/synth_ibex.sh micro   # or emc, small
```

| Config | ISA | Cells | Area (µm²) | Share of 8x4 tiles (~1.0 mm²) |
|---|---|---|---|---|
| micro | RV32EC | 9,235 | 143,028 | ~14% |
| emc | RV32EMC | 12,706 | 184,972 | ~18% |
| small | RV32IMC, fast multiplier | 15,355 | 232,007 | ~23% |

SRAM macros (IHP CMOS5L library, single port): 256x32 = 49,488 µm²,
512x32 = 79,720 µm², 1024x32 = 140,183 µm².

Pinned sources: Ibex `3d524f1` (2026-11-12), IHP-Open-PDK `e73adb4` (2026-11-10).
Measured 2026-11-18. Timing and placement not yet checked.

## Plain-Verilog conversion (`src/ibex_small.v`)

The Tiny Tapeout flow and Icarus read plain Verilog, so the core is committed
in converted form, regenerated and re-proved in CI (`.github/workflows/ibex.yaml`):

```sh
pip install -r syn/requirements.txt          # pinned yowasp-yosys (output depends on it)
./scripts/fetch_deps.sh
python3 scripts/ibex_flow.py convert small   # -> src/ibex_small.v (deterministic)
python3 scripts/ibex_flow.py equiv small     # formal equivalence, ~10 min
test/ibex_smoke/run.sh                       # runs an RV32IMC program in Icarus
```

- **Conversion:** yosys-slang with `--keep-hierarchy`, then `proc`, then
  `write_verilog`. Modules are renamed `ibex_small_top` and `ibex_small__<module>`
  (`__<instance>` is appended where Ibex instantiates a module more than once),
  so the names can't collide with our RTL.
- **Equivalence:** gold is slang's default flattened elaboration of the
  original SystemVerilog. Gate is the committed file, read back by Yosys's own
  Verilog frontend and flattened. `equiv_make` pairs 25,035 points. A
  two-valued pass proves all but one; the last one is proved with X modelled
  as don't-care (`-undef`), so the conversion must match wherever the
  SystemVerilog defines a value.
  - The remaining point is `id_stage_i.id_fsm_d`, which comes from a
    `unique case` over one-hot decode signals.
  - **The proof catches errors:** changing one `&` to `|` in the ALU of a copy
    makes the proof fail (`IBEX_EQUIV_SRC=<file>` checks another file).
- **Simulation:** `test/ibex_smoke` hand-encodes an RV32IMC program (ALU, loads
  and stores including a byte store, branch loop, mul/div, a compressed
  instruction, LUI) and checks memory after it runs, with no toolchain needed.
  It finishes in 119 cycles.
- **Synthesis of the converted file:** 15,522 cells / 229,781 µm² on IHP CMOS5L
  through the plain-Verilog reader, within 1% of the SystemVerilog-direct
  figure above.
- **Clock gate:** `prim_clock_gating` converts to a generic latch plus AND. For
  the chip it must be replaced by the IHP integrated clock-gate cell (top-level
  integration task).
- **Config:** `small` = RV32IMC (`RV32MFast`), flip-flop register file. The
  `micro` config (RV32EC) also converts with `convert micro`. Every other
  parameter is at its upstream default; note that Ibex now defaults
  `RV32ZC` to Zca+Zcb+Zcmp.
