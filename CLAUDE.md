# Protocol Emulator ASIC — Jane Street competition entry

Goal: win the Jane Street Protocol Emulator ASIC competition
(https://blog.janestreet.com/protocol-emulator-asic-competition/).
Submission deadline 2027-01-18; target submit 2027-01-16.
Architecture plan (source of decisions): https://claude.ai/artifact/VHZMwCqiHRDPna6ZycLJ8i

## Fixed decisions (do not change without the owner)
- Process: IHP 130nm SG13CMOS5L via Tiny Tapeout, 8x4 tiles (~1.0 mm²), 60 MHz target.
- CPU: **Ibex** (lowRISC). RV32EC "micro" or RV32IMC "small" with flip-flop register file.
  Ibex SystemVerilog is read with yosys-slang; for the TT flow, commit a converted
  plain-Verilog Ibex to `src/` and prove equivalence with a formal check.
- Architecture: Ibex control plane + 4 cycle-exact Protocol Engines (PIO-like 16-bit ISA)
  with generic assists (CRC/LFSR, NRZ/NRZI/Manchester line coder, bit stuffer,
  input conditioner + SYNC clock recovery, edge timestamper), pin crossbar,
  event flags, host SPI boot/debug bridge from the RP2350.
- Scope is fixed: UART, SPI (modes 0–3), I2C in controller and target roles, concurrent;
  JTAG, SWD, CAN; USB (full-speed stretch); 10BASE-T stretch.

## Rules for the design
- No feature is named after or specific to one protocol (generic, configurable only).
- Flexibility proof uses a holdout: freeze RTL/ISA in a tagged commit, then pick test
  protocols (e.g. random from sigrok decoders). Report all results, failures included.
- Log every AI-assisted step in `docs/ai_usage.md` (required by competition rules).
- Clean TT precheck is mandatory. Gate-level simulation strongly recommended.

## Layout
- `src/` TT design sources (top: `tt_um_singhgaurav1_protoemu`), `src/config.json` LibreLane config
- `test/` cocotb tests (TT template)
- `syn/ibex/` Ibex area check (`synth_ibex.sh micro|emc|small`)
- `scripts/fetch_deps.sh` pinned Ibex + IHP PDK into `deps/` (git-ignored)
- `docs/` datasheet (`info.md`) and `ai_usage.md`

## Tools
`pip install --break-system-packages yowasp-yosys` (includes the slang SV frontend;
use `read_slang --threads 1 --compat vcs`). GitHub release downloads are blocked from
the Claude workspace; git clones and PyPI work. Full GDS runs on GitHub Actions (`gds.yaml`).

## Status (2026-11-18)
- Repo seeded from ttihp-verilog-template; placeholder example design still in `src/project.v`.
- Ibex measured: micro 9,235 cells / 0.143 mm², small 15,355 cells / 0.232 mm² (pre-layout).
- Next: PE ISA spec + Python golden model; Ibex SV-to-Verilog conversion + equivalence;
  top-level skeleton with SRAM macro; first full GDS run; set CLOCK_PERIOD to 16.67 ns.
