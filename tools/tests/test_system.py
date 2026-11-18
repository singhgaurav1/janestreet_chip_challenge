"""System-level and property tests of the PE block."""
import random

import pytest

import vip
from helpers import PEBlock, drain, example, load_engine
from peemu import clkdiv_for, isa
from peemu.model import DRIVEABLE, M32
from peemu.randprog import random_engine
from test_protocols import F, i2c_word, lvl, preload_y, warm


@pytest.mark.parametrize("err", [-0.04, -0.01, 0.0, 0.02, 0.04])
def test_autosync_preserves_tick_count_under_drift(err):
    """Nearest-slot resync: exactly 8 ticks per 'bit' between edges, whatever the drift."""
    D = 10
    blk = PEBlock()
    load_engine(blk, 0, example("uart:uart_tx"), own=(), clkdiv_int=D, autosync=1, sync_edge=0,
                jmp_pin=0, sync_phase=3)
    e = blk.engines[0]
    e.enabled = True
    period = 8 * D * (1 + err)
    edges = {round(200 + k * period) for k in range(40)}   # a transition every 'bit'
    level, at_detect = 1, []
    for c in range(max(edges) + 50):
        if c in edges:
            level ^= 1
        blk.ui_in = level
        f_prev = e.f_prev
        blk.step()
        if e.f_prev != f_prev:          # edge detected this cycle; count ticks up to and incl. it
            at_detect.append(e.ticks)
    counts = [b - a for a, b in zip(at_detect, at_detect[1:])]
    assert counts[2:] and set(counts[2:]) == {8}, counts


def test_four_engines_concurrently_from_one_memory():
    """UART TX->RX and I2C controller->target, all four engines at once, 62 of 64 words."""
    p_uart, p_i2c = example("uart"), example("i2c")
    blk = PEBlock()
    warm(blk, ui=1)
    i, f = clkdiv_for(F, 8 * 1_000_000)
    load_engine(blk, 0, p_uart["uart_tx"], origin=0, own=(16,), out_base=16, side_base=16,
                clkdiv_int=i, clkdiv_frac=f)
    load_engine(blk, 1, p_uart["uart_rx"], origin=4, in_base=0, jmp_pin=0, clkdiv_int=i, clkdiv_frac=f)
    ci, cf = clkdiv_for(F, 8 * 400_000)
    load_engine(blk, 2, p_i2c["i2c_ctrl"], origin=12, own=(8, 9), od=(8, 9), out_base=8, set_base=8,
                in_base=8, side_base=9, jmp_pin=9, clkdiv_int=ci, clkdiv_frac=cf, timeout=4000)
    load_engine(blk, 3, p_i2c["i2c_target"], origin=32, own=(10,), od=(10,), set_base=10, in_base=10,
                jmp_pin=11, clkdiv_int=15, timeout=120, enable=False)
    used = sum(len(p.code) for p in (p_uart["uart_tx"], p_uart["uart_rx"], p_i2c["i2c_ctrl"],
                                       p_i2c["i2c_target"]))
    assert used == 62
    for _ in preload_y(blk.engines[3], 0x42 << 1):
        blk.step()
    blk.engines[3].enabled = True
    u_data = list(b"concurrent!")
    i_words = [i2c_word(0x42 << 1, start=1), i2c_word(0x5A), i2c_word(0xA5), i2c_word(0x0F, stop=1)]
    u_got, i_got, ctrl_rx = [], [], []
    for _ in range(60_000):
        for eng, q in ((0, u_data), (2, i_words)):
            if q and blk.engines[eng].tx_push(q[0]):
                q.pop(0)
        sda = lvl(blk, 8) & lvl(blk, 10)
        scl = lvl(blk, 9)
        blk.ui_in = lvl(blk, 16)
        blk.uio_in = sda | scl << 1 | sda << 2 | scl << 3 | 0xF0
        blk.step()
        u_got += [x >> 24 for x in drain(blk.engines[1])]
        ctrl_rx += drain(blk.engines[2])
        i_got += [x & 0xFF for x in drain(blk.engines[3])]
    assert bytes(u_got) == b"concurrent!"
    assert i_got == [0x5A, 0xA5, 0x0F]
    assert [x & 1 for x in ctrl_rx] == [0, 0, 0, 0]


@pytest.mark.parametrize("seed", range(60))
def test_random_programs_keep_invariants(seed):
    """Fuzz: random legal programs on all engines. Checks structural invariants
    (the RTL lockstep test reuses this generator with the same seeds)."""
    rnd = random.Random(seed)
    blk = PEBlock()
    origins = [0, 16, 32, 48]
    owners = {}
    for n in range(4):
        code, cfg = random_engine(rnd, length=rnd.randint(2, 16), origin=origins[n])
        blk.load(code, origins[n])
        e = blk.engines[n]
        e.configure(cfg)
        e.enabled = True
    for p in DRIVEABLE:
        blk.owner[p] = rnd.choice([0, 1, 2, 3, 7])
    blk.set_od([p for p in range(8, 16) if rnd.random() < 0.3])
    for c in range(400):
        blk.ui_in = rnd.getrandbits(8)
        blk.uio_in = rnd.getrandbits(8)
        for e in blk.engines:
            if rnd.random() < 0.2:
                e.tx_push(rnd.getrandbits(32))
            if rnd.random() < 0.2:
                e.rx_pop()
        if rnd.random() < 0.05:
            blk.set_flags(rnd.getrandbits(8), rnd.getrandbits(8))
        before = blk.pad_out
        blk.step()
        for e in blk.engines:
            assert 0 <= e.pc < 32
            assert len(e.txf) <= 4 and len(e.rxf) <= 4
            assert 0 <= e.isr_cnt <= 32 and 0 <= e.osr_cnt <= 32
            for v in (e.x, e.y, e.isr, e.osr):
                assert 0 <= v <= M32
            assert e.crc < (1 << e.cfg.crc_width)
            assert 0 <= e.delay < 32
        changed = before ^ blk.pad_out
        assert changed & ~sum(1 << p for p in DRIVEABLE if blk.owner[p] in (0, 1, 2, 3)) == 0
        assert 0 <= blk.flags < 256
