# Firmware (Ibex)

Owner: firmware chat. Workstream F2 to F7 in the build brief. This directory
is written **only from the specs**: `docs/pe_isa.md` for the Protocol Engines
and the Ibex documentation for the CPU. It never reads `src/` internals, so
it is an independent check on the hardware.

Status:
- **F2 firmware base: done.**
- **F1/F3 firmware side: running on the full chip** (`docs/soc.md`, 4 KiB
  SRAM). Images are loaded through the real host SPI pins by an RP2350 loader
  model: riscv-tests 63 pass, 2 expected fail, 2 too big; 16 runtime, boot and
  bridge tests pass across power-up seeds.
- **Shared infrastructure:** `system_validation/` (the RTL chat's hardware
  tests) builds by including `fw/Makefile`. Keep the variable names it uses
  (`RT_SRC`, `CFLAGS`, `LDFLAGS`, `LIBS`, `CROSS`, `NM`, `OBJCOPY`, `PYTHON`,
  `TARGET`, `ARCH`, `RAM_SIZE`) stable. `fw/ci.sh` checks that it still
  builds and reproduces its committed image.
- **Not yet:** the PE block is not attached in the SoC (shared-block
  offsets are still stubs), so the HAL has only run against a register sink.

## Quick start

```sh
sudo apt-get install gcc-riscv64-unknown-elf verilator    # GCC 13.2, Verilator 5.020
pip install -r tools/requirements.txt
./scripts/fetch_deps.sh && ./fw/scripts/fetch_riscv_tests.sh
./fw/ci.sh                                                 # everything CI runs, ~1 min
```

Single pieces: `make -C fw [TARGET=chip|tb] [ARCH=rv32imc|rv32ec]`,
`make -C fw host-test`, `fw/riscv-tests/run.py --tb both|chip`,
`fw/tests/run_fw_tests.py --tb conv|ref|ref_micro|chip [--seed N]`.

Two build targets:

| `TARGET` | For | RAM | Result reported via |
|---|---|---|---|
| `chip` (default) | the SoC of `docs/soc.md` | 4 KiB | sysctrl `SCRATCH`, polled by the host |
| `tb` | the standalone-Ibex testbench | 4 KiB | the testbench's `tohost` device |

## Layout

| Path | What |
|---|---|
| `include/platform.h` | Memory map and PE interrupt wiring from `docs/soc.md` |
| `include/sysctrl.h` | sysctrl driver (ID, GPIO, cycle counter, SCRATCH) and its pin safe state |
| `include/pe_regs.h` | PE registers from `pe_isa.md` section 6 (register map v1) |
| `include/pe_hal.h`, `src/pe_hal.c` | PE HAL: config images, program load, start/stop, FIFOs, event flags, pads, safe state |
| `include/fw.h`, `src/runtime.c` | Boot checks, status block, panic, exit, CRC-32 |
| `include/irq.h`, `src/irq.c` | Interrupt registration and dispatch |
| `include/csr.h` | CSR access, 32/64-bit cycle counter, timeouts |
| `src/start.S` | Vector table, reset, image header, trap entry |
| `link/fw.ld` | Single-RAM layout with size checks |
| `scripts/mkimage.py` | ELF to loadable `.bin`/`.hex`; fills length and CRC; size report |
| `scripts/stack_check.py` | Static worst-case stack (call graph + `-fstack-usage` + interrupt frame); fails the build if it does not fit |
| `apps/selftest` | Runtime self-test (startup, counters, M extension, CRC, interrupts) |
| `apps/crashtest` | One image per failure mode (fault injection) |
| `apps/halpanic` | HAL on the real core, then a panic; checks the bus write sequence (tb) |
| `apps/chiptest` | Chip self-test: sysctrl ID, cycle counter vs `mcycle`, GPIO through the pins, pins released at exit |
| `apps/bridgetest` | Host bridge while the CPU runs: peek/poke, junk frames, CPU loads under bridge traffic |
| `apps/pehal` | PE HAL on the chip's real PE block, three images (`-fifo`, `-engines`, `-pads`); engine programs in `progs.pe`, assembled at build time |
| `drivers/` | Protocol drivers on the engines (`pe_uart`); `drivers.mk` lists each driver's sources and engine programs (the spec's `examples/pe/*.pe`, assembled at build time) |
| `scripts/pecfg.py` | Engine configurations from `apps/<app>/*.pecfg`, built and **checked at build time by the HAL's own C code** on the host; emits `const pe_cfg_t` for `pe_configure_prechecked()` |
| `apps/uart` | F4 UART echo, one image per baud rate (115200, 1M, 3M) |
| `apps/pebench` | Cycle costs of bus accesses and HAL calls on the chip (read by hand) |
| `ci_proto.sh` | F4 protocol tests (Icarus + cocotb + third-party protocol models; `test/fw/cocotb/`) |
| `tests/host/` | HAL unit tests with a recording MMIO mock |
| `tests/test_hal_crosscheck.py` | C HAL packing vs the golden model's packing |
| `tests/run_fw_tests.py` | Runs the apps on the testbench; checks exit codes and the status block |
| `riscv-tests/` | Ibex environment and runner for upstream riscv-tests |
| `../test/fw/` | Standalone-Ibex testbench and full-chip testbench with the RP2350 loader model (Verilator) |

## Toolchain

Ubuntu's `gcc-riscv64-unknown-elf` 13.2 targets RV32 with `-march`/`-mabi`
and ships rv32 libgcc multilibs.

- **Ibex small:** `-march=rv32imc_zicsr -mabi=ilp32` (default).
- **Ibex micro (fallback):** `ARCH=rv32ec`, `-march=rv32ec_zicsr -mabi=ilp32e`.
  Built and tested in CI so the fallback stays usable.
- Freestanding, `-Os`, `-Werror` with `-Wconversion`, `-fstack-usage`.
  `libc.c` supplies `memset`/`memcpy`/`memmove`/`memcmp`.
- Ibex implements Zcb and Zcmp too; GCC 13 does not emit them. Plain C code
  is valid on any Zc setting, so a smaller Zc configuration costs firmware
  nothing.

## Boot and image format

Ibex fetches its first instruction at `boot_addr + 0x80`, and `mtvec` is
always vectored with a 256-byte aligned base. The image starts at the boot
address:

| Offset | Content |
|---|---|
| `0x00` | Exception entry (all exceptions) |
| `0x04`–`0x78` | Interrupt ID n at `4n` |
| `0x7C` | NMI |
| `0x80` | Reset: `j _start` |
| `0x84` | Header: magic `PEFW`, length, CRC-32, build id |
| `0x94` | Startup, then the boot-check path (ends below 1 KiB, linker-checked) |

The host loads the `.bin` in place (no ROM, no `.data` copy). Startup:

1. Set `gp`, `sp` and `mtvec`. `gp` comes first with relaxation off, because
   the linker may rewrite later address loads as `gp`-relative. A test caught
   this bug.
2. **Boot checks** (before `.bss` is touched): refuse a second start from
   one load (`DIRTY_RESTART`), then verify the CRC-32 over the whole image
   (`IMAGE_CRC`). On failure, record the reason and halt without touching
   any hardware.
3. Zero `.bss`, paint the stack, set the guard word, run `main`.
4. `main` returning calls `fw_exit(code)`.

## Safety

The chip drives real buses, so a firmware fault must leave the pins safe and
must tell the host why it stopped.

- **Result mailbox:** `fw_exit`/`fw_panic` write a riscv-tests-style code
  to sysctrl `SCRATCH` (1 = pass, `(n << 1) | 1` = fail with n; panics
  use n = 1000 + reason). The host polls it over SPI.
- **Status block** at RAM top − 64 bytes (`fw_status_t`): state, reason,
  `mcause`/`mepc`/`mtval`, `ra`, `sp`, cycle count, spurious-IRQ count,
  stack high-water mark, heartbeat. The host reads it over the SPI debug
  bridge at any time. Cleared at boot, so stale RAM cannot pose as a record.
- **Panic path** (`fw_panic`, any exception, NMI, stack overflow at
  interrupt entry, failed `FW_ASSERT`): interrupts off, record written, state
  set to `PANIC` last, then `fw_safe_state()`, then halt. Exception entry
  switches to a separate 128-byte panic stack, so a bad `sp` cannot cause a
  second fault. A fault during the panic path halts at once.
- **Safe state** (`pe_safe_state`): engines off, PE interrupts off, every
  pad the firmware assigned is taken back by the CPU and released. The order
  is OE off while still open-drain, then open-drain off, so an I2C pad never
  drives high. Pads the firmware never assigned are left alone, since the
  host bridge may own them. A golden test checks the exact write order on
  the real core. On the chip the sysctrl pins are released too (`uio` OE
  off first, then outputs low); a chip test panics while driving every pin
  and checks the pins afterwards.
- **No blind hardware access:** the HAL never writes a full TX FIFO and
  never reads an empty RX FIFO (behaviour unspecified), never reprograms
  memory an enabled engine is using, never configures a running engine, and
  validates every field and cross-field rule before writing. Every blocking
  call has a cycle timeout.
- **Interrupts:** level-sensitive on Ibex. An interrupt with no handler is
  disabled and counted, not left to storm. No nesting.
- **Known limit:** there is no PMP (area), so stack overflow is only caught
  at interrupt entry and at `fw_stack_free()`. Mitigations: a static
  worst-case stack check on every build (see "Code size"), plus the measured
  high-water mark. Note that the SRAM aliases above `0x7FF`
  (`docs/soc.md`), so on the chip a wild pointer past the end of RAM
  silently wraps instead of faulting (`fw/QUESTIONS.md` A13).

## Code size (RAM is the binding constraint)

Measured, rv32imc, `-Os`:

| Piece | Bytes |
|---|---|
| Runtime (vectors, boot checks, panic, IRQ, libc), linked | ~820–1,190 image + 128 bss |
| PE HAL, functions a test links (`--gc-sections`) | ~1,400–1,600 |
| ... of which run-time config packing and checking (`pe_cfg_set/get/check`) | ~830, **avoidable**: see `pecfg` below |
| UART driver | ~250 |
| Status block + panic stack | 192 |
| Stack | computed per image, typically 80–370 B incl. one interrupt frame |

- **Chip (4 KiB):** runtime plus the HAL a test links is ~2.3–2.4 KB,
  leaving ~1.2 KB (after bss, status block and stack) for drivers, tests
  and PE program arrays. The HAL test on the chip (`apps/pehal`) did not fit
  as one image (4.9 KB) and is split in three. Drivers will need to be lean,
  and images are per scenario (the chip is reloaded for each use).
- **Build-time configurations (`pecfg`).** The first UART echo was 4,050 B
  of code and did not fit. Engine configurations are fixed per image, so
  `scripts/pecfg.py` builds them at build time with the HAL's own
  `pe_cfg_set` / `pe_cfg_set_tick_hz` / `pe_cfg_check` compiled for the
  host: a configuration the HAL would refuse fails the build with the same
  rules. The image carries only the 28-byte register image (covered by the
  image CRC) and calls `pe_configure_prechecked()`. UART echo: 2,692 B. The
  run-time path stays for configurations computed on the chip.
- At 2 KiB (before PR #3) the HAL did not fit at all.
- The stack is checked statically. `scripts/stack_check.py` adds up the
  deepest call path from `_start`, then one interrupt frame plus the deepest
  possible handler (interrupts don't nest on Ibex), plus a 32 B margin, and
  checks the panic path against its own 128 B stack. It fails the build on
  recursion, dynamic frames, or a missing frame size. Compiler tail calls
  (`j` into another function) do not add the caller's frame; hand-written
  assembly gets no such credit. This replaced a fixed
  512 B minimum. The measured high-water mark in the status block stays
  below the computed bound (self-test: 132 B used, 368 B bound).
- `mkimage.py` rejects libgcc's 64-bit divide (about 2.4 KB).
  `-msave-restore` makes these images larger and is not used.
- GCC 13 treats constant addresses below 4 KiB as null derefs
  (`-Warray-bounds`); `mmio.h` hides the address from that check at no
  instruction cost.

## Tests

| Test | What it shows | Result |
|---|---|---|
| riscv-tests rv32ui/um/uc/mi on the converted core | ISA and trap behaviour of `src/ibex_small.v` | 65 pass, 2 expected fails (need PMP / debug triggers) |
| Same on upstream Ibex SV (`--tb both`) | Conversion check with an independent frontend | identical results **and cycle counts**, 67/67 |
| Same with 30% random stalls and single-port RAM | Bus robustness (the chip's SRAM is single-port) | 67/67 as expected |
| `run_fw_tests.py` (16 cases) on conv, ref, ref_micro (rv32ec) | Startup, interrupts, all panic paths, corrupted, truncated and dirty images, HAL write sequence | 16/16 each |
| riscv-tests on the **full chip**, each loaded over the SPI pins | Boot path + ISA on the real SoC | 63 pass, 2 expected fail, 2 skipped (over 4 KiB) |
| `run_fw_tests.py --tb chip` (21 cases), 3 power-up seeds | Chip self-test and GPIO through the pins, reload after re-hold, slow SCK, bus errors from the interconnect, boot guard (restart refused without a load, during boot and while running), restart with a stale image, corrupted and truncated loads, pins released after a panic, bridge frame rules while the CPU loads the bus, access past the end of RAM (load fault), and the **PE HAL on the real PE block**: reset values, forced instructions and FORCE_PENDING, FIFO levels and sticky errors, two engines started in one write, program memory busy while running, CPU-owned and open-drain pads through the pins, pad 23 protected, and the safe state (engines and pad owners read back over the bridge) | 21/21 each |
| `test_pe_hal` | HAL rules on a register-file mock, every field position written out by hand from the spec | 616 checks |
| `test_hal_crosscheck.py` | 3,000 random configurations and 308 divider rates vs the golden model | identical |
| **F4** `ci_proto.sh`: UART echo on the full chip (Icarus), firmware loaded over the SPI pins, line checked by **cocotbext-uart** | Driver + spec example programs + PE block + pins, at 115200, 1 M and 3 Mbaud back-to-back | 3/3 |

The testbench randomises all uninitialised state and starts from a real
reset edge (see `test/fw/README.md` for why that matters).

## Measured numbers on the chip (`apps/pebench`, cycles at 60 MHz)

| Operation | Cycles |
|---|---|
| Instruction (fetch from the single-port SRAM) | ~2 |
| Load from SRAM, PE or sysctrl register; store to a PE register | ~4 (on top of the instruction) |
| `pe_status()`, `pe_rx_try()` on an empty FIFO (inline) | 8, 11 (were 19 and 49 as calls) |

At 3 Mbaud a byte arrives every 200 cycles and the RX FIFO is 2 deep. With
the FIFO calls out of line, the echo loop took ~230 cycles per byte, the
engine stalled on a full RX FIFO and lost frames (the independent UART model
caught it). With the inline calls 3 Mbaud passes. Data paths must stay lean.

## Measured numbers (standalone core, zero wait states)

- Interrupt entry, from raising the line to the first handler line: 90
  cycles (1.5 µs at 60 MHz). Per-byte interrupts cannot meet the F5 budget
  of one byte per ~40 cycles; full-speed data paths must use polling or FIFO
  batching.
- Boot CRC check: ~35 cycles/byte (about 2.4 ms for a full 4 KiB image).
- Chip boot over SPI at 10 MHz: loading plus read-back verify of a 1.5 KiB
  image takes ~180k cycles (3 ms); the firmware's own checks then take ~45k.

## What changes when F1 lands

Replace `pe_regs.h` with the generated `regs.h` (naming convention proposed
in the header). Set the PE shared-block offsets and interrupt wiring in
`platform.h`. Rebuild. The golden write-sequence test in
`tests/run_fw_tests.py` then needs its addresses updated. Running the HAL on
the chip also needs the RAM decision (A1).
