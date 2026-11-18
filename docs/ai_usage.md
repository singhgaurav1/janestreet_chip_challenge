# AI usage log

The competition rules allow AI tools if the write-up explains how they were
used. This entry is designed and built with Claude (Anthropic). Every
AI-assisted step is logged here, newest first, so the write-up can describe the
flow accurately.

| Date | Area | What the AI did | Human decision |
|---|---|---|---|
| 2026-11-18 | PE ISA + toolchain | Wrote the PE ISA spec, assembler, cycle-accurate golden model, example programs and 242 tests (independent protocol models, directed cycle-exact tests, fuzzing). Found and fixed through testing: hard-sync tick loss (now nearest-slot resync), open-drain reset glitch, I2C Fast-mode timing | Pending owner review; ISA refinements listed in `docs/pe_isa.md` section 8 |
| 2026-11-18 | Repo setup | Seeded repo from the TT IHP template, pinned Ibex and IHP PDK, added Ibex area-check scripts | Owner chose Ibex and full scope |
| 2026-11-18 | Feasibility | Synthesized Ibex configs on IHP CMOS5L cells to measure area | - |
| 2026-11-17 | Architecture | Drafted the architecture plan (Ibex control plane + 4 protocol engines) | Owner reviewed and set direction |
