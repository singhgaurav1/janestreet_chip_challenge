"""RP2350 side of the chip, written from docs/soc.md ("Host SPI protocol"):
the SPI loader/debug bridge and the firmware result convention. Shared by
the firmware protocol tests."""
from __future__ import annotations

import os
from pathlib import Path

from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

ROOT = Path(__file__).resolve().parents[3]
CLK_PS = 16_666                     # 60.0024 MHz (60 MHz fixed decision; cocotb needs an even period)
SYS_HZ = 60_000_000
SCRATCH = 0x1000_0018
RAM = 4096
STATUS_BLOCK = RAM - 64
CMD_WR_CTRL, CMD_WRITE, CMD_READ, CMD_STATUS, CMD_ID = 0x01, 0x02, 0x03, 0x05, 0x9F
ID_VALUE = 0x50454D55
ST_RUN, ST_OVERRUN, ST_UNDERRUN, ST_RUN_REFUSED = 0x01, 0x04, 0x08, 0x10
PANIC_BASE = 1000


def image(name: str, arch: str = os.environ.get("FW_ARCH", "rv32imc")) -> bytes:
    p = ROOT / f"fw/build/{arch}_chip/{name}.bin"
    if not p.exists():
        raise FileNotFoundError(f"{p}: run make -C fw ARCH={arch}")
    raw = p.read_bytes()
    return raw + bytes(-len(raw) % 4)


class Host:
    """SPI mode 0, MSB first; SCK = clk / (2 * half) (soc.md: <= clk / 6)."""

    def __init__(self, dut, half: int = 3):
        self.dut, self.half = dut, half

    async def xfer(self, out: bytes, nread: int = 0) -> bytes:
        d = self.dut
        await FallingEdge(d.clk)
        d.host_cs_n.value = 0
        await ClockCycles(d.clk, self.half, rising=False)
        rx = bytearray()
        for i in range(len(out) + nread):
            b = out[i] if i < len(out) else 0
            v = 0
            for bit in range(7, -1, -1):
                d.host_mosi.value = (b >> bit) & 1
                await ClockCycles(d.clk, self.half, rising=False)
                d.host_sck.value = 1
                v = (v << 1) | int(d.host_miso.value)
                await ClockCycles(d.clk, self.half, rising=False)
                d.host_sck.value = 0
            if i >= len(out):
                rx.append(v)
        await ClockCycles(d.clk, self.half, rising=False)
        d.host_cs_n.value = 1
        await ClockCycles(d.clk, 2 * self.half, rising=False)
        return bytes(rx)

    async def id(self) -> int:
        return int.from_bytes(await self.xfer(bytes([CMD_ID]), 4), "big")

    async def status(self) -> int:
        return (await self.xfer(bytes([CMD_STATUS]), 1))[0]

    async def ctrl(self, run: int):
        await self.xfer(bytes([CMD_WR_CTRL, run]))

    async def write(self, addr: int, words: list[int]):
        data = b"".join(w.to_bytes(4, "big") for w in words)
        await self.xfer(bytes([CMD_WRITE]) + addr.to_bytes(4, "big") + data)

    async def read(self, addr: int, n: int = 1) -> list[int]:
        rx = await self.xfer(bytes([CMD_READ]) + addr.to_bytes(4, "big") + b"\0", 4 * n)
        return [int.from_bytes(rx[4 * i:4 * i + 4], "big") for i in range(n)]

    async def load(self, img: bytes, verify: bool = True):
        """Image words are little-endian in memory; the bridge moves words."""
        words = [int.from_bytes(img[i:i + 4], "little") for i in range(0, len(img), 4)]
        await self.ctrl(0)
        await self.write(SCRATCH, [0])
        for i in range(0, len(words), 64):
            await self.write(4 * i, words[i:i + 64])
        if verify:
            assert await self.read(0, len(words)) == words, "image readback differs"

    async def result(self, max_cycles: int, every: int = 4000) -> int:
        """Poll SCRATCH until the firmware posts a result (odd). Returns the
        exit code: 0 pass, n fail, PANIC_BASE + reason on a panic."""
        waited = 0
        while waited < max_cycles:
            await ClockCycles(self.dut.clk, every)
            waited += every
            r = (await self.read(SCRATCH))[0]
            if r & 1:
                return 0 if r == 1 else r >> 1
        raise TimeoutError(f"no result after {max_cycles} cycles")


async def power_up(dut) -> Host:
    import cocotb
    cocotb.start_soon(Clock(dut.clk, CLK_PS, unit="ps").start())
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 10)
    host = Host(dut)
    assert await host.id() == ID_VALUE
    return host


async def boot(dut, name: str, before_run=None, verify: bool = False) -> Host:
    """Power up, load and start an image from fw/build. before_run() is
    called just before the CPU starts (e.g. to arm a pin watcher). No
    readback by default: the boot code checks the image CRC."""
    host = await power_up(dut)
    await host.load(image(name), verify=verify)
    if before_run:
        before_run()
    await host.ctrl(1)
    st = await host.status()
    assert st & ST_RUN and not st & ST_RUN_REFUSED, f"CPU did not start (status 0x{st:02x})"
    return host


async def firmware_state(host: Host) -> str:
    """SCRATCH and the status block, for failure messages."""
    sc = (await host.read(SCRATCH))[0]
    st = await host.read(STATUS_BLOCK, 9)
    names = ["magic", "state", "reason", "detail", "mcause", "mepc", "mtval", "ra", "sp"]
    return f"SCRATCH 0x{sc:x}; " + " ".join(f"{n}=0x{v:x}" for n, v in zip(names, st))


async def check_bridge(host: Host):
    st = await host.status()
    assert not st & (ST_OVERRUN | ST_UNDERRUN), f"bridge error status 0x{st:02x}"
