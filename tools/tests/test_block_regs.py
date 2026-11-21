"""Block-level CPU controls (docs/pe_isa.md section 6): released pads, sticky
FIFO errors, STATUS, ENGINE_CTRL, SAFE_STATE and the interrupt lines.

Expectations come from the spec text; the RTL lockstep tests inherit them.
"""
from peemu import OWNER_CPU, OWNER_NONE, PEBlock, assemble_one, load_engine
from peemu.model import OWNER_SYS


def test_released_pads_drive_nothing():
    blk = PEBlock()
    blk.set_owner(range(8, 24), OWNER_CPU)
    blk.cpu_write_pins("out", {p: 1 for p in range(8, 24)})
    blk.cpu_write_pins("oe", {p: 1 for p in range(8, 16)})
    assert blk.uo_out == 0xFF and blk.uio_out == 0xFF and blk.uio_oe == 0xFF
    blk.set_owner([8, 17], OWNER_NONE)                  # registers keep their values ...
    assert blk.uio_oe == 0xFE and blk.uio_out == 0xFE   # ... but the pad lets go
    assert blk.uo_out == 0xFD
    blk.set_owner([9], 6)                               # reserved code behaves as NONE
    assert blk.uio_oe == 0xFC
    blk.set_od([10])                                    # open drain on an owned pad still works
    blk.cpu_write_pins("out", {10: 0})
    assert blk.uio_oe & 0x04 and not blk.uio_out & 0x04
    blk.set_owner([10], OWNER_NONE)                     # ... and is released too
    assert not blk.uio_oe & 0x04


def test_reset_owners():
    blk = PEBlock(owner_reset={23: OWNER_SYS})
    assert blk.owner[23] == OWNER_SYS and blk.owner[22] == OWNER_NONE
    assert blk.uo_out == 0 and blk.uio_oe == 0


def test_sticky_fifo_errors_and_status():
    blk = PEBlock()
    e = blk.engines[0]
    assert e.tx_push(1) and e.tx_push(2)
    assert e.status() >> 8 & 0xF == 2 and not e.status() >> 17 & 1
    assert not e.tx_push(3)                             # full: dropped, TXOVF
    assert list(e.txf) == [1, 2] and e.status() >> 17 & 1
    assert e.rx_pop() is None and e.status() >> 18 & 1  # empty: RXUNF
    e.clear_errors(1 << 17)
    assert not e.status() >> 17 & 1 and e.status() >> 18 & 1
    e.clear_errors(1 << 18)
    assert e.status() >> 17 & 3 == 0


def test_status_force_pending():
    blk = PEBlock()
    e = blk.engines[0]
    e.force(assemble_one(".program t\nwait 1 gpio 0").code[0])   # stalls while gpio 0 is low
    blk.step()
    assert e.status() >> 19 & 1                         # still pending
    blk.ui_in = 1
    for _ in range(4):
        blk.step()
    assert not e.status() >> 19 & 1                     # completed


def test_engine_ctrl_starts_engines_on_the_same_cycle():
    blk = PEBlock()
    p = assemble_one(".program t\nset pins, 1\nset pins, 0")
    for n, pin in ((0, 16), (2, 17)):
        load_engine(blk, n, p, origin=0, own=(pin,), enable=False, clkdiv_int=3,
                    set_base=pin, set_count=1)
    blk.step()
    blk.engine_ctrl(0b0101 | 0b0101 << 4 | 0b0101 << 8)   # EN, RESTART, DIV_RESTART: 0 and 2
    trace = []
    for _ in range(12):
        blk.step()
        trace.append(blk.uo_out & 3)
    assert all(v in (0, 3) for v in trace) and 3 in trace   # pins 16 and 17 always equal
    assert blk.engines[0].pc == blk.engines[2].pc
    blk.engine_ctrl(0)
    assert not any(e.enabled for e in blk.engines)


def test_safe_state():
    blk = PEBlock(owner_reset={23: OWNER_SYS})
    load_engine(blk, 1, assemble_one(".program t\nset pins, 1"), own=(8, 16),
                set_base=16, set_count=1)
    blk.set_owner([9], OWNER_CPU)
    blk.safe_state()
    assert not any(e.enabled for e in blk.engines)
    assert all(blk.owner[p] == OWNER_NONE for p in range(8, 23))
    assert blk.owner[23] == OWNER_SYS


def test_irq_lines():
    blk = PEBlock()
    e = blk.engines[1]
    blk.set_flags(0b1000_0001)
    assert blk.irq == 0                                 # nothing enabled
    blk.irq_en = 0x01
    assert blk.irq == 0x01
    blk.irq_en = 1 << (12 + 1)                          # engine 1: TX has space
    assert blk.irq == 1 << 9
    e.tx_push(0)
    e.tx_push(0)
    assert blk.irq == 0                                 # TX full
    blk.irq_en = 1 << (8 + 1)                           # engine 1: RX has data
    assert blk.irq == 0
    e.rxf.append(5)
    assert blk.irq == 1 << 9
