"""Test-bench helpers: pad wiring, waveform capture and program loading.

Pin indices: 0-7 ui_in, 8-15 uio, 16-23 uo_out (see docs/pe_isa.md 2.1).
"""
from __future__ import annotations

from pathlib import Path

from peemu import PEBlock, assemble, load_engine

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "pe"


def example(name: str, defines: dict | None = None):
    """Assemble examples/pe/<file>.pe and return its programs by name."""
    fname, _, prog = name.partition(":")
    text = (EXAMPLES / f"{fname}.pe").read_text()
    for k, v in (defines or {}).items():
        text = text.replace(f".define {k} ", f".define {k} {v} ;", 1)
    progs = {p.name: p for p in assemble(text)}
    return progs[prog] if prog else progs


def pad_level(blk: PEBlock, pin: int) -> int:
    """Level a chip output pad drives, or None if not driving (uio only)."""
    if 16 <= pin < 24:
        return (blk.uo_out >> (pin - 16)) & 1
    if 8 <= pin < 16:
        b = pin - 8
        if (blk.uio_oe >> b) & 1:
            return (blk.uio_out >> b) & 1
        return None
    raise ValueError(pin)


class Bus:
    """Open-drain/pull-up resolution for uio pins plus loopback wires.

    `pulldowns` is a dict pin -> callable(cycle) returning True to pull low
    (models external devices). Undriven uio pins idle high (pull-up).
    `loop` maps a source pad pin to a ui_in pin index it feeds.
    """

    def __init__(self, blk: PEBlock, loop: dict | None = None):
        self.blk = blk
        self.loop = loop or {}
        self.ext_low: dict = {}
        self.ui_static = 0

    def line(self, pin: int) -> int:
        lvl = pad_level(self.blk, pin) if pin >= 8 else None
        ext = self.ext_low.get(pin, False)
        if lvl == 0 or ext:
            return 0
        return 1

    def drive_inputs(self):
        ui = self.ui_static
        for src, dst in self.loop.items():
            v = self.line(src) if src < 16 else pad_level(self.blk, src)
            ui = (ui & ~(1 << dst)) | (v << dst)
        self.blk.ui_in = ui
        self.blk.uio_in = sum(self.line(8 + i) << i for i in range(8))


def run(blk: PEBlock, cycles: int, each=None):
    for _ in range(cycles):
        if each:
            each(blk)
        blk.step()


def capture(blk: PEBlock, cycles: int, pins, each=None) -> dict:
    """Run and record each pin's output level per cycle (before the step)."""
    wave = {p: [] for p in pins}

    def hook(b):
        if each:
            each(b)
        for p in pins:
            wave[p].append(pad_level(b, p))
    run(blk, cycles, hook)
    return wave


def drain(e) -> list[int]:
    out = []
    while (v := e.rx_pop()) is not None:
        out.append(v)
    return out


__all__ = ["PEBlock", "Bus", "example", "load_engine", "pad_level", "run", "capture", "drain"]
