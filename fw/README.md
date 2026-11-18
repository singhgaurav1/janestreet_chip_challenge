# Firmware (Ibex)

Owner: firmware chat. Workstream F2 to F7 in the build brief. This directory
is written **only from the specs**: `docs/pe_isa.md` for the Protocol Engines
and the Ibex documentation for the CPU. It never reads `src/` internals, so
it is an independent check on the hardware.

Status: **F2 firmware base done** on the standalone core. The chip memory map
does not exist yet (F1), so every chip address is a marked stub in
`include/platform.h` and `include/pe_regs.h`.

## Quick start

```sh
sudo apt-get install gcc-riscv64-unknown-elf verilator    # GCC 13.2, Verilator 5.020
pip install -r tools/requirements.txt
./scripts/fetch_deps.sh && ./fw/scripts/fetch_riscv_tests.sh
./fw/ci.sh                                                 # everything CI runs, ~1 min
```

Single pieces: `make -C fw [ARCH=rv32imc|rv32ec] [RAM_SIZE=4096]`,
`make -C fw host-test`, `fw/riscv-tests/run.py --tb both`,
`fw/tests/run_fw_tests.py --tb conv|ref|ref_micro`.

## Layout

| Path | What |
|---|---|
| `include/platform.h` | Memory map and IRQ wiring. **STUB** until F1 |
| `include/pe_regs.h` | PE registers from `pe_isa.md` section 6. Shared-block offsets are **STUB** |
| `include/pe_hal.h`, `src/pe_hal.c` | PE HAL: config images, program load, start/stop, FIFOs, event flags, pads, safe state |
| `include/fw.h`, `src/runtime.c` | Boot checks, status block, panic, exit, CRC-32 |
| `include/irq.h`, `src/irq.c` | Interrupt registration and dispatch |
| `include/csr.h` | CSR access, 32/64-bit cycle counter, timeouts |
| `src/start.S` | Vector table, reset, image header, trap entry |
| `link/fw.ld` | Single-RAM layout with size checks |
| `scripts/mkimage.py` | ELF to loadable `.bin`/`.hex`; fills length and CRC; size report |
| `apps/selftest` | Runtime self-test (startup, counters, M extension, CRC, interrupts) |
| `apps/crashtest` | One image per failure mode (fault injection) |
| `apps/halpanic` | HAL on the real core, then a panic; checks the bus write sequence |
| `tests/host/` | HAL unit tests with a recording MMIO mock |
| `tests/test_hal_crosscheck.py` | C HAL packing vs the golden model's packing |
| `tests/run_fw_tests.py` | Runs the apps on the testbench; checks exit codes and the status block |
| `riscv-tests/` | Ibex environment and runner for upstream riscv-tests |
| `../test/fw/` | Standalone-Ibex testbench (Verilator) |

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
  the real core.
- **No blind hardware access:** the HAL never writes a full TX FIFO and
  never reads an empty RX FIFO (behaviour unspecified), never reprograms
  memory an enabled engine is using, never configures a running engine, and
  validates every field and cross-field rule before writing. Every blocking
  call has a cycle timeout.
- **Interrupts:** level-sensitive on Ibex. An interrupt with no handler is
  disabled and counted, not left to storm. No nesting.
- **Known limit:** there is no PMP (area), so stack overflow is only caught
  at interrupt entry and at `fw_stack_free()`. Mitigations: measured
  high-water mark and a 512-byte minimum stack enforced by the linker.

## Code size (RAM is the binding constraint)

With 4 KiB RAM (1024×32 macro), rv32imc:

| Piece | Bytes |
|---|---|
| Runtime (vectors, boot checks, panic, IRQ, libc) | ~1,160 image + 128 bss |
| PE HAL | ~1,750 |
| Status block + panic stack | 192 |
| Minimum stack | 512 |
| **Left for protocol drivers and PE programs** | **~350** |

- **2 KiB RAM (512×32 macro) cannot hold the runtime plus a minimum stack.**
  This is raised with the architect (`fw/QUESTIONS.md`).
- `mkimage.py` rejects libgcc's 64-bit divide (`__udivdi3` and friends, about
  2.4 KB).
- `-msave-restore` makes these images larger and is not used.

## Tests

| Test | What it shows | Result |
|---|---|---|
| riscv-tests rv32ui/um/uc/mi on the converted core | ISA and trap behaviour of `src/ibex_small.v` | 65 pass, 2 expected fails (need PMP / debug triggers) |
| Same on upstream Ibex SV (`--tb both`) | Conversion check with an independent frontend | identical results **and cycle counts**, 67/67 |
| Same with 30% random stalls and single-port RAM | Bus robustness (the chip's SRAM is single-port) | 67/67 as expected |
| `run_fw_tests.py` (16 cases) on conv, ref, ref_micro (rv32ec) | Startup, interrupts, all panic paths, corrupted, truncated and dirty images, HAL write sequence | 16/16 each |
| `test_pe_hal` | HAL rules, every field position written out by hand from the spec | 585 checks |
| `test_hal_crosscheck.py` | 3,000 random configurations and 308 divider rates vs the golden model | identical |

The testbench randomises all uninitialised state and starts from a real
reset edge (see `test/fw/README.md` for why that matters).

## Measured numbers (standalone core, zero wait states)

- Interrupt entry, from raising the line to the first handler line: 90
  cycles (1.5 µs at 60 MHz). Per-byte interrupts cannot meet the F5 budget
  of one byte per ~40 cycles; full-speed data paths must use polling or FIFO
  batching.
- Boot CRC check: ~35 cycles/byte (about 2.4 ms for a full 4 KiB image).

## What changes when F1 lands

Replace `pe_regs.h` with the generated `regs.h` (naming convention proposed
in the header). Set the addresses and the PE interrupt wiring in
`platform.h`. Rebuild. The golden write-sequence test in
`tests/run_fw_tests.py` then needs its addresses updated.
