# AI usage log

The competition rules allow AI tools if the write-up explains how they were
used. This entry is designed and built with Claude (Anthropic). Every
AI-assisted step is logged here, newest first, so the write-up can describe the
flow accurately.

| Date | Area | What the AI did | Human decision |
|---|---|---|---|
| 2026-11-18 | Firmware base (F2) | Firmware chat, working only from `docs/pe_isa.md` and the Ibex docs. Wrote the Ibex startup/trap code, linker script, image format with CRC, runtime (status block, panic, safe state), interrupt layer and PE HAL. Also wrote a standalone-Ibex Verilator testbench (random stalls, single-port mode, randomised initial state), an Ibex environment for upstream riscv-tests, a differential run against upstream Ibex SV, fault-injection images, HAL host tests with a cross-check against the golden model, and CI. Found through testing: the testbench reset bug (hart started in U-mode), a gp-relaxation startup bug, the CRC checker sitting in the image tail where truncation removes it, and libgcc 64-bit divide size. Raised RAM-size and spec questions in `fw/QUESTIONS.md` | Pending owner review; questions go to the architecting chat |
| 2026-11-18 | Ibex conversion | Wrote the SV-to-Verilog conversion flow (yosys-slang, hierarchy kept), the formal equivalence proof (25,035 points, X-aware refinement for one `unique case` point), a mutation check showing the proof catches a one-operator change, an Icarus smoke test with a hand-encoded RV32IMC program, and CI | Pending owner review |
| 2026-11-18 | PE ISA + toolchain | Wrote the PE ISA spec, assembler, cycle-accurate golden model, example programs and 242 tests (independent protocol models, directed cycle-exact tests, fuzzing). Found and fixed through testing: hard-sync tick loss (now nearest-slot resync), open-drain reset glitch, I2C Fast-mode timing | Pending owner review; ISA refinements listed in `docs/pe_isa.md` section 8 |
| 2026-11-18 | Repo setup | Seeded repo from the TT IHP template, pinned Ibex and IHP PDK, added Ibex area-check scripts | Owner chose Ibex and full scope |
| 2026-11-18 | Feasibility | Synthesized Ibex configs on IHP CMOS5L cells to measure area | - |
| 2026-11-17 | Architecture | Drafted the architecture plan (Ibex control plane + 4 protocol engines) | Owner reviewed and set direction |
