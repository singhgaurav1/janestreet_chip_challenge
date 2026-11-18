"""Per-engine configuration and its packing into CPU-visible registers.

Field layout follows docs/pe_isa.md section 6. `EngineConfig.to_regs()` is
what the firmware HAL and the host loader write; `from_regs()` is used by the
RTL test benches to compare register images.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace

# (register, field, lsb, width, encode, decode)
_ID = (lambda v: v, lambda v: v)
_M1 = (lambda v: v - 1, lambda v: v + 1)          # stored as value-1
_Z32 = (lambda v: v & 31, lambda v: v or 32)       # 32 stored as 0

LAYOUT = [
    ("CLKDIV", "clkdiv_int", 16, 16, _ID),
    ("CLKDIV", "clkdiv_frac", 8, 8, _ID),
    ("EXECCTRL", "wrap_bottom", 0, 5, _ID),
    ("EXECCTRL", "wrap_top", 5, 5, _ID),
    ("EXECCTRL", "jmp_pin", 10, 5, _ID),
    ("EXECCTRL", "filter", 15, 1, _ID),
    ("EXECCTRL", "sync_edge", 16, 2, _ID),
    ("EXECCTRL", "autosync", 18, 1, _ID),
    ("EXECCTRL", "side_count", 19, 3, _ID),
    ("EXECCTRL", "side_opt", 22, 1, _ID),
    ("EXECCTRL", "side_pindirs", 23, 1, _ID),
    ("EXECCTRL", "origin", 24, 6, _ID),
    ("SHIFTCTRL", "in_shift_right", 0, 1, _ID),
    ("SHIFTCTRL", "out_shift_right", 1, 1, _ID),
    ("SHIFTCTRL", "autopush", 2, 1, _ID),
    ("SHIFTCTRL", "autopull", 3, 1, _ID),
    ("SHIFTCTRL", "push_thresh", 4, 5, _Z32),
    ("SHIFTCTRL", "pull_thresh", 9, 5, _Z32),
    ("PINCTRL", "out_base", 0, 5, _ID),
    ("PINCTRL", "out_count", 5, 6, _ID),
    ("PINCTRL", "set_base", 11, 5, _ID),
    ("PINCTRL", "set_count", 16, 3, _ID),
    ("PINCTRL", "side_base", 19, 5, _ID),
    ("PINCTRL", "in_base", 24, 5, _ID),
    ("ASSIST", "coder_mode", 0, 2, _ID),
    ("ASSIST", "coder_diff", 2, 1, _ID),
    ("ASSIST", "coder_idle", 3, 1, _ID),
    ("ASSIST", "stuff_mode", 4, 2, _ID),
    ("ASSIST", "stuff_n", 6, 3, _M1),
    ("ASSIST", "bit_ticks", 9, 4, _M1),
    ("ASSIST", "crc_width", 13, 5, _M1),
    ("ASSIST", "crc_init_ones", 18, 1, _ID),
    ("ASSIST", "stamp_en", 19, 1, _ID),
    ("ASSIST", "stamp_count", 20, 4, _M1),
    ("ASSIST", "crc_reflect", 24, 1, _ID),
    ("CRC_POLY", "crc_poly", 0, 32, _ID),
    ("TIMING", "timeout", 0, 16, _ID),
    ("TIMING", "sync_phase", 16, 16, _ID),
]
REG_OFFSETS = {"CTRL": 0x00, "CLKDIV": 0x04, "EXECCTRL": 0x08, "SHIFTCTRL": 0x0C,
               "PINCTRL": 0x10, "ASSIST": 0x14, "CRC_POLY": 0x18, "TIMING": 0x1C,
               "INSTR": 0x20, "TXF": 0x24, "RXF": 0x28, "STATUS": 0x2C}
CONFIG_REGS = ["CLKDIV", "EXECCTRL", "SHIFTCTRL", "PINCTRL", "ASSIST", "CRC_POLY", "TIMING"]

CODER_MODES = {"nrz": 0, "nrzi": 1, "nrzi1": 2, "manchester": 3}
STUFF_MODES = {"any": 0, "ones": 1, "zeros": 2}
SYNC_EDGES = {"any": 0, "rise": 1, "fall": 2}


@dataclass
class EngineConfig:
    clkdiv_int: int = 1
    clkdiv_frac: int = 0
    wrap_bottom: int = 0
    wrap_top: int = 31
    jmp_pin: int = 0
    filter: int = 0
    sync_edge: int = 0
    autosync: int = 0
    side_count: int = 0
    side_opt: int = 0
    side_pindirs: int = 0
    origin: int = 0
    in_shift_right: int = 1
    out_shift_right: int = 1
    autopush: int = 0
    autopull: int = 0
    push_thresh: int = 32
    pull_thresh: int = 32
    out_base: int = 0
    out_count: int = 0
    set_base: int = 0
    set_count: int = 0
    side_base: int = 0
    in_base: int = 0
    coder_mode: int = 0
    coder_diff: int = 0
    coder_idle: int = 0
    stuff_mode: int = 0
    stuff_n: int = 6
    bit_ticks: int = 1
    crc_width: int = 16
    crc_init_ones: int = 0
    stamp_en: int = 0
    stamp_count: int = 1
    crc_reflect: int = 0
    crc_poly: int = 0
    timeout: int = 0
    sync_phase: int = 0

    def updated(self, **kw) -> "EngineConfig":
        bad = set(kw) - {f.name for f in fields(self)}
        if bad:
            raise KeyError(f"unknown config field(s): {', '.join(sorted(bad))}")
        return replace(self, **kw)

    def validate(self) -> None:
        for reg, name, lsb, width, (enc, _) in LAYOUT:
            v = enc(getattr(self, name))
            if not 0 <= v < (1 << width):
                raise ValueError(f"{name}={getattr(self, name)} out of range for {width}-bit field")
        if self.set_count > 5:
            raise ValueError("set_count must be 0..5")
        if self.out_count > 32:
            raise ValueError("out_count must be 0..32")
        if self.side_count > 5:
            raise ValueError("side_count must be 0..5")

    def to_regs(self) -> dict[str, int]:
        self.validate()
        regs = {r: 0 for r in CONFIG_REGS}
        for reg, name, lsb, width, (enc, _) in LAYOUT:
            regs[reg] |= enc(getattr(self, name)) << lsb
        return regs

    @classmethod
    def from_regs(cls, regs: dict[str, int]) -> "EngineConfig":
        kw = {}
        for reg, name, lsb, width, (_, dec) in LAYOUT:
            kw[name] = dec((regs[reg] >> lsb) & ((1 << width) - 1))
        return cls(**kw)

    @property
    def div_period(self) -> float:
        return (self.clkdiv_int or 65536) + self.clkdiv_frac / 256


def clkdiv_for(sys_hz: float, tick_hz: float) -> tuple[int, int]:
    """Integer and 1/256 fractional divider for a desired tick rate."""
    d = sys_hz / tick_hz
    if d < 1 or d >= 65536:
        raise ValueError(f"divider {d:.3f} out of range 1..65535")
    q = round(d * 256)
    return q >> 8, q & 0xFF
