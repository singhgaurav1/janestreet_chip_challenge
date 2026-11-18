"""PE instruction set: field definitions, encode/decode and disassembly.

Single source of truth for encodings, shared by the assembler, the golden
model and the RTL test benches. See docs/pe_isa.md.
"""
from __future__ import annotations

from dataclasses import dataclass

IMEM_DEPTH = 64
PROG_MAX = 32  # PC / jump targets are 5-bit offsets from ORIGIN

# Major opcodes [15:13]
OP_JMP, OP_WAIT, OP_IN, OP_OUT, OP_SYS, OP_MOV, OP_MATCH, OP_SET = range(8)

JMP_CONDS = ["", "!x", "x--", "!y", "y--", "pin", "!osre", "t"]
WAIT_SRCS = ["gpio", "pin", "evt", "jmppin"]
IN_SRCS = ["pins", "x", "y", "null", "bit", None, "isr", "osr"]
OUT_DSTS = ["pins", "x", "y", "null", "pindirs", "pc", "isr", "bit"]
MOV_DSTS = ["pins", "x", "y", "pindirs", "pc", "isr", "osr", "crc"]
MOV_SRCS = ["pins", "x", "y", "null", "status", "isr", "osr", "crc"]
MOV_OPS = ["", "~", "::", "~::"]
MATCH_SRCS = ["isr", "osr", "x", "crc", "pins", None, None, None]
SET_DSTS = ["pins", "x", "y", None, "pindirs", None, None, None]

# OP_SYS sub-ops [7:6]
SYS_PUSH, SYS_PULL, SYS_EVT, SYS_CTL = range(4)
EVT_OPS = ["set", "clr", "setwait", None]
CTL_OPS = ["sync", "assist on", "assist off", "crcinit"]
ASSIST_BITS = {"crc": 1, "stuff": 2, "coder": 4}


def _chk(name: str, val: int, bits: int) -> int:
    if not 0 <= val < (1 << bits):
        raise ValueError(f"{name}={val} does not fit in {bits} bits")
    return val


def word(op: int, operands: int, sd: int = 0) -> int:
    return (_chk("op", op, 3) << 13) | (_chk("side/delay", sd, 5) << 8) | _chk("operands", operands, 8)


def enc_n(n: int) -> int:
    """Bit counts 1..32 encode as 5 bits with 32 -> 0."""
    if not 1 <= n <= 32:
        raise ValueError(f"bit count {n} out of range 1..32")
    return n & 31


def dec_n(f: int) -> int:
    return f if f else 32


# ---- encoders (operand byte only; caller adds side/delay) -------------------

def e_jmp(cond: int, addr: int) -> int:
    return word(OP_JMP, (_chk("cond", cond, 3) << 5) | _chk("addr", addr, 5))


def e_wait(pol: int, src: int, idx: int) -> int:
    return word(OP_WAIT, (_chk("pol", pol, 1) << 7) | (_chk("src", src, 2) << 5) | _chk("idx", idx, 5))


def e_in(src: int, n: int) -> int:
    return word(OP_IN, (_chk("src", src, 3) << 5) | enc_n(n))


def e_out(dst: int, n: int) -> int:
    return word(OP_OUT, (_chk("dst", dst, 3) << 5) | enc_n(n))


def e_push(iffull: int = 0, block: int = 1) -> int:
    return word(OP_SYS, (SYS_PUSH << 6) | (iffull << 5) | (block << 4))


def e_pull(ifempty: int = 0, block: int = 1) -> int:
    return word(OP_SYS, (SYS_PULL << 6) | (ifempty << 5) | (block << 4))


def e_evt(op: int, n: int, rel: int = 0) -> int:
    return word(OP_SYS, (SYS_EVT << 6) | (_chk("evt op", op, 2) << 4) | (rel << 3) | _chk("flag", n, 3))


def e_ctl(sub: int, mask: int = 0) -> int:
    return word(OP_SYS, (SYS_CTL << 6) | (_chk("ctl", sub, 2) << 4) | _chk("mask", mask, 3))


def e_mov(dst: int, op: int, src: int) -> int:
    return word(OP_MOV, (_chk("dst", dst, 3) << 5) | (_chk("op", op, 2) << 3) | _chk("src", src, 3))


def e_match(src: int, n: int) -> int:
    return word(OP_MATCH, (_chk("src", src, 3) << 5) | enc_n(n))


def e_set(dst: int, val: int) -> int:
    return word(OP_SET, (_chk("dst", dst, 3) << 5) | _chk("imm", val, 5))


NOP = e_mov(2, 0, 2)  # mov y, y


# ---- side-set / delay field -------------------------------------------------

@dataclass(frozen=True)
class SideCfg:
    count: int = 0      # bits of [12:8] used for side-set, including the enable bit
    opt: bool = False   # top side bit is an enable
    pindirs: bool = False

    def __post_init__(self):
        if not 0 <= self.count <= 5:
            raise ValueError("side-set count must be 0..5")
        if self.opt and self.count == 0:
            raise ValueError("side-set opt needs count >= 1")

    @property
    def pins(self) -> int:
        return self.count - (1 if self.opt else 0)

    @property
    def delay_bits(self) -> int:
        return 5 - self.count

    def pack(self, side: int | None, delay: int) -> int:
        if delay < 0 or delay >= (1 << self.delay_bits):
            raise ValueError(f"delay {delay} exceeds {self.delay_bits} bits")
        if side is None:
            if self.count and not self.opt:
                raise ValueError("side-set value required (side-set is not optional)")
            s = 0
        else:
            if self.count == 0:
                raise ValueError("side-set used but .side_set not declared")
            if side < 0 or side >= (1 << self.pins):
                raise ValueError(f"side value {side} exceeds {self.pins} pins")
            s = side | ((1 << self.pins) if self.opt else 0)
        return (s << self.delay_bits) | delay

    def unpack(self, field: int) -> tuple[int | None, int]:
        """Return (side value or None, delay) from the 5-bit [12:8] field."""
        delay = field & ((1 << self.delay_bits) - 1)
        s = field >> self.delay_bits
        if self.count == 0:
            return None, delay
        if self.opt:
            if not (s >> self.pins) & 1:
                return None, delay
            s &= (1 << self.pins) - 1
        return s, delay


# ---- decode / disassemble ---------------------------------------------------

@dataclass(frozen=True)
class Decoded:
    op: int
    sd: int       # raw [12:8]
    a: int        # [7:5]
    b: int        # [4:0]
    raw: int

    @property
    def opnd(self) -> int:
        return self.raw & 0xFF


def decode(w: int) -> Decoded:
    if not 0 <= w <= 0xFFFF:
        raise ValueError("instruction must be 16 bits")
    return Decoded(op=w >> 13, sd=(w >> 8) & 31, a=(w >> 5) & 7, b=w & 31, raw=w)


def disasm(w: int, side: SideCfg = SideCfg()) -> str:
    d = decode(w)
    o = d.opnd
    if d.op == OP_JMP:
        c = JMP_CONDS[d.a]
        s = f"jmp {c + ', ' if c else ''}{d.b}"
    elif d.op == OP_WAIT:
        pol, src = o >> 7, (o >> 5) & 3
        if src == 2:
            s = f"wait {pol} evt {d.b & 7}" + (" rel" if d.b & 8 else "") + (" keep" if d.b & 16 else "")
        elif src == 3:
            s = f"wait {pol} jmppin"
        else:
            s = f"wait {pol} {WAIT_SRCS[src]} {d.b}"
    elif d.op == OP_IN:
        s = f"in {IN_SRCS[d.a] or '?'}, {dec_n(d.b)}"
    elif d.op == OP_OUT:
        s = f"out {OUT_DSTS[d.a]}, {dec_n(d.b)}"
    elif d.op == OP_SYS:
        sub = o >> 6
        if sub == SYS_PUSH:
            s = "push" + (" iffull" if o & 32 else "") + (" block" if o & 16 else " noblock")
        elif sub == SYS_PULL:
            s = "pull" + (" ifempty" if o & 32 else "") + (" block" if o & 16 else " noblock")
        elif sub == SYS_EVT:
            s = f"evt {EVT_OPS[(o >> 4) & 3] or '?'} {o & 7}" + (" rel" if o & 8 else "")
        else:
            c = (o >> 4) & 3
            s = CTL_OPS[c]
            if c in (1, 2):
                s += " " + ",".join(k for k, v in ASSIST_BITS.items() if o & v)
    elif d.op == OP_MOV:
        if w & ~0x1F00 == NOP:
            s = "nop"
        else:
            s = f"mov {MOV_DSTS[d.a]}, {MOV_OPS[(o >> 3) & 3]}{MOV_SRCS[o & 7]}"
    elif d.op == OP_MATCH:
        s = f"match {MATCH_SRCS[d.a] or '?'}, {dec_n(d.b)}"
    else:
        s = f"set {SET_DSTS[d.a] or '?'}, {d.b}"
    sv, dl = side.unpack(d.sd)
    if sv is not None:
        s += f" side {sv}"
    if dl:
        s += f" [{dl}]"
    return s
