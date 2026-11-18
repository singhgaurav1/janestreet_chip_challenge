"""Directed tests of engine semantics with exact cycle expectations.

Each expectation is derived by hand from docs/pe_isa.md section 4 and is a
contract the RTL lockstep tests inherit.
"""
import pytest

from helpers import PEBlock, capture, drain, load_engine, pad_level, run
from peemu import OWNER_CPU, assemble_one, crc, isa


def prog(src, **cfg):
    return assemble_one(".program t\n" + src), cfg


def setup(src, engine=0, origin=0, own=(16,), blk=None, **cfg):
    blk = blk or PEBlock()
    p = assemble_one(".program t\n" + src)
    load_engine(blk, engine, p, origin=origin, own=own, **cfg)
    return blk, blk.engines[engine]


def first_cycle(blk, cycles, cond, each=None):
    for c in range(cycles):
        if each:
            each(blk, c)
        blk.step()
        if cond(blk):
            return c
    return None


# ---- timing contract ------------------------------------------------------

def test_input_to_output_latency():
    """Pad edge in cycle 5 -> WAIT completes in 7 (2-flop sync) -> SET in 8."""
    blk, e = setup("wait 1 gpio 0\n set pins, 1\n", set_base=16, set_count=1)
    c = first_cycle(blk, 20, lambda b: b.uo_out & 1,
                    each=lambda b, c: setattr(b, "ui_in", int(c >= 5)))
    assert c == 8


def test_delay_cycles_exact():
    blk, e = setup("set pins, 1 [3]\n set pins, 0 [1]\n", set_base=16, set_count=1)
    w = capture(blk, 30, [16])[16]
    # first pad update is visible after cycle 0
    assert w[1:13] == [1, 1, 1, 1, 0, 0] * 2


def test_divider_integer_and_fraction():
    blk, e = setup("set pins, 1\n set pins, 0\n", set_base=16, set_count=1,
                   clkdiv_int=2, clkdiv_frac=128)
    run(blk, 1000)
    assert e.ticks == 400   # period 2.5 cycles
    blk, e = setup("set pins, 1\n set pins, 0\n", set_base=16, set_count=1, clkdiv_int=0)
    run(blk, 70000)
    assert e.ticks == 2     # INT = 0 means 65536


def test_side_set_applies_while_stalled_and_wins_over_set():
    blk, e = setup(".side_set 1 opt\n pull side 1\n set pins, 0 side 0\n", side_base=16,
                   set_base=16, set_count=1)
    run(blk, 5)
    assert pad_level(blk, 16) == 1 and e.stalled
    e.tx_push(0)
    run(blk, 1)               # pull completes
    run(blk, 1)               # set pins 0 + side 0
    assert pad_level(blk, 16) == 0
    # side-set beats SET on the same pin in the same instruction
    blk, e = setup(".side_set 1\n set pins, 0 side 1\n", side_base=16, set_base=16, set_count=1)
    run(blk, 2)
    assert pad_level(blk, 16) == 1


def test_wrap_and_relocation():
    src = "set x, 1\n.wrap_target\n mov isr, x\n push noblock\n set x, 2\n.wrap\n"
    blk, e = setup(src, origin=40)
    run(blk, 7)
    assert drain(e)[:2] == [1, 2]
    assert blk.imem[40] == assemble_one(".program t\n" + src).code[0]


# ---- control flow and data ops ---------------------------------------------

def test_jmp_xdec_semantics():
    blk, e = setup("set x, 2\nloop: jmp x--, loop\n mov isr, x\n push\nend: jmp end\n")
    run(blk, 10)
    assert drain(e) == [0xFFFFFFFF]


@pytest.mark.parametrize("cond,x,y,expect", [
    ("!x", 0, 1, 1), ("!x", 3, 1, 0), ("!y", 0, 0, 1), ("y--", 0, 2, 1), ("y--", 0, 0, 0),
])
def test_jmp_conditions(cond, x, y, expect):
    src = f"set x, {x}\n set y, {y}\n jmp {cond}, yes\n set x, 0\n jmp out\nyes: set x, 1\nout: mov isr, x\n push\nh: jmp h\n"
    blk, e = setup(src)
    run(blk, 12)
    assert drain(e) == [expect]


def test_jmp_osre_and_pin():
    blk, e = setup("pull\n out null, 8\n jmp !osre, a\n set x, 0\n jmp o\na: set x, 1\n"
                   "o: mov isr, x\n push\n jmp pin, b\n set x, 0\n jmp p\nb: set x, 1\n"
                   "p: mov isr, x\n push\nh: jmp h\n", pull_thresh=16, jmp_pin=0)
    e.tx_push(0)
    blk.ui_in = 1
    run(blk, 20)
    assert drain(e) == [1, 1]


def test_shift_directions_and_autopush_autopull():
    blk, e = setup("out x, 4\n in x, 4\n", autopull=1, autopush=1, pull_thresh=8, push_thresh=8,
                   out_shift_right=0, in_shift_right=0)
    e.tx_push(0xA5000000)
    run(blk, 4)
    assert drain(e) == [0xA5]
    blk, e = setup("out x, 4\n in x, 4\n", autopull=1, autopush=1, pull_thresh=8, push_thresh=8,
                   out_shift_right=1, in_shift_right=1)
    e.tx_push(0x000000A5)
    run(blk, 4)
    assert drain(e) == [0xA5000000]


def test_out_stalls_when_fifo_empty_and_in_stalls_when_full():
    blk, e = setup("out x, 32\n", autopull=1)
    run(blk, 3)
    assert e.stalled and e.pc == 0
    blk, e = setup("in null, 32\n", autopush=1, push_thresh=32)
    run(blk, 10)
    assert len(e.rxf) == 4 and e.stalled


def test_push_pull_nonblocking():
    blk, e = setup("set x, 7\n pull noblock\n mov isr, osr\n push\n")
    run(blk, 4)
    assert drain(e) == [7]   # empty FIFO: OSR <- X
    blk, e = setup("set x, 1\nl: mov isr, x\n push noblock\n jmp l\n")
    run(blk, 40)
    assert len(e.rxf) == 4   # full FIFO drops


def test_mov_ops():
    blk, e = setup("set x, 1\n mov isr, ~x\n push\n mov isr, ::x\n push\n mov isr, ~::x\n push\n"
                   "mov y, status\n mov isr, y\n push\nh: jmp h\n")
    run(blk, 12)
    out = drain(e)
    assert out[:3] == [0xFFFFFFFE, 0x80000000, 0x7FFFFFFF]
    assert out[3] & 0b100 == 0b100   # tx fifo empty bit


def test_match_low_bits_and_right_shift_alignment():
    blk, e = setup("set y, 0x15\n pull\n mov x, osr\n match x, 5\n jmp t, a\n set x, 0\n jmp o\na: set x, 1\n"
                   "o: mov isr, x\n push\n match x, 6\n jmp t, b\n set x, 0\n jmp p\nb: set x, 1\n"
                   "p: mov isr, x\n push\nh: jmp h\n")
    e.tx_push(0x35)
    run(blk, 20)
    assert drain(e) == [1, 0]
    # right-shifting ISR: newest 3 bits are at the top; Y holds them left-aligned
    blk, e = setup("pull\n mov y, osr\n set x, 5\n in x, 3\n match isr, 3\n mov isr, status\n push\n",
                   in_shift_right=1)
    e.tx_push(5 << 29)
    run(blk, 8)
    assert drain(e)[0] & 1 == 1


def test_wait_timeout_sets_t():
    blk, e = setup("wait 1 gpio 0\n jmp t, to\n set x, 0\n jmp o\nto: set x, 1\no: mov isr, x\n push\n"
                   "h: jmp h\n", timeout=5)
    c = first_cycle(blk, 30, lambda b: len(e.rxf) == 1)
    assert drain(e) == [1]
    assert c == 8   # wait times out in cycle 4; jmp 5, set 6, mov 7, push 8
    # a satisfied wait clears T
    blk, e = setup("wait 1 gpio 0\n mov isr, status\n push\n", timeout=5)
    e.t = 1
    blk.ui_in = 1
    run(blk, 8)
    assert drain(e)[0] & 1 == 0


# ---- events, forced instructions, crossbar ---------------------------------

def test_event_setwait_handshake_cycle_exact():
    blk = PEBlock()
    load_engine(blk, 0, assemble_one(".program a\n evt setwait 0\n set pins, 1\nh: jmp h\n"),
                origin=0, own=(16,), set_base=16, set_count=1)
    load_engine(blk, 1, assemble_one(".program b\n wait 1 evt 0\n set pins, 1\nh: jmp h\n"),
                origin=8, own=(17,), set_base=17, set_count=1)
    rise = {}
    for c in range(8):
        blk.step()
        for p in (16, 17):
            if pad_level(blk, p) and p not in rise:
                rise[p] = c
    assert rise == {17: 2, 16: 3}
    assert blk.flags == 0


def test_event_rel_index_and_cpu_flags():
    blk, e = setup("evt set 1 rel\n", engine=2)
    run(blk, 1)
    assert blk.flags == 1 << 3
    blk, e = setup("wait 1 evt 6 keep\n set x, 1\n mov isr, x\n push\n")
    run(blk, 3)
    assert not e.rxf
    blk.set_flags(set_mask=1 << 6)
    run(blk, 4)
    assert drain(e) == [1] and blk.flags == 1 << 6


def test_forced_instructions_run_while_disabled():
    blk, e = setup("h: jmp h\n")
    e.enabled = False
    for w in (isa.e_set(1, 21), isa.e_mov(5, 0, 1), isa.e_push()):
        e.force(w)
        run(blk, 1)
    assert drain(e) == [21]
    e.force(isa.e_jmp(0, 9))
    run(blk, 1)
    assert e.pc == 9


def test_pad_ownership_and_open_drain():
    blk = PEBlock()
    load_engine(blk, 0, assemble_one(".program a\n set pins, 1\nh: jmp h\n"), own=(16,),
                set_base=16, set_count=1)
    load_engine(blk, 1, assemble_one(".program b\n set pins, 3\nh: jmp h\n"), origin=4, own=(17,),
                set_base=16, set_count=2)
    run(blk, 3)
    assert blk.uo_out & 3 == 0b11   # pin 16 from PE0, pin 17 from PE1, PE1's write to 16 dropped
    blk.set_owner([16], OWNER_CPU)
    blk.cpu_write_pins("out", {16: 0})
    run(blk, 2)
    assert blk.uo_out & 1 == 0
    # open drain: out=0 drives low, out=1 releases
    blk = PEBlock()
    load_engine(blk, 0, assemble_one(".program a\n set pins, 0 [3]\n set pins, 1 [3]\n"), own=(8,), od=(8,),
                set_base=8, set_count=1)
    run(blk, 2)
    assert (blk.uio_oe & 1, blk.uio_out & 1) == (1, 0)
    run(blk, 4)
    assert blk.uio_oe & 1 == 0


# ---- clock recovery, timestamper, filter -----------------------------------

def test_hard_sync_reload_timing():
    # tick every 10 cycles; a falling edge on pin 0 in cycle p re-phases the
    # divider: detected at p+2, next tick at p+3+SYNC_PHASE
    blk, e = setup("sync\nl: set pins, 1\n set pins, 0\n jmp l\n", set_base=16, set_count=1,
                   clkdiv_int=10, jmp_pin=0, sync_edge=2, sync_phase=7)
    blk.ui_in = 1
    ticks = []
    last = e.ticks
    for c in range(80):
        blk.ui_in = 0 if c >= 33 else 1
        blk.step()
        if e.ticks != last:
            ticks.append(c)
            last = e.ticks
    after = [t for t in ticks if t > 30]
    assert after[0] == 33 + 3 + 7 and after[1] - after[0] == 10   # natural grid would be 40


def test_glitch_filter_rejects_single_cycle_pulse():
    for filt, expect in ((0, 1), (1, 0)):
        blk, e = setup("wait 1 jmppin\n wait 0 jmppin\n set pins, 1\n", set_base=16, set_count=1,
                       jmp_pin=0, filter=filt)
        run(blk, 30, lambda b: setattr(b, "ui_in", 0 if b.cycle == 10 else 1))
        assert (blk.uo_out & 1) == expect


def test_timestamper_records_changes():
    blk, e = setup("h: jmp h\n", stamp_en=1, stamp_count=2, in_base=0)
    edges = {5: 1, 25: 3, 26: 2}
    v = 0
    for c in range(40):
        v = edges.get(c, v)
        blk.ui_in = v
        blk.step()
    recs = [(r >> 16, r & 0xFFFF) for r in drain(e)]
    # first record = initial state; then deltas between observed changes
    assert recs == [(0, 0), (7, 1), (20, 3), (1, 2)]


# ---- serial assists -------------------------------------------------------

def test_manchester_and_diff_tx():
    blk, e = setup("out bit, 1 [3]\n", autopull=1, out_base=16, own=(16, 17), coder_mode=3,
                   coder_diff=1, bit_ticks=4)
    e.force(isa.e_ctl(1, 4))
    run(blk, 1)
    e.tx_push(0b0110)
    w = capture(blk, 18, [16, 17])
    bits = w[16][1:17]
    assert bits == [1, 1, 0, 0, 0, 0, 1, 1, 0, 0, 1, 1, 1, 1, 0, 0]
    assert all(a != b for a, b in zip(w[16][1:17], w[17][1:17]))


def test_crc_catalogue_via_engine():
    src = "pull\n out x, 32\n crcinit\n assist on crc\nl: out bit, 1\n jmp x--, l\n mov isr, crc\n push\n"
    for name, (w, poly, init, refin, refout, xorout, check) in crc.CATALOGUE.items():
        assert crc.crc_bytes(b"123456789", w, poly, init, refin, refout, xorout) == check, name
        assert init in (0, (1 << w) - 1)
        reflect = refin and refout
        bits = [(b >> (i if refin else 7 - i)) & 1 for b in b"123456789" for i in range(8)]
        words = [sum(bit << i for i, bit in enumerate(bits[k:k + 32])) for k in range(0, len(bits), 32)]
        p = int(f"{poly:0{w}b}"[::-1], 2) if reflect else poly
        blk, e = setup(src, own=(), autopull=1, out_base=24, crc_width=w, crc_poly=p,
                       crc_reflect=int(reflect), crc_init_ones=int(init != 0))
        for x in [len(bits) - 1] + words:
            assert e.tx_push(x)
        run(blk, 200)
        assert drain(e)[0] ^ xorout == check, name


def test_stuffer_inserts_stuff_bits():
    # 8 ones, N=3 'ones' stuffing, 2 ticks per bit -> 111 0 111 0 11
    blk, e = setup("pull\n assist on stuff\nl: out bit, 1\n jmp !osre, l\nh: jmp h\n", out_base=16,
                   pull_thresh=8, stuff_mode=1, stuff_n=3, bit_ticks=2)
    e.tx_push(0xFF)
    w = capture(blk, 30, [16])[16]
    # w[i] is the pad before step i; the first OUT bit executes in cycle 2
    assert w[3:23:2] == [1, 1, 1, 0, 1, 1, 1, 0, 1, 1]


def test_destuffer_drops_stuff_bits_and_flags_errors():
    src = "assist on stuff\n nop [2]\nl: in bit, 1 [1]\n jmp l\n"
    for wire, data, err in (([1, 1, 1, 0, 1, 1, 0], [1, 1, 1, 1, 1, 0], 0),
                            ([1, 1, 1, 1, 0], [1, 1, 1, 1, 0], 1)):
        blk, e = setup(src, own=(), in_base=0, stuff_mode=1, stuff_n=3, bit_ticks=3,
                       autopush=1, push_thresh=len(data), in_shift_right=0)
        # sample k happens in cycle 4+3k and sees the pad from cycle 3k+2
        # (2-flop sync), so hold wire[k] over cycles 3k+2 .. 3k+4
        for c in range(3 * len(wire) + 6):
            k = max(0, (c - 2) // 3)
            blk.ui_in = wire[k] if k < len(wire) else 0
            blk.step()
        got = drain(e)
        assert got and got[0] == int("".join(map(str, data)), 2)
        assert e.serr == err and e.t == err


@pytest.mark.parametrize("sp,pad_edge,expect", [
    (8, 25, [20, 27, 36, 46]),   # pending tick 3 cycles away belongs to the old slot: emit now
    (8, 31, [30, 42, 52]),       # same slot: move 40 -> 42
    (0, 20, [20, 33, 43]),       # tick 2 cycles ago already served this slot: skip one
])
def test_autosync_snap_cases(sp, pad_edge, expect):
    """D=10 free-running ticks at 0,10,20,...; edge detected 2 cycles after the pad edge."""
    blk, e = setup("l: jmp l\n", own=(), clkdiv_int=10, autosync=1, sync_edge=0, jmp_pin=0,
                   sync_phase=sp)
    ticks, last = [], 0
    for c in range(60):
        blk.ui_in = int(c >= pad_edge)
        blk.step()
        if e.ticks != last:
            ticks.append(c)
            last = e.ticks
    assert [t for t in ticks if t >= expect[0]][:len(expect)] == expect


def test_timestamper_drops_record_when_program_pushes_same_cycle():
    blk, e = setup("l: push noblock\n jmp l\n", stamp_en=1, stamp_count=1, in_base=0)
    # first stamp (initial state) coincides with the first push in cycle 0
    blk.step()
    assert len(e.rxf) == 1 and e.ovf == 1
