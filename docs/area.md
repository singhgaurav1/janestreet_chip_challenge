# Area budget (2026-11-18)

Measured with Yosys on IHP SG13CMOS5L standard cells (typical corner), before
placement. Die: 8×4 tiles ≈ 1.0 mm²; CMOS5L routes on Metal1–4 plus TopMetal1,
and the first full build already reported routing congestion at ~34 %
utilization. Planning ceiling: **~0.60 mm² of cells + macros**.

| Block | Area | Source |
|---|---|---|
| Ibex `small` (RV32IMC) + SoC glue | 0.253 mm² | `syn/ibex/out/soc.stat` |
| SRAM 1024×32 macro (4 KiB; was 512×32, 0.080) | 0.140 mm² | LEF 416.64 × 336.46 µm |
| **Protocol Engine block, after rework** | **0.516 mm²** (was 0.631) | `pe_block` synthesis |
| Total | ~0.91 mm² | does not fit |

The plan estimated 2–3k cells per engine. The verified RTL (`src/pe_engine.v`,
lockstep-equal to the golden model) is about 11k cells per engine.

## Where the PE area goes

| Part | Area | Notes |
|---|---|---|
| Engine logic (each) | ~100k µm² | ~15k µm² flops; ~86k µm² combinational: variable shifters for IN/OUT in both directions, pin-window rotators (OUT/SET/MOV and side-set), operand muxes, CRC with variable width, divider resync arithmetic |
| Engine FIFOs (each) | ~21k µm² | TX + RX, 4 × 32 bits each |
| Block, shared | ~199k µm² | 64×16 instruction memory in flops (~50k) with 4 × 64:1 read ports (~45k), 4 × 7 config registers (~44k), pads, flags, register read mux |

## Rework done (no ISA change)

Lockstep-verified (300 seeds × 1000 cycles × 2 program styles, all 115 bins).

| Change | Effect |
|---|---|
| One 64-bit funnel shifter for IN/OUT, one pin-window unit for OUT/SET/MOV, one shared operand mux | engine 121k → 107k µm² |
| 2-deep TX/RX FIFOs (model default, spec and tests updated) | engine 107k → 96k µm² |
| Config registers store only defined bits (unused bits read 0) | −152 flops |
| Instruction memory: one clock gate per word, no enable muxes | −1,024 mux2 |

Block: 0.631 → 0.516 mm² (4 × 96k engines + 130k shared).

Remaining big items per engine, measured by tying config fields to constants:
configurable OUT/SET/side bases −20k µm², IN base −6k, CRC −5k, side-set −5k,
both shift directions −3k, coder/stuffer −3k, WAIT timeout −2k.

## Options (original list; 1–3 are now done)

No-regret (no ISA change; lockstep keeps them safe):

1. **Share one funnel shifter** for all IN/OUT shifting, one window rotator for OUT/SET/MOV, and generate pin masks with comparators instead of rotated masks. Estimated −35 to −45k µm² per engine.
2. **2-deep FIFOs** (the plan's own lever). About −40k µm² total. Changes the FIFO depth parameter only.
3. **Pack the config registers** to their used bits, and keep the instruction memory in latches. About −40k µm².

Estimated result: PE block ≈ 0.35–0.40 mm². That is still ~0.70 mm² in total with Ibex `small`, above the ceiling.

Decisions needed (they change fixed decisions or the ISA):

4. **Ibex `micro`** (RV32EC): −0.11 mm², and an easier 60 MHz slow-corner close.
5. **3 engines** instead of 4: −25 %.
6. **ISA trims:** one shared CRC unit, a single shared timestamper (logic-analyzer mode on one engine), and a 48-word instruction memory.

Recommended order: 1–3 now (measurable, verification-safe), then choose
among 4–6 with real numbers.
