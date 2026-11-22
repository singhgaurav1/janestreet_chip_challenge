"""UART on the Protocol Engines (F4), firmware fw/apps/uart, checked by an
independent UART model (cocotbext-uart) on the pins.

TX on uo_out[0], RX on ui_in[0]. The firmware sends "PEMU\\n", then echoes
16 bytes, then sends 0x04. One test per baud rate (one image each)."""
from __future__ import annotations

import os
import random

import cocotb
from cocotb.triggers import RisingEdge, SimTimeoutError, Timer, with_timeout
from cocotbext.uart import UartSink, UartSource

from host import boot, check_bridge, firmware_state

ECHO_BYTES = 16


async def read_n(sink: UartSink, n: int) -> bytes:
    """cocotbext-uart 0.1.4 UartSink.read(n) returns once one byte is queued
    and then raises QueueEmpty if fewer than n are there; read one at a time."""
    out = bytearray()
    while len(out) < n:
        out += await sink.read(1)
    return bytes(out)


async def uart_echo(dut, baud: int):
    # uo_out[0] is low from reset until the firmware runs (sysctrl GPIO
    # resets to 0), which a UART reads as a break: attach the sink when the
    # firmware first drives the line idle high.
    sinks = []

    async def attach_sink():
        await RisingEdge(dut.uo0)
        sinks.append(UartSink(dut.uo0, baud=baud, bits=8))

    source = UartSource(dut.ui0, baud=baud, bits=8)
    host = await boot(dut, f"uart-{baud}", lambda: cocotb.start_soon(attach_sink()))
    ns_per_byte = 10 * 1_000_000_000 // baud

    try:
        for _ in range(2000):              # line idle high within 2 ms of the start
            if sinks:
                break
            await Timer(1, "us")
        else:
            raise SimTimeoutError()
        sink = sinks[0]
        banner = await with_timeout(read_n(sink, 5), 500_000 + 2 * 5 * ns_per_byte, "ns")
    except SimTimeoutError:
        raise AssertionError(f"no banner; {await firmware_state(host)}") from None
    assert banner == b"PEMU\n", f"banner {banner!r}"

    rng = random.Random(int(os.environ.get("SEED", "1")) ^ baud)
    data = bytes([0x00, 0xFF, 0x55, 0xAA]) + bytes(rng.randrange(256) for _ in range(ECHO_BYTES - 4))
    await source.write(data)
    # echo, then the end marker 0x04: exactly these bytes, nothing between.
    # After the marker the firmware exits and its safe state drives the pad
    # low (a break, fw/QUESTIONS.md A18), so the sink is not read further.
    want = data + b"\x04"
    try:
        echo = await with_timeout(read_n(sink, len(want)), 200_000 + 3 * len(want) * ns_per_byte, "ns")
    except SimTimeoutError:
        raise AssertionError(f"echo incomplete; {await firmware_state(host)}") from None
    assert echo == want, f"echo {echo.hex()} != {want.hex()}"
    code = await host.result(max_cycles=400_000)
    assert code == 0, f"firmware exit code {code}"
    await check_bridge(host)


@cocotb.test()
async def test_uart_115200(dut):
    await uart_echo(dut, 115_200)


@cocotb.test()
async def test_uart_1m(dut):
    await uart_echo(dut, 1_000_000)


@cocotb.test()
async def test_uart_3m(dut):
    await uart_echo(dut, 3_000_000)
