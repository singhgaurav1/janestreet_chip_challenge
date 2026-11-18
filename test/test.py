# SPDX-FileCopyrightText: © 2026 singhgaurav1
# SPDX-License-Identifier: Apache-2.0
"""System tests through the chip's real pins: the host SPI bridge loads
firmware, starts Ibex and checks the result. Runs on RTL and on the
gate-level netlist (GATES=yes)."""
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

CLK_PS = 16666                  # 60 MHz (must be even in simulator ticks)
SCK_HALF = 3                    # clk cycles per SCK half period -> 10 MHz SCK
FW = Path(__file__).resolve().parent.parent / "firmware" / "images"

SCK, CS_N, MOSI, MISO = 7, 6, 5, 7     # ui_in / uo_out bit positions
CMD_WR_CTRL, CMD_WRITE, CMD_READ, CMD_STATUS, CMD_ID = 0x01, 0x02, 0x03, 0x05, 0x9F
SYS_SCRATCH = 0x1000_0018


class HostSPI:
    """Bit-banged SPI mode 0 host, like the RP2350 on the demo board."""

    def __init__(self, dut):
        self.dut = dut
        self.pins = 1 << CS_N

    def _drive(self):
        self.dut.ui_in.value = self.pins

    def _set(self, bit, v):
        self.pins = (self.pins | (1 << bit)) if v else (self.pins & ~(1 << bit))

    async def xfer(self, out: bytes, n_in: int = 0) -> bytes:
        self._set(CS_N, 0)
        self._drive()
        await ClockCycles(self.dut.clk, SCK_HALF)
        rx = 0
        for i, byte in enumerate(out + bytes(n_in)):
            for k in range(7, -1, -1):
                self._set(MOSI, (byte >> k) & 1)
                self._set(SCK, 0)
                self._drive()
                await ClockCycles(self.dut.clk, SCK_HALF)
                rx = (rx << 1) | ((int(self.dut.uo_out.value) >> MISO) & 1)   # sample at the rising edge
                self._set(SCK, 1)
                self._drive()
                await ClockCycles(self.dut.clk, SCK_HALF)
        self._set(SCK, 0)
        self._set(CS_N, 1)
        self._drive()
        await ClockCycles(self.dut.clk, 4 * SCK_HALF)
        return rx.to_bytes(len(out) + n_in, "big")[len(out):]

    async def write(self, addr: int, words):
        payload = b"".join(w.to_bytes(4, "big") for w in words)
        await self.xfer(bytes([CMD_WRITE]) + addr.to_bytes(4, "big") + payload)

    async def read(self, addr: int, n: int = 1):
        rx = await self.xfer(bytes([CMD_READ]) + addr.to_bytes(4, "big") + b"\0", 4 * n)
        return [int.from_bytes(rx[4 * i:4 * i + 4], "big") for i in range(n)]

    async def ctrl(self, run: int):
        await self.xfer(bytes([CMD_WR_CTRL, run]))

    async def status(self) -> int:
        return (await self.xfer(bytes([CMD_STATUS]), 1))[0]


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PS, unit="ps").start())
    dut.ena.value = 1
    dut.ui_in.value = 1 << CS_N
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 10)


def load_image(name: str):
    return [int(line, 16) for line in (FW / f"{name}.hex").read_text().split()]


@cocotb.test()
async def test_host_bridge(dut):
    """ID, status, and SRAM/peripheral write-read through the SPI bridge with the CPU held."""
    await reset(dut)
    host = HostSPI(dut)
    assert int.from_bytes(await host.xfer(bytes([CMD_ID]), 4), "big") == 0x50454D55
    assert await host.status() & 1 == 0, "CPU must be held in reset after power-up"
    pattern = [0x01234567, 0x89ABCDEF, 0xFFFFFFFF, 0x00000000, 0xA5A5A5A5]
    await host.write(0x100, pattern)
    assert await host.read(0x100, len(pattern)) == pattern
    await host.write(SYS_SCRATCH, [0xCAFEF00D])
    assert await host.read(SYS_SCRATCH) == [0xCAFEF00D]
    assert await host.status() & 0x0C == 0, "no bridge overrun/underrun"


@cocotb.test()
async def test_firmware_smoke(dut):
    """Load firmware/tests/smoke.c over SPI, run it on Ibex, check its report."""
    await reset(dut)
    host = HostSPI(dut)
    image = load_image("smoke")
    await host.write(0, image)
    assert await host.read(0, 4) == image[:4]
    await host.ctrl(1)
    result = 0
    for _ in range(40):
        await ClockCycles(dut.clk, 1000)
        result = (await host.read(SYS_SCRATCH))[0]       # peek while the CPU runs
        if result >> 16 in (0x600D, 0xBAD0, 0xDEAD):
            break
    dut._log.info(f"firmware result 0x{result:08x}")
    assert result == 0x600D0009, f"firmware reported 0x{result:08x}"
    assert int(dut.uo_out.value) & 0x7F == 0x55
    assert int(dut.uio_oe.value) == 0xF0 and int(dut.uio_out.value) & 0xF0 == 0xA0
    await ClockCycles(dut.clk, 50)
    assert await host.status() & 0x02, "CPU should be asleep (wfi) after main returns"
    # hold the CPU again: it must stop and the bus must still answer the host
    await host.ctrl(0)
    assert await host.status() & 1 == 0
    assert await host.read(0, 1) == image[:1]
