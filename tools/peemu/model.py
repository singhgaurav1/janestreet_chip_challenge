"""Cycle-accurate golden model of the Protocol Engine block.

One call to `PEBlock.step()` is one system clock cycle. The model implements
docs/pe_isa.md section 4 literally: all engines read one start-of-cycle
snapshot (pins, event flags), and pin/flag updates commit at the end of the
cycle. The RTL is checked against this model in lockstep.

The CPU side (register writes, FIFO access) is modelled as happening between
cycles: a call made before `step()` is seen by the engines in that cycle.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from . import isa
from .config import EngineConfig

M32 = 0xFFFFFFFF
N_ENGINES = 4
OWNER_CPU, OWNER_SYS, OWNER_NONE = 4, 5, 7
DRIVEABLE = range(8, 24)   # uio 8..15, uo_out 16..23
BIDIR = range(8, 16)


def rotr(v: int, s: int) -> int:
    s &= 31
    return ((v >> s) | (v << (32 - s))) & M32 if s else v & M32


def rev32(v: int) -> int:
    return int(f"{v & M32:032b}"[::-1], 2)


def mask(n: int) -> int:
    return M32 if n >= 32 else (1 << n) - 1


@dataclass
class Snapshot:
    pins: int     # 32-bit pin index space as seen this cycle
    flags: int    # event flags at start of cycle


@dataclass
class Effects:
    pin_writes: list = field(default_factory=list)  # (engine, kind 'out'|'oe', idx, bit)
    flag_set: int = 0
    flag_clr: int = 0


STALL, DONE, REEXEC = "stall", "done", "reexec"


class Engine:
    def __init__(self, block: "PEBlock", idx: int, fifo_depth: int):
        self.blk = block
        self.idx = idx
        self.depth = fifo_depth
        self.cfg = EngineConfig()
        self.enabled = False
        self.txf: deque[int] = deque()
        self.rxf: deque[int] = deque()
        self.x = self.y = self.isr = self.osr = 0
        self.t = self.serr = self.ovf = 0
        self.crc = 0
        self.div_cnt = self.div_acc = 0
        self.h1 = self.h2 = self.f_prev = 0
        self.jpin = 0
        self.stamp_prev = None
        self.stamp_t = 0
        self.force_instr: int | None = None
        self.restart()
        self.stalled = False
        self.ticks = 0

    # ---- CPU-side controls ------------------------------------------------
    def restart(self):
        self.pc = 0
        self.isr_cnt = 0
        self.osr_cnt = 32
        self.delay = 0
        self.wait_cnt = 0
        self.evt_pending = False
        self.sync_armed = False
        self.crc_on = self.stuff_on = self.coder_on = False
        self.run = 0
        self.last = 0
        self.tx_lvl = self.rx_prev = 0
        self.man_cnt = 0
        self.man_bit = 0

    def div_restart(self):
        self.div_cnt = 0
        self.div_acc = 0

    def configure(self, cfg: EngineConfig):
        cfg.validate()
        self.cfg = cfg

    def tx_push(self, v: int) -> bool:
        if len(self.txf) >= self.depth:
            return False
        self.txf.append(v & M32)
        return True

    def rx_pop(self) -> int | None:
        return self.rxf.popleft() if self.rxf else None

    def force(self, instr: int):
        self.force_instr = instr & 0xFFFF

    # ---- helpers ----------------------------------------------------------
    def _flag_index(self, n: int, rel: bool) -> int:
        n &= 7
        return (n & 4) | ((n + self.idx) & 3) if rel else n

    def _window(self, eff: Effects, kind: str, base: int, count: int, data: int):
        for i in range(count):
            eff.pin_writes.append((self.idx, kind, (base + i) & 31, (data >> i) & 1))

    def _crc_feed(self, b: int):
        w = self.cfg.crc_width
        wm = mask(w)
        if self.cfg.crc_reflect:   # right-shifting LFSR, CRC_POLY holds the reflected polynomial
            fb = b ^ (self.crc & 1)
            self.crc >>= 1
        else:
            fb = b ^ ((self.crc >> (w - 1)) & 1)
            self.crc = (self.crc << 1) & wm
        if fb:
            self.crc ^= self.cfg.crc_poly & wm

    def _stuff_target(self) -> int:
        return (self.last, 1, 0, 1)[self.cfg.stuff_mode]

    def _stuff_update(self, b: int):
        if self.cfg.stuff_mode == 0:
            self.run = self.run + 1 if b == self.last else 1
            self.last = b
        else:
            self.run = self.run + 1 if b == self._stuff_target() else 0

    def _coder_drive(self, eff: Effects, b: int):
        mode = self.cfg.coder_mode if self.coder_on else 0
        if mode == 0:
            lvl = b
        elif mode == 1:
            lvl = self.tx_lvl if b else 1 - self.tx_lvl
        elif mode == 2:
            lvl = 1 - self.tx_lvl if b else self.tx_lvl
        else:
            half = self.cfg.bit_ticks // 2
            if half:
                lvl, self.man_bit, self.man_cnt = 1 - b, b, half
            else:
                lvl = b
        self._drive_level(eff, lvl)

    def _drive_level(self, eff: Effects, lvl: int):
        self.tx_lvl = lvl
        eff.pin_writes.append((self.idx, "out", self.cfg.out_base & 31, lvl))
        if self.cfg.coder_diff:
            eff.pin_writes.append((self.idx, "out", (self.cfg.out_base + 1) & 31, 1 - lvl))

    def _shift_out(self, n: int) -> int:
        if self.cfg.out_shift_right:
            data = self.osr & mask(n)
            self.osr = (self.osr >> n) & M32 if n < 32 else 0
        else:
            data = (self.osr >> (32 - n)) & mask(n)
            self.osr = (self.osr << n) & M32 if n < 32 else 0
        self.osr_cnt = min(32, self.osr_cnt + n)
        return data

    def _shift_in(self, data: int, n: int):
        data &= mask(n)
        if n == 32:
            self.isr = data
        elif self.cfg.in_shift_right:
            self.isr = ((self.isr >> n) | (data << (32 - n))) & M32
        else:
            self.isr = ((self.isr << n) | data) & M32
        self.isr_cnt = min(32, self.isr_cnt + n)

    def _autopull(self) -> bool:
        """Refill OSR if autopull is due. False means the OUT must stall."""
        c = self.cfg
        if c.autopull and self.osr_cnt >= c.pull_thresh:
            if not self.txf:
                return False
            self.osr = self.txf.popleft()
            self.osr_cnt = 0
        return True

    def status_word(self) -> int:
        return (self.t | self.serr << 1 | int(not self.txf) << 2 |
                int(len(self.rxf) >= self.depth) << 3 | (len(self.txf) & 15) << 4 |
                (len(self.rxf) & 15) << 8)

    def state(self) -> dict:
        """Architectural state compared against the RTL every cycle in lockstep tests."""
        return {"pc": self.pc, "x": self.x, "y": self.y, "isr": self.isr, "osr": self.osr,
                "isr_cnt": self.isr_cnt, "osr_cnt": self.osr_cnt, "t": self.t, "serr": self.serr,
                "delay": self.delay, "crc": self.crc, "div_cnt": self.div_cnt,
                "txf": list(self.txf), "rxf": list(self.rxf)}

    # ---- per-cycle --------------------------------------------------------
    def cycle(self, snap: Snapshot, eff: Effects):
        c = self.cfg
        s = (snap.pins >> c.jmp_pin) & 1
        f = int(s + self.h1 + self.h2 >= 2) if c.filter else s
        edge = f != self.f_prev
        qual = edge and (c.sync_edge == 0 or (c.sync_edge == 1 and f) or (c.sync_edge == 2 and not f))
        self.jpin = f

        rx_before = len(self.rxf)        # the RX FIFO has one write port per cycle
        tick = False
        if self.enabled:
            if qual and self.sync_armed:     # frame start: plain reload, no tick this cycle
                self.div_cnt = c.sync_phase
                self.div_acc = 0
                self.sync_armed = False
            elif qual and c.autosync:        # in-frame resync: nearest-slot snap
                tick = self._div_snap()
            else:
                tick = self._div_step()

        if self.force_instr is not None:
            if self._issue(self.force_instr, snap, eff, forced=True) != STALL:
                self.force_instr = None
        elif tick:
            self.ticks += 1
            if self.man_cnt:
                self.man_cnt -= 1
                if self.man_cnt == 0:
                    self._drive_level(eff, self.man_bit)
            if self.delay:
                self.delay -= 1
                self.stalled = False
            else:
                w = self.blk.imem[(c.origin + self.pc) % isa.IMEM_DEPTH]
                self.stalled = self._issue(w, snap, eff, forced=False) == STALL

        if self.enabled and c.stamp_en:
            self._stamp(snap, rx_before)

        self.h2, self.h1, self.f_prev = self.h1, s, f

    def _div_step(self) -> bool:
        c = self.cfg
        if self.div_cnt == 0:
            acc = self.div_acc + c.clkdiv_frac
            self.div_acc = acc & 0xFF
            self.div_cnt = (c.clkdiv_int or 65536) - 1 + (acc >> 8)
            return True
        self.div_cnt -= 1
        return False

    def _div_snap(self) -> bool:
        """Hard sync: re-phase the tick grid so a tick lands SYNC_PHASE+1 cycles
        after this (edge-detect) cycle, mapping the pending tick to the nearest
        new slot so no tick is added or lost (docs/pe_isa.md 5.4)."""
        c = self.cfg
        period = c.clkdiv_int or 65536
        d = self.div_cnt - (c.sync_phase + 1)     # pending tick relative to the new grid point
        self.div_acc = 0
        if 2 * d < -period:          # pending tick belongs to the slot before: emit it now
            self.div_cnt = c.sync_phase
            return True
        if 2 * d >= period:          # the slot was already used by the last tick: skip one
            self.div_cnt = c.sync_phase + period
            return False
        self.div_cnt = c.sync_phase  # same slot: move it
        return False

    def _stamp(self, snap: Snapshot, rx_before: int):
        cur = rotr(snap.pins, self.cfg.in_base) & mask(self.cfg.stamp_count)
        self.stamp_t = min(self.stamp_t + 1, 0xFFFF)
        if self.stamp_prev is None or cur != self.stamp_prev or self.stamp_t == 0xFFFF:
            rec = ((0 if self.stamp_prev is None else self.stamp_t) << 16) | cur
            # one FIFO write port: a record is dropped (OVF) if the FIFO is full
            # or the program pushed in this same cycle
            if len(self.rxf) == rx_before and rx_before < self.depth:
                self.rxf.append(rec)
            else:
                self.ovf = 1
            self.stamp_prev = cur
            self.stamp_t = 0

    def _issue(self, w: int, snap: Snapshot, eff: Effects, forced: bool) -> str:
        c = self.cfg
        d = isa.decode(w)
        side = isa.SideCfg(c.side_count, bool(c.side_opt), bool(c.side_pindirs))
        sval, dly = side.unpack(d.sd)
        res, target = self._execute(d, snap, eff)
        if sval is not None:  # after op writes so side-set wins on overlap
            self._window(eff, "oe" if c.side_pindirs else "out", c.side_base, side.pins, sval)
        if res == STALL:
            return STALL
        if res == REEXEC:
            if forced:
                return STALL
            self.delay = c.bit_ticks - 1
            return REEXEC
        self.wait_cnt = 0
        self.evt_pending = False
        if forced:
            if target is not None:
                self.pc = target
            return DONE
        if target is not None:
            self.pc = target
        else:
            self.pc = c.wrap_bottom if self.pc == c.wrap_top else (self.pc + 1) & 31
        self.delay = dly
        return DONE

    def _execute(self, d: isa.Decoded, snap: Snapshot, eff: Effects):
        c = self.cfg
        o = d.opnd
        op = d.op

        if op == isa.OP_JMP:
            k = d.a
            if k == 0:
                take = True
            elif k == 1:
                take = self.x == 0
            elif k == 2:
                take = self.x != 0
                self.x = (self.x - 1) & M32
            elif k == 3:
                take = self.y == 0
            elif k == 4:
                take = self.y != 0
                self.y = (self.y - 1) & M32
            elif k == 5:
                take = self.jpin == 1
            elif k == 6:
                take = self.osr_cnt < c.pull_thresh
            else:
                take = self.t == 1
            return DONE, (d.b if take else None)

        if op == isa.OP_WAIT:
            pol, src, idx = o >> 7, (o >> 5) & 3, d.b
            if src == 0:
                v = (snap.pins >> idx) & 1
            elif src == 1:
                v = (snap.pins >> ((c.in_base + idx) & 31)) & 1
            elif src == 2:
                fi = self._flag_index(idx, bool(idx & 8))
                v = (snap.flags >> fi) & 1
            else:
                v = self.jpin
            if v == pol:
                if src == 2 and pol == 1 and not idx & 16:
                    eff.flag_clr |= 1 << fi
                if c.timeout:
                    self.t = 0
                return DONE, None
            if c.timeout:
                self.wait_cnt += 1
                if self.wait_cnt >= c.timeout:
                    self.t = 1
                    return DONE, None
            return STALL, None

        if op == isa.OP_IN:
            src = d.a
            n = 1 if src == 4 else isa.dec_n(d.b)
            if c.autopush and self.isr_cnt + n >= c.push_thresh and len(self.rxf) >= self.depth:
                return STALL, None
            if src == 4:
                lvl = (snap.pins >> (c.in_base & 31)) & 1
                mode = c.coder_mode if self.coder_on else 0
                if mode == 1:
                    b = int(lvl == self.rx_prev)
                elif mode == 2:
                    b = int(lvl != self.rx_prev)
                else:
                    b = lvl
                self.rx_prev = lvl
                if self.stuff_on and self.run >= c.stuff_n:
                    if b == 1 - self._stuff_target():
                        self._stuff_update(b)
                        return REEXEC, None
                    self.t = 1          # stuff error: deliver the bit, restart the run
                    self.serr = 1
                    self.run = 0
                    self.last = b
                elif self.stuff_on:
                    self._stuff_update(b)
                if self.crc_on:
                    self._crc_feed(b)
                data = b
            else:
                data = {0: rotr(snap.pins, c.in_base), 1: self.x, 2: self.y, 3: 0,
                        5: 0, 6: self.isr, 7: self.osr}[src]
            self._shift_in(data, n)
            if c.autopush and self.isr_cnt >= c.push_thresh:
                self.rxf.append(self.isr)
                self.isr = 0
                self.isr_cnt = 0
            return DONE, None

        if op == isa.OP_OUT:
            dst = d.a
            if dst == 7:
                if self.stuff_on and self.run >= c.stuff_n:
                    sb = 1 - self._stuff_target()
                    self._stuff_update(sb)
                    self._coder_drive(eff, sb)
                    return REEXEC, None
                if not self._autopull():
                    return STALL, None
                b = self._shift_out(1)
                if self.crc_on:
                    self._crc_feed(b)
                if self.stuff_on:
                    self._stuff_update(b)
                self._coder_drive(eff, b)
                return DONE, None
            n = isa.dec_n(d.b)
            if not self._autopull():
                return STALL, None
            data = self._shift_out(n)
            target = None
            if dst == 0:
                self._window(eff, "out", c.out_base, n, data)
            elif dst == 1:
                self.x = data
            elif dst == 2:
                self.y = data
            elif dst == 4:
                self._window(eff, "oe", c.out_base, n, data)
            elif dst == 5:
                target = data & 31
            elif dst == 6:
                self.isr = data
                self.isr_cnt = n
            return DONE, target

        if op == isa.OP_SYS:
            sub = o >> 6
            if sub == isa.SYS_PUSH:
                if o & 32 and self.isr_cnt < c.push_thresh:
                    return DONE, None
                if len(self.rxf) >= self.depth:
                    if o & 16:
                        return STALL, None
                else:
                    self.rxf.append(self.isr)
                self.isr = 0
                self.isr_cnt = 0
                return DONE, None
            if sub == isa.SYS_PULL:
                if o & 32 and self.osr_cnt < c.pull_thresh:
                    return DONE, None
                if not self.txf:
                    if o & 16:
                        return STALL, None
                    self.osr = self.x
                else:
                    self.osr = self.txf.popleft()
                self.osr_cnt = 0
                return DONE, None
            if sub == isa.SYS_EVT:
                eop = (o >> 4) & 3
                bit = 1 << self._flag_index(o & 7, bool(o & 8))
                if eop == 0:
                    eff.flag_set |= bit
                elif eop == 1:
                    eff.flag_clr |= bit
                elif eop == 2:
                    if not self.evt_pending:
                        eff.flag_set |= bit
                        self.evt_pending = True
                        return STALL, None
                    if snap.flags & bit:
                        return STALL, None
                return DONE, None
            sub2, m = (o >> 4) & 3, o & 7
            if sub2 == 0:
                self.sync_armed = True
            elif sub2 == 1:
                if m & 1:
                    self.crc_on = True
                if m & 2:
                    self.stuff_on = True
                    self.run = 0
                    self.last = c.coder_idle
                if m & 4:
                    self.coder_on = True
                    self.tx_lvl = self.rx_prev = c.coder_idle
                    self.man_cnt = 0
            elif sub2 == 2:
                if m & 1:
                    self.crc_on = False
                if m & 2:
                    self.stuff_on = False
                if m & 4:
                    self.coder_on = False
                    self.man_cnt = 0
            else:
                self.crc = mask(c.crc_width) if c.crc_init_ones else 0
            return DONE, None

        if op == isa.OP_MOV:
            dst, mop, src = d.a, (o >> 3) & 3, o & 7
            v = {0: rotr(snap.pins, c.in_base), 1: self.x, 2: self.y, 3: 0,
                 4: self.status_word(), 5: self.isr, 6: self.osr, 7: self.crc}[src]
            if mop & 1:
                v = ~v & M32
            if mop & 2:
                v = rev32(v)
            target = None
            if dst == 0:
                self._window(eff, "out", c.out_base, c.out_count, v)
            elif dst == 1:
                self.x = v
            elif dst == 2:
                self.y = v
            elif dst == 3:
                self._window(eff, "oe", c.out_base, c.out_count, v)
            elif dst == 4:
                target = v & 31
            elif dst == 5:
                self.isr = v
                self.isr_cnt = 0
            elif dst == 6:
                self.osr = v
                self.osr_cnt = 0
            else:
                self.crc = v & mask(c.crc_width)
            return DONE, target

        if op == isa.OP_MATCH:
            src, n = d.a, isa.dec_n(d.b)
            v = {0: self.isr, 1: self.osr, 2: self.x, 3: self.crc,
                 4: rotr(snap.pins, c.in_base)}.get(src, 0)
            m = mask(n)
            if src == 0 and c.in_shift_right:  # newest bits sit at the top of a right-shifting ISR
                m = (m << (32 - n)) & M32
            self.t = int((v & m) == (self.y & m))
            return DONE, None

        # OP_SET
        dst, v = d.a, d.b
        if dst == 0:
            self._window(eff, "out", c.set_base, c.set_count, v)
        elif dst == 1:
            self.x = v
        elif dst == 2:
            self.y = v
        elif dst == 4:
            self._window(eff, "oe", c.set_base, c.set_count, v)
        return DONE, None


class PEBlock:
    """Four engines, shared instruction memory, event flags and pad crossbar."""

    def __init__(self, fifo_depth: int = 2):
        self.imem = [isa.NOP] * isa.IMEM_DEPTH
        self.flags = 0
        self.pad_out = 0           # bits indexed by pin index (8..23 used)
        self.pad_oe = 0            # bits 8..15 used
        self.owner = {i: OWNER_NONE for i in DRIVEABLE}
        self.od = 0                # bits 8..15
        self.engines = [Engine(self, i, fifo_depth) for i in range(N_ENGINES)]
        self.ui_in = 0             # external pad values for the current cycle
        self.uio_in = 0
        self._sync1 = 0
        self._sync2 = 0
        self.cycle = 0

    # ---- CPU / host interface --------------------------------------------
    def load(self, code: list[int], origin: int):
        if origin + len(code) > isa.IMEM_DEPTH:
            raise ValueError(f"program of {len(code)} words does not fit at origin {origin}")
        for i, w in enumerate(code):
            self.imem[origin + i] = w

    def set_owner(self, pins, owner: int):
        for p in pins:
            if p not in DRIVEABLE:
                raise ValueError(f"pin {p} is not driveable")
            self.owner[p] = owner

    def set_od(self, pins, on: bool = True):
        for p in pins:
            if p not in BIDIR:
                raise ValueError(f"pin {p} has no output enable")
            self.od = (self.od | 1 << p) if on else (self.od & ~(1 << p))
            if on:   # enabling open drain presets the pad to "released"
                self.pad_out |= 1 << p

    def cpu_write_pins(self, kind: str, values: dict):
        for p, b in values.items():
            if self.owner.get(p) == OWNER_CPU:
                self._pad_write(kind, p, b)

    def set_flags(self, set_mask: int = 0, clr_mask: int = 0):
        self.flags = ((self.flags & ~clr_mask) | set_mask) & 0xFF

    # ---- pad view ---------------------------------------------------------
    @property
    def uo_out(self) -> int:
        return (self.pad_out >> 16) & 0xFF

    @property
    def uio_oe(self) -> int:
        od = (self.od >> 8) & 0xFF
        oe = (self.pad_oe >> 8) & 0xFF
        out = (self.pad_out >> 8) & 0xFF
        return ((oe & ~od) | (~out & od)) & 0xFF

    @property
    def uio_out(self) -> int:
        od = (self.od >> 8) & 0xFF
        return (self.pad_out >> 8) & 0xFF & ~od

    def _pad_write(self, kind: str, p: int, b: int):
        if kind == "out" and p in DRIVEABLE:
            self.pad_out = (self.pad_out & ~(1 << p)) | (b << p)
        elif kind == "oe" and p in BIDIR:
            self.pad_oe = (self.pad_oe & ~(1 << p)) | (b << p)

    def state(self) -> dict:
        return {"cycle": self.cycle, "flags": self.flags, "pad_out": self.pad_out,
                "pad_oe": self.pad_oe, "engines": [e.state() for e in self.engines]}

    # ---- one system clock cycle ------------------------------------------
    def step(self):
        pins = (self._sync2 & 0xFFFF) | (self.pad_out & 0xFF0000)
        snap = Snapshot(pins=pins, flags=self.flags)
        eff = Effects()
        for e in self.engines:
            e.cycle(snap, eff)
        for eng, kind, p, b in eff.pin_writes:
            if self.owner.get(p) == eng:
                self._pad_write(kind, p, b)
        self.flags = ((self.flags & ~eff.flag_clr) | eff.flag_set) & 0xFF
        self._sync2 = self._sync1
        self._sync1 = (self.ui_in & 0xFF) | ((self.uio_in & 0xFF) << 8)
        self.cycle += 1
