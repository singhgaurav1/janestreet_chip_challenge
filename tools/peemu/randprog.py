"""Random valid PE programs and configs.

Used by the model fuzz tests now and by the RTL lockstep tests later (same
seed -> same program, so a mismatch is reproducible from its seed).
"""
from __future__ import annotations

import random

from . import isa
from .config import EngineConfig


def random_instr(rnd: random.Random, length: int, side: isa.SideCfg) -> int:
    op = rnd.randrange(8)
    a, b = rnd.randrange(8), rnd.randrange(32)
    if op == isa.OP_JMP:
        w = isa.e_jmp(a, rnd.randrange(length))
    elif op == isa.OP_WAIT:
        src = rnd.randrange(4)
        idx = rnd.randrange(32) if src != 2 else rnd.randrange(32)
        w = isa.e_wait(rnd.randrange(2), src, idx)
    elif op == isa.OP_IN:
        src = rnd.choice([0, 1, 2, 3, 4, 6, 7])
        w = isa.e_in(src, 1 if src == 4 else rnd.randint(1, 32))
    elif op == isa.OP_OUT:
        dst = rnd.randrange(8)
        n = 1 if dst == 7 else rnd.randint(1, 32)
        if dst == 5:
            n = rnd.randint(1, 5)
        w = isa.e_out(dst, n)
    elif op == isa.OP_SYS:
        sub = rnd.randrange(4)
        if sub == isa.SYS_PUSH:
            w = isa.e_push(rnd.randrange(2), rnd.randrange(2))
        elif sub == isa.SYS_PULL:
            w = isa.e_pull(rnd.randrange(2), rnd.randrange(2))
        elif sub == isa.SYS_EVT:
            w = isa.e_evt(rnd.randrange(3), rnd.randrange(8), rnd.randrange(2))
        else:
            w = isa.e_ctl(rnd.randrange(4), rnd.randrange(8))
    elif op == isa.OP_MOV:
        w = isa.e_mov(a, rnd.randrange(4), rnd.randrange(8))
    elif op == isa.OP_MATCH:
        w = isa.e_match(rnd.randrange(5), rnd.randint(1, 32))
    else:
        w = isa.e_set(rnd.choice([0, 1, 2, 4]), b)
    sv = rnd.randrange(1 << side.pins) if side.count and (not side.opt or rnd.random() < 0.5) else None
    dl = rnd.randrange(1 << side.delay_bits) if rnd.random() < 0.3 else 0
    return w | side.pack(sv, dl) << 8


def random_engine(rnd: random.Random, length: int | None = None, origin: int = 0,
                  serial: bool = False):
    """Return (code, EngineConfig) for a random but legal program.

    serial=True biases toward the serial assists: the program starts by
    enabling CRC, stuffer and line coder, and most instructions move bits
    through `out bit` / `in bit`, so stuff-bit insertion, removal and stuff
    errors actually occur."""
    length = length or rnd.randint(2, isa.PROG_MAX)
    sc = rnd.randrange(4)
    side = isa.SideCfg(sc, opt=bool(sc) and rnd.random() < 0.5, pindirs=rnd.random() < 0.2)
    code = [random_instr(rnd, length, side) for _ in range(length)]
    if serial:
        for k in range(1, length):
            r = rnd.random()
            if r < 0.35:
                code[k] = isa.e_out(7, 1)
            elif r < 0.6:
                code[k] = isa.e_in(4, 1)
        code[0] = isa.e_ctl(1, 7)                   # assist on crc, stuff, coder
    wb = rnd.randrange(length)
    cfg = EngineConfig(
        clkdiv_int=rnd.choice([1, 1, 2, 3, 7]), clkdiv_frac=rnd.choice([0, 0, 64, 200]),
        wrap_bottom=wb, wrap_top=rnd.randrange(wb, length), jmp_pin=rnd.randrange(32),
        filter=rnd.randrange(2), sync_edge=rnd.randrange(3), autosync=rnd.randrange(2),
        side_count=side.count, side_opt=int(side.opt), side_pindirs=int(side.pindirs), origin=origin,
        in_shift_right=rnd.randrange(2), out_shift_right=rnd.randrange(2),
        autopush=rnd.randrange(2), autopull=int(serial) or rnd.randrange(2),
        push_thresh=rnd.randint(1, 32), pull_thresh=rnd.randint(1, 32),
        out_base=rnd.randrange(32), out_count=rnd.randint(0, 32), set_base=rnd.randrange(32),
        set_count=rnd.randint(0, 5), side_base=rnd.randrange(32), in_base=rnd.randrange(32),
        coder_mode=rnd.randrange(4), coder_diff=rnd.randrange(2), coder_idle=rnd.randrange(2),
        stuff_mode=rnd.randrange(3), stuff_n=rnd.randint(1, 3 if serial else 8),
        bit_ticks=rnd.randint(1, 16),
        crc_width=rnd.randint(1, 32), crc_init_ones=rnd.randrange(2), crc_reflect=rnd.randrange(2),
        stamp_en=int(rnd.random() < 0.1), stamp_count=rnd.randint(1, 16),
        crc_poly=rnd.getrandbits(32), timeout=rnd.choice([0, 0, 3, 50]), sync_phase=0)
    cfg = cfg.updated(sync_phase=rnd.randrange(cfg.clkdiv_int or 1))
    cfg.validate()
    return code, cfg
