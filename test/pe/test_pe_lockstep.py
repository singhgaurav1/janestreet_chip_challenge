# SPDX-License-Identifier: Apache-2.0
"""Lockstep: pe_block RTL vs the peemu golden model, cycle by cycle.

Each cycle, the same pad inputs and at most one CPU action are applied to
both. The RTL's CPU action lands at the clock edge that ends the cycle; the
model applies it right after its step() for that cycle, which is the same
point ("between cycles", docs/pe_isa.md section 4). After every edge, the
full architectural state of all four engines, the event flags and the pads
are compared. Stimulus: seeded random legal programs and configs
(peemu.randprog), random pad ownership, inputs and CPU traffic.

Environment: SEEDS=a:b selects the seed range (default 0:40), CYCLES per seed.
"""
import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from peemu import isa                                    # noqa: E402
from peemu.config import CONFIG_REGS, REG_OFFSETS        # noqa: E402
from peemu.model import PEBlock                          # noqa: E402
FIFO_DEPTH = 2                                           # must match src/pe_engine.v
from peemu.randprog import random_engine, random_instr   # noqa: E402

M32 = 0xFFFFFFFF
STATE_FIELDS = ["pc", "x", "y", "isr", "osr", "isr_cnt", "osr_cnt", "t", "serr", "delay",
                "crc", "div_cnt"]


COVERAGE: dict = {}      # (opcode, variant, result) -> count, over all seeds


def cover_key(d, res):
    """Coverage bin for an issued instruction: opcode, its main variant field, result."""
    o = d.opnd
    variant = {0: d.a, 1: (o >> 5) & 3, 2: d.a, 3: d.a, 4: (o >> 6, (o >> 4) & 3),
               5: (d.a, o & 7), 6: d.a, 7: d.a}[d.op]
    return (d.op, variant, res)


class Rig:
    def __init__(self, dut):
        self.dut = dut
        self.m = PEBlock()
        for e in self.m.engines:                      # record what actually executes
            orig = e._execute

            def wrapped(d, snap, eff, orig=orig):
                res, tgt = orig(d, snap, eff)
                COVERAGE[cover_key(d, res)] = COVERAGE.get(cover_key(d, res), 0) + 1
                return res, tgt
            e._execute = wrapped
        self.cycle = 0
        self.trace = []               # recent (cycle, op, per-engine pc/instr) for diagnostics

    # ---- CPU access: one per cycle --------------------------------------
    def drive(self, op):
        d = self.dut
        if op is None:
            d.en.value = 0
            d.we.value = 0
            return
        we, addr, data = op
        d.en.value = 1
        d.we.value = int(we)
        d.addr.value = addr
        d.wdata.value = data & M32

    def eng(self, n):
        return getattr(self.dut, "gen_eng")[n].u_eng

    async def step(self, op=None, model_action=None, ui=0, uio=0):
        """One cycle: inputs + optional CPU op on both sides, then compare."""
        d = self.dut
        d.ui_in.value = ui
        d.uio_in.value = uio
        self.drive(op)
        self.m.ui_in, self.m.uio_in = ui, uio
        await RisingEdge(d.clk)
        pre = [(e.pc, e.delay, isa.disasm(self.m.imem[(e.cfg.origin + e.pc) % 64],
                                          isa.SideCfg(e.cfg.side_count, bool(e.cfg.side_opt),
                                                      bool(e.cfg.side_pindirs))),
                e.force_instr) for e in self.m.engines]
        # A CPU read observes the state at the start of the cycle; its side
        # effect (an RXF pop) lands at the end, after the engines' own update.
        is_read = op is not None and not op[0]
        exp_err = op is not None and not mapped(op[1])
        expected_read, post = (model_action() if (is_read and model_action) else (None, None))
        self.m.step()
        self.trace = (self.trace + [(self.cycle, op, pre)])[-12:]
        if is_read:
            if post:
                post()
        elif model_action:
            model_action()
        await ReadOnly()
        self.compare()
        if op is not None:
            got_err = int(d.err.value)
            assert got_err == int(exp_err), \
                f"cycle {self.cycle}: err {got_err} for access to 0x{op[1]:03x}, expected {int(exp_err)}"
        if is_read:
            got = int(d.rdata.value)
            assert got == expected_read, \
                f"cycle {self.cycle}: CPU read 0x{got:08x}, model 0x{expected_read:08x}"
        self.cycle += 1
        await Timer(1, unit="ns")

    def compare(self):
        try:
            self._compare()
        except AssertionError:
            for c, op, pre in self.trace:
                self.dut._log.error(f"  cycle {c} op={op} " + " | ".join(
                    f"e{n}: pc={pc} dly={dl} {txt}{' F' + hex(fi) if fi is not None else ''}"
                    for n, (pc, dl, txt, fi) in enumerate(pre)))
            for n, e in enumerate(self.m.engines):
                self.dut._log.error(f"  engine {n} cfg {e.cfg}")
            raise

    def _compare(self):
        d, m = self.dut, self.m
        for n in range(4):
            r, e = self.eng(n), m.engines[n]
            for f in STATE_FIELDS:
                got = int(getattr(r, f).value)
                exp = getattr(e, f)
                assert got == exp, f"cycle {self.cycle} engine {n} {f}: RTL 0x{got:x} model 0x{exp:x}"
            for name, q in (("u_txf", e.txf), ("u_rxf", e.rxf)):
                fifo = getattr(r, name)
                cnt = int(fifo.cnt.value)
                assert cnt == len(q), f"cycle {self.cycle} engine {n} {name} level {cnt} vs {len(q)}"
                rdp = int(fifo.rd.value)
                got = [int(fifo.mem[(rdp + k) % FIFO_DEPTH].value) for k in range(cnt)]
                assert got == list(q), f"cycle {self.cycle} engine {n} {name} {got} vs {list(q)}"
        assert int(d.flags_out.value) == m.flags, f"cycle {self.cycle} flags"
        assert int(d.uo_out.value) == m.uo_out, \
            f"cycle {self.cycle} uo_out RTL {int(d.uo_out.value):02x} model {m.uo_out:02x}"
        assert int(d.uio_out.value) == m.uio_out, f"cycle {self.cycle} uio_out"
        assert int(d.uio_oe.value) == m.uio_oe, (f"cycle {self.cycle} uio_oe RTL {int(d.uio_oe.value):02x} "
                                                 f"model {m.uio_oe:02x} od {m.od >> 8:02x} out {m.pad_out >> 8 & 0xFF:02x} "
                                                 f"oe {m.pad_oe >> 8:02x} own {[m.owner[p] for p in range(8, 16)]}")
        assert int(d.irq.value) == m.irq, \
            f"cycle {self.cycle} irq RTL {int(d.irq.value):03x} model {m.irq:03x}"


def reg(n, name):
    return 0x100 * n + REG_OFFSETS[name]


# shared registers (docs/pe_isa.md section 6)
EVT, EVT_SET, EVT_CLR, IRQ_EN = 0x500, 0x504, 0x508, 0x50C
ENGINE_CTRL, SAFE_STATE, SAFE_KEY = 0x510, 0x514, 0x5AFE
PAD_OD, GPIO_OUT, GPIO_OE, PAD_STATE = 0x518, 0x51C, 0x520, 0x524
GPIO_OUT_SET, GPIO_OUT_CLR, GPIO_OE_SET, GPIO_OE_CLR = 0x528, 0x52C, 0x530, 0x534
PAD_OD_SET, PAD_OD_CLR = 0x538, 0x53C


def pad_owner_reg(p):
    return 0x540 + 4 * (p - 8)


def mapped(a):
    """Offsets the block answers without err."""
    if a < 0x400:
        return (a & 0xFF) <= 0x2C
    if 0x400 <= a < 0x500:
        return True
    return a in (EVT, EVT_SET, EVT_CLR, IRQ_EN, ENGINE_CTRL, SAFE_STATE) or 0x518 <= a <= 0x57C


def cpu_pads(m, kind, new):
    """GPIO write of a full value: lands on CPU-owned pads only (model view)."""
    width = 16 if kind == "out" else 8
    m.cpu_write_pins(kind, {8 + i: new >> i & 1 for i in range(width)})


def od_write(m, v):
    m.set_od([8 + i for i in range(8) if v >> i & 1], True)
    m.set_od([8 + i for i in range(8) if not v >> i & 1], False)


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.en.value = 0
    dut.we.value = 0
    dut.addr.value = 0
    dut.wdata.value = 0
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await Timer(1, unit="ns")
    return Rig(dut)


async def run_seed(dut, seed, cycles, serial=False):
    rig = await setup(dut)
    rnd = random.Random(seed)
    m = rig.m

    # instruction memory: every word written on both sides
    code = [isa.NOP] * isa.IMEM_DEPTH
    cfgs = []
    for n in range(4):
        prog, cfg = random_engine(rnd, length=rnd.randint(2, 16), origin=16 * n, serial=serial)
        code[16 * n:16 * n + len(prog)] = prog
        cfgs.append(cfg)
    for i, w in enumerate(code):
        await rig.step((1, 0x400 + 4 * i, w),
                       lambda i=i, w=w: m.imem.__setitem__(i, w))

    # configuration
    for n, cfg in enumerate(cfgs):
        regs = cfg.to_regs()
        for name in CONFIG_REGS:
            await rig.step((1, reg(n, name), regs[name]),
                           lambda n=n, cfg=cfg: m.engines[n].configure(cfg))

    # readback: stored config bits (packed per docs/pe_isa.md 6) and imem
    for n, cfg in enumerate(cfgs):
        regs = cfg.to_regs()
        for name in CONFIG_REGS:
            await rig.step((0, reg(n, name), 0), lambda v=regs[name]: (v, None))
    for i in rnd.sample(range(isa.IMEM_DEPTH), 8):
        await rig.step((0, 0x400 + 4 * i, 0), lambda w=code[i]: (w, None))

    # pads: random owners (one register per pad) and open drain
    for p in range(8, 24):
        o = rnd.choice([0, 1, 2, 3, 4, 6, 7])
        await rig.step((1, pad_owner_reg(p), o), lambda p=p, o=o: m.owner.__setitem__(p, o))
    for p in rnd.sample(range(8, 24), 4):
        await rig.step((0, pad_owner_reg(p), 0), lambda p=p: (m.owner[p], None))
    od = rnd.getrandbits(8)
    await rig.step((1, PAD_OD, od), lambda: od_write(m, od))
    irq_en = rnd.getrandbits(16)
    await rig.step((1, IRQ_EN, irq_en), lambda: setattr(m, "irq_en", irq_en))
    await rig.step((0, IRQ_EN, 0), lambda: (m.irq_en, None))

    # enable all engines
    def enable(n):
        def act():
            m.engines[n].enabled = True
        return act
    for n in range(4):
        await rig.step((1, reg(n, "CTRL"), 1), enable(n))

    # run with random traffic
    ui, uio = 0, 0
    for _ in range(cycles):
        if serial:   # slowly changing inputs give runs of equal bits
            if rnd.random() < 0.1:
                ui ^= 1 << rnd.randrange(8)
            if rnd.random() < 0.1:
                uio ^= 1 << rnd.randrange(8)
        else:
            ui, uio = rnd.getrandbits(8), rnd.getrandbits(8)
        r = rnd.random()
        n = rnd.randrange(4)
        e = m.engines[n]
        if r < (0.4 if serial else 0.15):
            v = rnd.choice([0, M32, rnd.getrandbits(32)]) if serial else rnd.getrandbits(32)
            await rig.step((1, reg(n, "TXF"), v), lambda e=e, v=v: (e.tx_push(v), None)[1], ui, uio)
        elif r < 0.30:
            await rig.step((0, reg(n, "RXF"), 0), lambda e=e: rx_read(e), ui, uio)
        elif r < 0.33:
            w = random_instr(rnd, 16, isa.SideCfg(e.cfg.side_count, bool(e.cfg.side_opt),
                                                  bool(e.cfg.side_pindirs)))
            await rig.step((1, reg(n, "INSTR"), w), lambda e=e, w=w: e.force(w), ui, uio)
        elif r < 0.36:
            sm, cm = rnd.getrandbits(8), rnd.getrandbits(8)
            if rnd.random() < 0.5:
                await rig.step((1, 0x504, sm), lambda sm=sm: m.set_flags(sm, 0), ui, uio)
            else:
                await rig.step((1, 0x508, cm), lambda cm=cm: m.set_flags(0, cm), ui, uio)
        elif r < 0.37:
            await rig.step((1, reg(n, "CTRL"), 0b111),
                           lambda e=e: (e.restart(), e.div_restart(), setattr(e, "enabled", True)), ui, uio)
        elif r < 0.38:
            await rig.step((1, reg(n, "CTRL"), 0), lambda e=e: setattr(e, "enabled", False), ui, uio)
        elif r < 0.39:
            await rig.step((1, reg(n, "CTRL"), 1), lambda e=e: setattr(e, "enabled", True), ui, uio)
        elif r < 0.41:
            await rig.step((0, reg(n, "STATUS"), 0), lambda e=e: (e.status(), None), ui, uio)
        elif r < 0.42:
            w = rnd.getrandbits(32)
            await rig.step((1, reg(n, "STATUS"), w), lambda e=e, w=w: e.clear_errors(w), ui, uio)
        elif r < 0.43:
            await rig.step((0, reg(n, "INSTR"), 0),
                           lambda e=e: (m.imem[(e.cfg.origin + e.pc) % 64], None), ui, uio)
        elif r < 0.44:
            w = rnd.getrandbits(12)
            await rig.step((1, ENGINE_CTRL, w), lambda w=w: m.engine_ctrl(w), ui, uio)
        elif r < 0.445:
            await rig.step((0, ENGINE_CTRL, 0),
                           lambda: (sum(int(x.enabled) << k for k, x in enumerate(m.engines)), None),
                           ui, uio)
        elif r < 0.45:
            p, o = rnd.randrange(8, 24), rnd.choice([0, 1, 2, 3, 4, 5, 6, 7])
            await rig.step((1, pad_owner_reg(p), o), lambda p=p, o=o: m.owner.__setitem__(p, o),
                           ui, uio)
        elif r < 0.47:
            a, v = rnd.choice([(GPIO_OUT, 16), (GPIO_OUT_SET, 16), (GPIO_OUT_CLR, 16),
                               (GPIO_OE, 8), (GPIO_OE_SET, 8), (GPIO_OE_CLR, 8)])
            w = rnd.getrandbits(v)
            kind = "out" if a in (GPIO_OUT, GPIO_OUT_SET, GPIO_OUT_CLR) else "oe"

            def gpio(a=a, w=w, kind=kind):
                cur = (m.pad_out >> 8 & 0xFFFF) if kind == "out" else (m.pad_oe >> 8 & 0xFF)
                new = {GPIO_OUT: w, GPIO_OE: w, GPIO_OUT_SET: cur | w, GPIO_OE_SET: cur | w,
                       GPIO_OUT_CLR: cur & ~w, GPIO_OE_CLR: cur & ~w}[a]
                cpu_pads(m, kind, new)
            await rig.step((1, a, w), gpio, ui, uio)
        elif r < 0.475:
            a, w = rnd.choice([PAD_OD, PAD_OD_SET, PAD_OD_CLR]), rnd.getrandbits(8)

            def odw(a=a, w=w):
                cur = m.od >> 8 & 0xFF
                od_write(m, {PAD_OD: w, PAD_OD_SET: cur | w, PAD_OD_CLR: cur & ~w}[a])
            await rig.step((1, a, w), odw, ui, uio)
        elif r < 0.48:
            a = rnd.choice([GPIO_OUT, GPIO_OE, PAD_OD, PAD_STATE, EVT, IRQ_EN])
            val = {GPIO_OUT: lambda: m.pad_out >> 8 & 0xFFFF, GPIO_OE: lambda: m.pad_oe >> 8 & 0xFF,
                   PAD_OD: lambda: m.od >> 8 & 0xFF, EVT: lambda: m.flags,
                   IRQ_EN: lambda: m.irq_en,
                   PAD_STATE: lambda: (m.pad_oe >> 8 & 0xFF) << 16 | (m.pad_out >> 8 & 0xFFFF)}[a]
            await rig.step((0, a, 0), lambda val=val: (val(), None), ui, uio)
        elif r < 0.483:
            w = rnd.getrandbits(16)
            await rig.step((1, IRQ_EN, w), lambda w=w: setattr(m, "irq_en", w), ui, uio)
        elif r < 0.484:
            key = SAFE_KEY if rnd.random() < 0.7 else rnd.getrandbits(16)
            await rig.step((1, SAFE_STATE, key),
                           (lambda: m.safe_state()) if key == SAFE_KEY else None, ui, uio)
        elif r < 0.49:
            a = rnd.choice([0x30, 0x0FC, 0x2F0, 0x50, 0x580, 0x600, 0xFFC, 0x7A0])
            if a < 0x400:
                a = 0x100 * n + (a & 0xFF)
            we = rnd.random() < 0.5
            await rig.step((int(we), a, rnd.getrandbits(32)),
                           None if we else (lambda: (0, None)), ui, uio)
        else:
            await rig.step(None, None, ui, uio)
    dut._log.info(f"seed {seed}: {rig.cycle} cycles in lockstep")


def rx_read(e):
    """RXF read: value = head at the start of the cycle (0 if empty); pop after.
    An empty read sets RXUNF (at the end of the cycle)."""
    if not e.rxf:     # a word the engine pushes in this same cycle is not popped
        return 0, lambda: setattr(e, "rxunf", 1)
    return e.rxf[0], e.rx_pop


def seed_range():
    a, _, b = os.environ.get("SEEDS", "0:40").partition(":")
    return range(int(a), int(b))


@cocotb.test()
async def lockstep_random(dut):
    cycles = int(os.environ.get("CYCLES", "400"))
    for seed in seed_range():
        await run_seed(dut, seed, cycles)
    report_coverage(dut)


@cocotb.test()
async def lockstep_serial(dut):
    """Serial-assist-biased programs: stuffing, NRZI/Manchester coding, CRC."""
    COVERAGE.clear()
    cycles = int(os.environ.get("CYCLES", "400"))
    for seed in seed_range():
        await run_seed(dut, 100000 + seed, cycles, serial=True)
    report_coverage(dut)


# legal (opcode, variant) bins: what the ISA defines (docs/pe_isa.md section 3)
LEGAL = ([(0, c) for c in range(8)] + [(1, s) for s in range(4)] +
         [(2, s) for s in (0, 1, 2, 3, 4, 6, 7)] + [(3, d) for d in range(8)] +
         [(4, (0, b)) for b in range(4)] + [(4, (1, b)) for b in range(4)] +
         [(4, (2, k)) for k in range(3)] + [(4, (3, k)) for k in range(4)] +
         [(5, (dd, ss)) for dd in range(8) for ss in range(8)] +
         [(6, s) for s in range(5)] + [(7, d) for d in (0, 1, 2, 4)])


def report_coverage(dut):
    hit = {(op, v) for (op, v, _r) in COVERAGE}
    missing = [b for b in LEGAL if b not in hit]
    results = {r for (_o, _v, r) in COVERAGE}
    dut._log.info(f"coverage: {len(LEGAL) - len(missing)}/{len(LEGAL)} opcode-variant bins, "
                  f"results seen {sorted(results)}, {sum(COVERAGE.values())} instructions issued")
    if missing:
        dut._log.info(f"missing bins: {missing}")
    stalls = sorted({(o, v) for (o, v, r) in COVERAGE if r == 'stall'})
    reexec = sorted({(o, v) for (o, v, r) in COVERAGE if r == 'reexec'})
    dut._log.info(f"stalling bins: {stalls}")
    dut._log.info(f"re-executing bins (stuffing): {reexec}")
