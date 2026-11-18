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
