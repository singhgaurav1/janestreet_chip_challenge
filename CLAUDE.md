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
- `src/` TT design sources (top: `tt_um_singhgaurav1_protoemu` -> `pemu_soc`), `src/config.json`
  LibreLane config (60 MHz, SRAM macro). Architecture/address map/host protocol: `docs/soc.md`
- `src/pemu_cells.v` technology wrappers; all library-cell use goes through it (`PROTOEMU_ASIC`)
- `firmware/` RV32IMC C firmware (Zig clang; `python3 firmware/build.py`); images in `firmware/images/`
- `librelane_plugin_sram_pdn.py`, `odb_stripes.py`, `src/pdn_cfg.tcl`: SRAM power hookup (from
  TinyTapeout/ttihp-cmos5l-sram-example); `test/sram_model/` IHP SRAM behavioural model
- `test/` cocotb tests (TT template)
- `syn/ibex/` Ibex area check (`synth_ibex.sh micro|emc|small`)
- `scripts/fetch_deps.sh` pinned Ibex + IHP PDK into `deps/` (git-ignored)
- `docs/` datasheet (`info.md`), `pe_isa.md` (PE ISA spec) and `ai_usage.md`
- `tools/peemu/` PE assembler + golden model; `tools/tests/` its tests
- `examples/pe/` PE assembly examples

## Tools
Firmware: `pip install -r firmware/requirements.txt` (ziglang). Lint: `apt-get install verilator`.
`pip install --break-system-packages -r syn/requirements.txt` (pinned yowasp-yosys, includes
the slang SV frontend; use `read_slang --threads 1 --compat vcs`). The WASM Yosys can only
open files inside the repo tree; use `syn/ibex/out/` for scratch. Icarus: `apt-get install iverilog`. GitHub release downloads are blocked from
the Claude workspace; git clones and PyPI work. Full GDS runs on GitHub Actions (`gds.yaml`).

## Status (2026-11-18)
- Repo seeded from ttihp-verilog-template; placeholder example design still in `src/project.v`.
- Ibex measured: micro 9,235 cells / 0.143 mm², small 15,355 cells / 0.232 mm² (pre-layout).
- Done: PE ISA spec v0.1 (`docs/pe_isa.md`), `tools/peemu` assembler + cycle-accurate golden
  model, example programs (`examples/pe/`: UART, SPI modes 0–3, I2C controller/target,
  NRZI+stuffed+CRC bitstream TX/RX), 242 tests (`python -m pytest tools/tests`).
- Done: Ibex `small` (RV32IMC, FF regfile) converted to `src/ibex_small.v` (modules
  `ibex_small_top`, `ibex_small__*`), formally equivalent to the SV sources, runs an RV32IMC
  smoke program in Icarus (`test/ibex_smoke/run.sh`). Flow: `scripts/ibex_flow.py`; CI:
  `.github/workflows/ibex.yaml`. Never edit `src/ibex_small.v` by hand; regenerate.
- Done: SoC skeleton (Ibex + 2 KiB SRAM macro + host SPI bridge + sysctrl/GPIO), Ibex clock
  gate on the IHP ICG cell (re-proved), C firmware toolchain, cocotb tests through the pins
  (`cd test && make`; GL in `gds.yaml`). Synth: 16.4k cells (0.253 mm²) + 0.080 mm² macro.
- SRAM: CMOS5L has no SRAM in the released PDK; the TT flow's PDK aliases the SG13G2 macros
  (`pdk_dir::libs.ref/sg13cmos5l_sram`), and TT's ttihp-cmos5l-sram-example integrates them.
- Next: PE RTL (one engine first) in lockstep with the golden model, attached at 0x1000_1000;
  pin crossbar; then firmware drivers validating every protocol on Ibex before tapeout.

## PE toolchain rules
- `docs/pe_isa.md` and `tools/peemu/model.py` must agree; change both plus a test.
- RTL tests compare against `PEBlock.state()` every cycle; random stimulus from `peemu.randprog`.
- Protocol tests check against independent models in `tools/tests/vip.py`, never the model itself.
