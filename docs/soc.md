# SoC architecture

Ibex, 4 KiB SRAM, system control, the host SPI bridge and the Protocol
Engine block (`docs/pe_isa.md`) at `0x1000_1000`.

```
            ui_in[7:5] / uo_out[7]                 ui_in, uio
                    │                                  │
             ┌──────┴───────┐                   ┌──────┴──────┐
             │ host SPI     │ m0 (highest)      │  sysctrl    │ 0x1000_0000
             │ bridge       ├────┐              │  ID, GPIO,  │
             └──────┬───────┘    │  ┌────────┐  │  CYCLE,     │
                cpu_run          ├──┤  bus   ├──┤  SCRATCH    │
             ┌──────┴───────┐    │  │        │  └─────────────┘
             │ Ibex RV32IMC ├────┤  │        │  ┌─────────────┐
             │ (clock-gated)├────┘  │        ├──┤ SRAM 4 KiB  │ 0x0000_0000
             └──────────────┘ m1,m2 └────────┘  │ IHP macro   │
                                                └─────────────┘
```

## Blocks

| Module | Role |
|---|---|
| `tt_um_singhgaurav1_protoemu` (`src/project.v`) | Pin assignment, reset synchronizer |
| `pemu_soc` | Wires the blocks together |
| `pemu_host_spi` | Host SPI target: boot loader and debug (peek/poke while running) |
| `pemu_bus` | Interconnect, 3 masters, fixed one-cycle responses |
| `pemu_sram` | `RM_IHPSG13_1P_1024x32_c2_bm_bist` (416.64 × 336.46 µm), enabled only on access |
| `pemu_sysctrl` | ID, GPIO, cycle counter, mailbox |
| `ibex_small_top` (`src/ibex_small.v`) | Generated from Ibex SystemVerilog, formally equivalent |
| `pe_block` (`pe_engine`, `pe_fifo`) | 4 Protocol Engines, shared instruction memory, event flags, pad owners (pin crossbar), interrupts |
| `pemu_cells` | Technology wrappers: IHP integrated clock gate, synchronizers |

## Clocks, resets, power

- **One clock domain** (60 MHz). The host SPI pins are oversampled, not clocked.
- **Reset:** `rst_n` is synchronized (asynchronous assert, synchronous release).
  The CPU has its own reset, held until the host sets `CTRL.run`.
- **Power:**
  - **Ibex's sleep clock gate** (`wfi`) uses the IHP integrated clock-gate
    cell `sg13cmos5l_slgcp_1`.
  - **The SRAM** is enabled only on the cycles it's accessed.
  - **No separate power domains:** Tiny Tapeout tiles have a single supply,
    so clock gating and access gating are the available power controls.

## Bus

Ibex-style `req`/`gnt`/`rvalid`. Each target answers exactly one cycle after
the grant, so every master can issue a request each cycle and responses
never need reordering. Priority on each target: host bridge, then CPU data,
then CPU instruction fetch.

| Address | Target | Notes |
|---|---|---|
| `0x0000_0000` – `0x0000_0FFF` | SRAM | 4 KiB. Vectors at `0x00`, reset at `0x80` |
| `0x1000_0000` – `0x1000_0FFF` | sysctrl | registers below; other offsets return `err` |
| `0x1000_1000` – `0x1000_1FFF` | Protocol Engines | `docs/pe_isa.md` section 6; unmapped offsets return `err`. Word accesses only |
| `0x1000_2000` – `0x1000_FFFF` | none | `err` |
| anything else | none | `err` (nothing aliases) |

Instruction fetch only from SRAM.

## Pads and interrupts

Pads are numbered as in `docs/pe_isa.md` 2.2: 0–7 `ui_in`, 8–15 `uio`,
16–23 `uo_out`. Pads 8–23 each have an owner register in the PE block
(`PAD_OWNER[p]`):

| Owner | Who drives the pad |
|---|---|
| 0–3 | that engine |
| 4 | the PE block's CPU GPIO (`GPIO_OUT`/`GPIO_OE` and their SET/CLR aliases) |
| 5 system (reset value) | the SoC: pad 23 = host bridge MISO; pads 16–22 = sysctrl `GPIO_OUT[6:0]`; pads 8–15 = sysctrl `UIO_OUT`/`UIO_OE` |
| 6–7 none | nobody: `uo_out` bit 0, `uio` OE off |

So after reset the pins behave as before the engines were added: MISO on
`uo_out[7]` and the sysctrl GPIO on the rest. Firmware hands pads to engines
(or to the PE GPIO) by writing their owners. `SAFE_STATE` releases every
pad except system-owned ones, so the host bridge keeps working. Keep pad 23
with the system; giving it away disconnects MISO. All inputs (`ui_in`,
`uio_in`) reach every engine, including the host SPI pins.

**Interrupts.** The PE block's 12 level-sensitive lines are Ibex fast
interrupts: event flag n → `irq_fast` ID 16 + n; engine n FIFO (RX not
empty / TX not full, `IRQ_EN`) → ID 24 + n. IDs 28–30, the external, timer
and software interrupts and the NMI are tied off.

**Bus errors.** An access that maps to no register is still granted and
answered one cycle later, with read data 0 and `err`. On the CPU this is an
access fault (`mcause` 1 fetch, 5 load, 7 store), so a wild pointer past
the end of RAM faults instead of wrapping onto real memory. The host bridge
ignores `err`: its reads return 0. Writes to read-only registers are
ignored without an error.

### sysctrl registers (`0x1000_0000`)

| Offset | Name | Access | Description |
|---|---|---|---|
| `0x00` | ID | RO | `0x50454D55` ("PEMU") |
| `0x04` | GPIO_OUT | RW | `[6:0]` → `uo_out[6:0]` |
| `0x08` | GPIO_IN | RO | `{uio_in, ui_in}`, synchronized |
| `0x0C` | UIO_OUT | RW | `[7:0]` |
| `0x10` | UIO_OE | RW | `[7:0]` |
| `0x14` | CYCLE | RO | free-running cycle counter |
| `0x18` | SCRATCH | RW | mailbox to the host (firmware results) |

## Host SPI protocol

Mode 0, MSB first, SCK ≤ f_clk / 6 (10 MHz at 60 MHz). One command per
CS-low frame.

| Command | Then | Result |
|---|---|---|
| `0x01` WR_CTRL | `ctrl` | `ctrl[0]` = CPU run (see boot guard) |
| `0x02` WRITE | `addr32`, `data32`... | word writes, address auto-increments |
| `0x03` READ | `addr32`, 1 dummy byte | `data32`... (auto-increment, next word prefetched) |
| `0x05` STATUS | — | `{3'b0, run_refused, underrun, overrun, cpu_sleep, cpu_run}` |
| `0x9F` ID | — | `0x50454D55` |

The bridge has bus priority, so it works while the CPU runs. `overrun` and
`underrun` are sticky errors set if the host clocks faster than the bus can
follow (cannot happen at the specified SCK).

**Boot guard.** Starting the CPU (`run` 0 → 1) needs at least one WRITE to
SRAM since the CPU last started (or since reset). Otherwise the bridge
refuses: the CPU stays in reset and `run_refused` is set. `run_refused`
reflects the last WR_CTRL. Stopping (`run` = 0) is always allowed, and
`run` = 1 while running changes nothing. So every start runs a freshly
loaded image. The firmware also checks its image header and CRC, which
catches a partial load.

**Frame rules.**
- The first byte selects the command. An unknown command (e.g. `0x00`,
  `0xFF`) is ignored until CS goes high; the next frame works normally.
- Addresses are word addresses: `addr[1:0]` are ignored.
- A WRITE data word is written only once all 32 bits have arrived; a word cut
  short by CS high is discarded. Earlier complete words stay written.
- CS high ends any frame; a READ may stop after any byte.

## Firmware

Two trees share one build infrastructure (toolchain, startup, runtime,
linker script, image format) from `fw/`:

- `fw/`: the product firmware (runtime, PE HAL, protocol drivers), written
  from the specs only. See `fw/README.md`.
- `system_validation/`: the RTL team's hardware validation programs, built
  with `fw/`'s infrastructure. See `system_validation/README.md`.

- **Image:** loaded at `0x0`; vectors at `0x00`, reset jump at `0x80`,
  header (magic, length, CRC-32) at `0x84`. Startup refuses a corrupted
  image or a restart without a reload.
- **Result convention** (one for every image): the runtime writes SCRATCH
  `1` = pass, `(n << 1) | 1` = failed with code `n` (panics: `n = 1000 +
  reason`). The status block (`fw_status`, `fw/include/fw.h`) occupies the
  top 64 bytes of RAM and is host-readable at any time.

## Verification

| Test | What |
|---|---|
| `test/test.py::test_host_bridge` | ID, status, SRAM and register write/read through the real SPI pins |
| `test/test.py::test_sysval_smoke` | Load `system_validation/tests/smoke` over SPI, run it: CPU, memory sections, M extension, byte stores, peripherals, 4 KiB status block, pin handshake both ways, safe state and sleep after exit |
| `test/test.py::test_boot_guard` | Start refused without an SRAM load (peripheral writes don't count), accepted after one, refused again after a stop |
| `test/test.py::test_sysval_pe_uart` | `system_validation/tests/pe_uart` on the CPU: loads the UART programs into the engines, echoes bytes from `ui_in[0]` to `uo_out[0]` (checked by the bench), RX interrupt pending in `mip`, register readback, one-write start and safe state |
| `test/test.py::test_sysval_buserr` | `system_validation/tests/buserr` on the CPU: loads, stores and fetches fault above RAM, at unused sysctrl offsets, in empty windows and outside both regions; mapped addresses and read-only writes do not |
| `test/ibex_smoke` | Hand-encoded program on the converted Ibex alone |
| `scripts/ibex_flow.py equiv` | Formal equivalence of the converted Ibex |

The cocotb tests run on RTL (`test.yaml`) and on the gate-level netlist
after place and route (`gds.yaml`, `gl_test`).
