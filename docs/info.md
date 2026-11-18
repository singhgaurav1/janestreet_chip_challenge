<!---
This file is used to generate the project datasheet.
-->

## How it works

A reprogrammable protocol emulator. A RISC-V CPU (lowRISC Ibex, RV32IMC) runs
firmware from a 2 KiB on-chip SRAM. Four cycle-exact Protocol Engines (being
added) drive the pins; this build contains the CPU, memory, system control and
the host interface.

The firmware is loaded at run time by a host (the RP2350 on the demo board)
over SPI. After reset the CPU is held; the host writes the firmware into SRAM,
then releases the CPU. The same SPI port can read and write memory and
registers while the CPU runs (debug).

Host SPI: mode 0, MSB first, SCK up to 10 MHz (clock / 6).

| Command | Bytes after the command | Effect |
|---|---|---|
| `0x01` | ctrl | bit 0 = CPU run |
| `0x02` | addr[31:0], data[31:0]... | write words, auto-increment |
| `0x03` | addr[31:0], dummy byte, then read data[31:0]... | read words |
| `0x05` | read status | {underrun, overrun, cpu_sleep, cpu_run} |
| `0x9F` | read 4 bytes | ID `0x50454D55` ("PEMU") |

Memory map: SRAM at `0x0000_0000` (reset vector `0x80`), system control at
`0x1000_0000` (ID, GPIO_OUT, GPIO_IN, UIO_OUT, UIO_OE, CYCLE, SCRATCH).

## How to test

1. Hold the CPU (it is held after reset) and read the ID with command `0x9F`.
2. Write a firmware image (`firmware/images/*.hex`, one word per line from
   address 0) with command `0x02` at address 0.
3. Send `0x01 0x01` to start the CPU.
4. `firmware/images/smoke.hex` sets `uo_out[6:0]` to `0x55`, drives `uio[7:4]`
   with `0xA` and writes `0x600D0009` to SCRATCH (`0x1000_0018`) on success.

## External hardware

The Tiny Tapeout demo board's RP2350 acts as the SPI host on `ui[7:5]` and `uo[7]`.
