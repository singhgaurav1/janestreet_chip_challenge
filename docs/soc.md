# SoC architecture (skeleton)

Status: first integration. The Protocol Engine block (docs/pe_isa.md) is the
next addition, at `0x1000_1000`.

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
| `0x0000_0000` – `0x0FFF_FFFF` | SRAM | 4 KiB, aliases above `0xFFF`. Vectors at `0x00`, reset at `0x80` |
| `0x1000_0000` | sysctrl | 4 KiB window |
| `0x1000_1000` | Protocol Engines | next step |
| other | none | granted, returns `err` |

Instruction fetch only from SRAM.

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
| `0x01` WR_CTRL | `ctrl` | `ctrl[0]` = CPU run |
| `0x02` WRITE | `addr32`, `data32`... | word writes, address auto-increments |
| `0x03` READ | `addr32`, 1 dummy byte | `data32`... (auto-increment, next word prefetched) |
| `0x05` STATUS | — | `{4'b0, underrun, overrun, cpu_sleep, cpu_run}` |
| `0x9F` ID | — | `0x50454D55` |

The bridge has bus priority, so it works while the CPU runs. `overrun` and
`underrun` are sticky errors set if the host clocks faster than the bus can
follow (cannot happen at the specified SCK).

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
| `test/ibex_smoke` | Hand-encoded program on the converted Ibex alone |
| `scripts/ibex_flow.py equiv` | Formal equivalence of the converted Ibex |

The cocotb tests run on RTL (`test.yaml`) and on the gate-level netlist
after place and route (`gds.yaml`, `gl_test`).
