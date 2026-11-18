"""Example programs checked against independent protocol models (vip.py).

Clock: 60 MHz system clock throughout, as on silicon.
"""
import random

import pytest

import vip
from helpers import PEBlock, capture, drain, example, load_engine, pad_level
from peemu import OWNER_CPU, clkdiv_for, crc, isa

F = 60_000_000


def lvl(blk, pin):
    v = pad_level(blk, pin)
    return 1 if v is None else v


def warm(blk, ui=0, uio=0xFF, cycles=3):
    """Let the input synchronizers settle on idle levels before enabling engines."""
    blk.ui_in, blk.uio_in = ui, uio
    for _ in range(cycles):
        blk.step()


# ---- UART -------------------------------------------------------------------

@pytest.mark.parametrize("baud", [115_200, 1_000_000])
def test_uart_tx_against_reference_decoder(baud):
    blk = PEBlock()
    cpb = F / baud
    i, f = clkdiv_for(F, 8 * baud)
    load_engine(blk, 0, example("uart:uart_tx"), own=(16,), out_base=16, side_base=16,
                clkdiv_int=i, clkdiv_frac=f)
    data = bytes([0x55, 0x00, 0xFF, 0xA5, 0x3C, 0x81])
    pending = list(data)
    w = capture(blk, int(cpb * 10 * (len(data) + 2)), [16],
                each=lambda b: pending and b.engines[0].tx_push(pending[0]) and pending.pop(0))[16]
    assert vip.uart_decode(w, cpb) == list(data)
    # bit edges stay within one system cycle of the ideal fractional grid
    edges = [k for k in range(1, len(w)) if w[k] != w[k - 1]]
    t0 = edges[0]
    for e in edges:
        nbits = round((e - t0) / cpb)
        assert abs((e - t0) - nbits * cpb) <= 1.0


def run_uart_rx(wave, cpb, engine=0):
    blk = PEBlock()
    warm(blk, ui=1)
    i, f = clkdiv_for(F, 8 * F / cpb)
    load_engine(blk, engine, example("uart:uart_rx"), in_base=0, jmp_pin=0, clkdiv_int=i,
                clkdiv_frac=f)
    got = []
    for v in wave:
        blk.ui_in = v
        blk.step()
        got += [x >> 24 for x in drain(blk.engines[engine])]
    return blk, got


@pytest.mark.parametrize("err", [-0.025, 0.0, 0.025])
def test_uart_rx_back_to_back_with_baud_error(err):
    rnd = random.Random(7)
    data = bytes(rnd.randrange(256) for _ in range(24)) + b"\x00\xff\x55"
    cpb = 60 * (1 + err)          # 1 Mbaud nominal, sender off by err
    _, got = run_uart_rx(vip.uart_encode(data, cpb, gap_bits=0), 60)
    assert bytes(got) == data


def test_uart_rx_framing_error_flag():
    data = b"\x12\x34\x56"
    wave = vip.uart_encode(data, 60, gap_bits=2, stop_ok=lambda i: i != 1)
    blk, got = run_uart_rx(wave, 60, engine=2)
    assert got[0] == 0x12 and got[-1] == 0x56
    assert blk.flags & (1 << (4 + 2))     # 'evt set 4 rel' on engine 2


def test_uart_loopback_tx_to_rx_concurrently():
    """Self-test pattern: PE0 transmits, PE1 receives through an external wire."""
    blk = PEBlock()
    warm(blk, ui=1)
    p = example("uart")
    i, f = clkdiv_for(F, 8 * 1_000_000)
    load_engine(blk, 0, p["uart_tx"], origin=0, own=(16,), out_base=16, side_base=16,
                clkdiv_int=i, clkdiv_frac=f)
    load_engine(blk, 1, p["uart_rx"], origin=8, in_base=0, jmp_pin=0, clkdiv_int=i, clkdiv_frac=f)
    data = list(range(0, 256, 17))
    pend, got = list(data), []
    for _ in range(60 * 10 * (len(data) + 2)):
        if pend and blk.engines[0].tx_push(pend[0]):
            pend.pop(0)
        blk.ui_in = lvl(blk, 16)
        blk.step()
        got += [x >> 24 for x in drain(blk.engines[1])]
    assert got == data


# ---- SPI --------------------------------------------------------------------

@pytest.mark.parametrize("mode", [0, 1, 2, 3])
@pytest.mark.parametrize("clkdiv", [2, 4])
def test_spi_controller_all_modes(mode, clkdiv):
    cpol, cpha = mode >> 1, mode & 1
    prog = example("spi", {"CPOL": cpol})[f"spi_cpha{cpha}"]
    blk = PEBlock()
    warm(blk)
    blk.set_owner([18], OWNER_CPU)                     # CS
    blk.cpu_write_pins("out", {18: 1})
    load_engine(blk, 0, prog, own=(16, 17), out_base=16, side_base=17, in_base=1, clkdiv_int=clkdiv)
    rnd = random.Random(mode * 10 + clkdiv)
    mosi = bytes(rnd.randrange(256) for _ in range(6))
    miso = bytes(rnd.randrange(256) for _ in range(6))
    tgt = vip.SpiTarget(mode, miso)
    e = blk.engines[0]
    for _ in range(2 * clkdiv):          # engine parks SCK at its idle level (stalled OUT)
        blk.step()
    sck_idle_ok = lvl(blk, 17) == cpol   # ...before the CPU asserts CS
    pend, got = [b << 24 for b in mosi], []
    blk.cpu_write_pins("out", {18: 0})
    for c in range(clkdiv * 4 * 8 * 8 + 200):
        if pend and e.tx_push(pend[0]):
            pend.pop(0)
        cs, sck, mo = lvl(blk, 18), lvl(blk, 17), lvl(blk, 16)
        tgt.step(cs, sck, mo)
        blk.ui_in = tgt.miso << 1
        blk.step()
        got += [x & 0xFF for x in drain(e)]
    blk.cpu_write_pins("out", {18: 1})
    assert sck_idle_ok and lvl(blk, 17) == cpol
    assert bytes(tgt.rx) == mosi
    assert bytes(got) == miso


# ---- I2C --------------------------------------------------------------------

SDA, SCL = 8, 9


def i2c_word(byte, start=0, stop=0, ack_bit=1):
    return (start << 31) | (stop << 30) | (byte << 22) | (ack_bit << 21)


def run_i2c_ctrl(words, target, f_scl=400_000, cycles=None):
    blk = PEBlock()
    warm(blk)
    i, f = clkdiv_for(F, 8 * f_scl)
    load_engine(blk, 0, example("i2c:i2c_ctrl"), own=(SDA, SCL), od=(SDA, SCL), out_base=SDA,
                set_base=SDA, in_base=SDA, side_base=SCL, jmp_pin=SCL, clkdiv_int=i, clkdiv_frac=f,
                timeout=4000)
    e = blk.engines[0]
    mon = vip.I2CBusMonitor()
    pend, got = list(words), []
    cycles = cycles or int(F / f_scl * 10 * (len(words) + 2)) + 4 * target.stretch
    for _ in range(cycles):
        if pend and e.tx_push(pend[0]):
            pend.pop(0)
        sda = lvl(blk, SDA) & target.sda_out
        scl = lvl(blk, SCL) & target.scl_out
        target.step(scl, sda)
        sda = lvl(blk, SDA) & target.sda_out
        scl = lvl(blk, SCL) & target.scl_out
        mon.step(scl)
        blk.uio_in = 0xFC | sda | (scl << 1)
        blk.step()
        got += drain(e)
    return got, mon


def test_i2c_controller_write_then_read_with_stretching():
    tgt = vip.I2CTarget(addr=0x50, read_data=b"\xde\xad", stretch=900)
    words = [i2c_word(0x50 << 1, start=1), i2c_word(0x12), i2c_word(0x34, stop=1),
             i2c_word((0x50 << 1) | 1, start=1), i2c_word(0xFF, ack_bit=0), i2c_word(0xFF, stop=1)]
    got, mon = run_i2c_ctrl(words, tgt)
    assert tgt.log == ["S", "A50W", "P", "S", "A50R", "P"]
    assert tgt.received == [0x12, 0x34]
    assert [g & 1 for g in got[:4]] == [0, 0, 0, 0]          # address, data, data, address ACKed
    assert [g >> 1 & 0xFF for g in got[4:6]] == [0xDE, 0xAD]
    # Fast-mode timing: SCL low >= 1.3 us, high >= 0.6 us
    assert min(mon.lows) >= 1.3e-6 * F and min(mon.highs) >= 0.6e-6 * F
    assert max(mon.lows) >= 900                               # the stretch was honoured


def test_i2c_controller_sees_nack_for_absent_address():
    tgt = vip.I2CTarget(addr=0x50)
    got, _ = run_i2c_ctrl([i2c_word(0x51 << 1, start=1, stop=1)], tgt)
    assert got[0] & 1 == 1 and tgt.received == []


def preload_y(e, value):
    """HAL helper: load Y through the TX FIFO using forced instructions."""
    e.tx_push(value)
    e.force(isa.e_pull())
    yield
    e.force(isa.e_mov(2, 0, 6))
    yield


def run_i2c_target(ops, addr=0x2A, f_scl=100_000):
    blk = PEBlock()
    warm(blk)
    load_engine(blk, 1, example("i2c:i2c_target"), origin=32, own=(SDA,), od=(SDA,), set_base=SDA,
                in_base=SDA, jmp_pin=SCL, clkdiv_int=15, timeout=120, enable=False)
    e = blk.engines[1]
    for _ in preload_y(e, addr << 1):
        blk.step()
    e.enabled = True
    ctl = vip.I2CController(ops, half=round(F / f_scl / 2))
    got = []
    while not ctl.done:
        scl, sda_c = ctl.levels()
        sda = sda_c & lvl(blk, SDA)
        ctl.sample(sda)
        blk.uio_in = 0xFC | sda | (scl << 1)
        blk.step()
        got += [x & 0xFF for x in drain(e)]
    return got, ctl.acks


def test_i2c_target_engine_against_reference_controller():
    ops = [("start",), ("byte", 0x2A << 1), ("byte", 0x11), ("byte", 0xC3), ("stop",),
           ("start",), ("byte", 0x2B << 1), ("byte", 0x99), ("stop",),
           ("start",), ("byte", 0x2A << 1), ("byte", 0x7E), ("stop",)]
    got, acks = run_i2c_target(ops)
    assert got == [0x11, 0xC3, 0x7E]
    # ACK low for our address and its data; released (NACK) for the other target
    assert acks == [0, 0, 0, 1, 1, 0, 0]


def test_i2c_controller_and_target_engines_on_one_bus():
    """On-chip self-verification: PE0 controller and PE1 target share an external bus
    (uio 8/9 wired to uio 10/11 on the board)."""
    blk = PEBlock()
    warm(blk)
    i, f = clkdiv_for(F, 8 * 100_000)
    load_engine(blk, 0, example("i2c:i2c_ctrl"), own=(8, 9), od=(8, 9), out_base=8, set_base=8,
                in_base=8, side_base=9, jmp_pin=9, clkdiv_int=i, clkdiv_frac=f, timeout=4000)
    load_engine(blk, 1, example("i2c:i2c_target"), origin=32, own=(10,), od=(10,), set_base=10,
                in_base=10, jmp_pin=11, clkdiv_int=15, timeout=120, enable=False)
    t = blk.engines[1]
    for _ in preload_y(t, 0x2A << 1):
        blk.step()
    t.enabled = True
    words = [i2c_word(0x2A << 1, start=1), i2c_word(0xA1), i2c_word(0x5C, stop=1)]
    pend, ctrl_rx, tgt_rx = list(words), [], []
    for _ in range(600 * 10 * 5):
        if pend and blk.engines[0].tx_push(pend[0]):
            pend.pop(0)
        sda = lvl(blk, 8) & lvl(blk, 10)
        scl = lvl(blk, 9)
        blk.uio_in = sda | scl << 1 | sda << 2 | scl << 3 | 0xF0
        blk.step()
        ctrl_rx += drain(blk.engines[0])
        tgt_rx += [x & 0xFF for x in drain(t)]
    assert tgt_rx == [0xA1, 0x5C]
    assert [x & 1 for x in ctrl_rx] == [0, 0, 0]


# ---- NRZI + stuffing + CRC bitstream ---------------------------------------

USB16 = crc.CATALOGUE["CRC-16/USB"]


def frame_words(data: bytes, with_count=True):
    bits = crc.bits_lsb_first(b"\x80" + data)
    words = [sum(b << k for k, b in enumerate(bits[i:i + 32])) for i in range(0, len(bits), 32)]
    return ([len(bits) - 8 - 2] if with_count else []) + words


def run_bitstream(data: bytes, loopback=False, corrupt_at=None):
    blk = PEBlock()
    warm(blk)
    p = example("bitstream")
    load_engine(blk, 0, p["bitstream_tx"], own=(16, 17), out_base=16, set_base=16, clkdiv_int=20)
    if loopback:
        load_engine(blk, 1, p["bitstream_rx"], origin=32, in_base=0, jmp_pin=0, clkdiv_int=5,
                    enable=False)
        rx = blk.engines[1]
        nbits = 8 * len(data) + 16
        c = crc.crc_bytes(data, 16, 0x8005, 0xFFFF, True, True, 0xFFFF)
        res = crc.residue(16, 0x8005, 0xFFFF, crc.bits_lsb_first(data),
                          [(c >> k) & 1 for k in range(16)], reflect=True)
        for w in (nbits - 1, 0x80 << 24, res):
            rx.tx_push(w)
    pend = frame_words(data)
    dp, dm, out = [], [], []
    for cyc in range(40 * (8 * len(data) * 7 // 6 + 60)):
        if pend and blk.engines[0].tx_push(pend[0]):
            pend.pop(0)
        if loopback and cyc == 20:
            rx.enabled = True
        v = lvl(blk, 16)
        if corrupt_at is not None and corrupt_at <= cyc < corrupt_at + 40:
            v ^= 1
        dp.append(v)
        dm.append(lvl(blk, 17))
        blk.ui_in = v
        blk.step()
        if loopback:
            out += drain(rx)
    return dp, dm, out


def test_usb_crc16_residue_constant():
    data = b"\x01\x02\x03"
    c = crc.crc_bytes(data, 16, 0x8005, 0xFFFF, True, True, 0xFFFF)
    r = crc.residue(16, 0x8005, 0xFFFF, crc.bits_lsb_first(data), [(c >> k) & 1 for k in range(16)])
    assert r == 0x800D   # the well-known USB CRC16 residual


@pytest.mark.parametrize("data", [b"\x00\x01\xc3", b"\xff\xff\xff\x00\x7e", bytes(range(9, 30, 3))])
def test_bitstream_tx_against_reference_decoder(data):
    dp, dm, _ = run_bitstream(data)
    bits, se0, j_after = vip.bitstream_decode(dp, dm, 40)
    c = crc.crc_bytes(data, *USB16[:6])
    assert vip.bits_to_bytes_lsb(bits) == data + bytes([c & 0xFF, c >> 8])
    assert se0 == 2 and j_after


def test_bitstream_stuffing_actually_happens():
    dp, _, _ = run_bitstream(b"\xff\xff")
    # 16 ones in a row need two stuff bits: count line transitions in the data region
    trans = sum(1 for k in range(1, len(dp)) if dp[k] != dp[k - 1])
    assert trans >= 2 + 8   # SYNC transitions plus at least the stuff-bit toggles


def test_bitstream_loopback_crc_good_and_bad():
    data = b"\x10\xff\xfe\x42\x00"
    _, _, out = run_bitstream(data, loopback=True)
    status = out[-1]
    assert status & 1 == 1 and status & 2 == 0           # CRC residue matched, no stuff error
    words = out[:-1]
    nbits = 8 * len(data) + 16
    bits = []
    for k, w in enumerate(words):
        n = min(32, nbits - 32 * k)
        w >>= 32 - n                                        # right-shifting ISR: last word is top-aligned
        bits += [(w >> i) & 1 for i in range(n)]
    c = crc.crc_bytes(data, *USB16[:6])
    assert vip.bits_to_bytes_lsb(bits) == data + bytes([c & 0xFF, c >> 8])
    # flip the line for one bit time in the middle of the data
    _, _, out = run_bitstream(data, loopback=True, corrupt_at=40 * 30)
    assert out and out[-1] & 1 == 0
