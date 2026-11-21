#!/usr/bin/env python3
"""Firmware runtime tests.

    fw/tests/run_fw_tests.py [--tb conv|ref|ref_micro|chip] [--arch rv32imc] [-k NAME]

--tb conv/ref/ref_micro: the standalone-Ibex testbench (test/fw/tb_ibex_fw.sv),
images from `make -C fw TARGET=tb`. --tb chip: the full chip with the RP2350
loader model (test/fw/tb_chip_fw.sv), images loaded over the host SPI pins,
from `make -C fw TARGET=chip`.

Each test runs an image and checks the result line, the status block at the
top of RAM (read the way the host reads it, over the debug bridge on the
chip) and, on the chip, the pins after the firmware stops. Exit status 0
only if every test passes.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FW = ROOT / "fw"
TBS = {"conv": ROOT / "test/fw/obj/Vtb", "ref": ROOT / "test/fw/obj/Vtb_ref",
       "ref_micro": ROOT / "test/fw/obj/Vtb_ref_micro", "chip": ROOT / "test/fw/obj/Vtb_chip"}
TARGET_RAM = {"tb": 4096, "chip": 4096}

# fw_status_t word indices (fw/include/fw.h)
ST = dict(magic=0, state=1, reason=2, detail=3, mcause=4, mepc=5, mtval=6, ra=7, sp=8,
          cycles_lo=9, cycles_hi=10, spurious_irqs=11, last_spurious_id=12,
          stack_free_min=13, heartbeat=14)
STATUS_MAGIC = 0x53544657
BOOTING, RUNNING, PANIC, EXITED = 1, 2, 3, 4
PANIC_BASE = 1000
EXC, NMI, CRC, DIRTY, STACK, ASSERT = 1, 2, 3, 4, 5, 6


@dataclass
class Case:
    name: str
    image: str
    code: int                       # expected exit code (0 = PASS)
    status: dict = field(default_factory=dict)
    plusargs: list = field(default_factory=list)
    patch: str | None = None        # "flip:<offset-from-end>" | "truncate:<bytes>"
    pew: list | None = None         # exact PE-block write sequence (+pe_stub)
    pins: tuple | None = None       # chip: expected (uo_out[6:0], uio_oe) at the end
    log: list = field(default_factory=list)  # regexes that must appear in the log


# HAL on the core (apps/halpanic): init, load, configure, hand pads to engine 1,
# start, then panic. Expected writes derived by hand from pe_isa.md section 6
# and the stub shared-block offsets in fw/include/pe_regs.h.
PE = 0x10001000  # docs/soc.md
E1, SH = PE + 0x100, PE + 0x400
HALPANIC_PEW = (
    [(PE + 0x100 * n, 0) for n in range(4)]                 # pe_hal_init: engines off
    + [(SH + 0x10C, 0), (SH + 0x100, 0), (SH + 0x120, 0), (SH + 0x118, 0), (SH + 0x11C, 0)]
    + [(SH + 4 * (8 + i), w) for i, w in enumerate([0x9f50, 0xf727, 0x6001, 0x0642])]  # IMEM 8..11
    + [(E1 + 0x04, 0x00411B00),   # CLKDIV: 60 MHz / 921.6 kHz = 65 + 27/256
       (E1 + 0x08, 0x08500060),   # EXECCTRL: uart_tx image + ORIGIN 8
       (E1 + 0x0C, 0x00000003), (E1 + 0x10, 0x00880031),             # PINCTRL: OUT/SIDE base 17
       (E1 + 0x14, 0x0001E140), (E1 + 0x18, 0), (E1 + 0x1C, 0)]
    + [(SH + 0x140 + 4 * (17 - 8), 1), (SH + 0x140 + 4 * (9 - 8), 1), (SH + 0x118, 0x02)]
    + [(E1, 0x6), (E1, 0x1)]                                         # restart, then enable
    # fw_panic -> pe_safe_state, in this order:
    + [(PE + 0x100 * n, 0) for n in range(4)] + [(SH + 0x10C, 0)]
    + [(SH + 0x140 + 4 * (9 - 8), 4), (SH + 0x140 + 4 * (17 - 8), 4)]  # owners -> CPU
    + [(SH + 0x120, 0), (SH + 0x118, 0), (SH + 0x11C, 0)]             # OE off, then OD off, then out
)


def cases(target: str) -> list[Case]:
    return tb_cases() if target == "tb" else chip_cases()


def chip_cases() -> list[Case]:
    """On the full chip. Interrupt and NMI cases need the standalone bench's
    interrupt lines and are not run here; everything else is."""
    p = lambda r: PANIC_BASE + r
    released = (0x00, 0x00)
    return [
        Case("chiptest", "chiptest", 0, dict(state=EXITED, reason=0), pins=released),
        Case("chiptest_rerun", "chiptest", 0, dict(state=EXITED), ["+rerun"], pins=released),
        Case("chiptest_slow_sck", "chiptest", 0, dict(state=EXITED), ["+sck_half=11"]),
        Case("pin_panic", "crashtest-pinpanic", p(16), dict(state=PANIC, reason=16, detail=0x9100),
             pins=released),
        Case("illegal", "crashtest-illegal", p(EXC), dict(state=PANIC, reason=EXC, mcause=2)),
        Case("loadfault", "crashtest-loadfault", p(EXC),
             dict(state=PANIC, reason=EXC, mcause=5, mtval=0x30000000)),
        Case("fetchfault", "crashtest-fetchfault", p(EXC),
             dict(state=PANIC, reason=EXC, mcause=1, mepc=0x30000000)),
        Case("ecall", "crashtest-ecall", p(EXC), dict(state=PANIC, reason=EXC, mcause=11)),
        Case("assert", "crashtest-assert", p(ASSERT), dict(state=PANIC, reason=ASSERT)),
        Case("exit_code", "crashtest-exitcode", 42, dict(state=EXITED, reason=42)),
        # Boot guard (docs/soc.md): a restart with no SRAM write since the last
        # start is refused; the CPU stays in reset where it stopped.
        Case("restart_refused", "crashtest-spin", -3, dict(state=RUNNING),
             ["+restart_at=150000", "+max_cycles=400000"],
             log=[r"RESTART run_refused=1 cpu_run=0"]),
        Case("restart_refused_during_boot", "crashtest-spin", -3, dict(state=BOOTING),
             ["+restart_at=10000", "+max_cycles=300000"],
             log=[r"RESTART run_refused=1 cpu_run=0"]),
        # One SRAM word written satisfies the guard without a reload: the
        # firmware's own check must then refuse the stale image.
        Case("restart_without_reload", "crashtest-spin", p(DIRTY),
             dict(state=PANIC, reason=DIRTY), ["+restart_at=150000", "+restart_poke"],
             log=[r"RESTART run_refused=0 cpu_run=1"]),
        # Same during boot: the image is still fresh (boot checks had not
        # finished), so the second boot runs normally.
        Case("restart_during_boot", "crashtest-spin", -3, dict(state=RUNNING),
             ["+restart_at=10000", "+restart_poke", "+max_cycles=700000"],
             log=[r"RESTART run_refused=0 cpu_run=1"]),
        Case("corrupt_image", "chiptest", p(CRC), dict(state=PANIC, reason=CRC), patch="flip:40"),
        Case("truncated_load", "chiptest", p(CRC), dict(state=PANIC, reason=CRC),
             patch="truncate:1024"),
        Case("bridge_while_running", "bridgetest", 0, dict(state=EXITED, reason=0),
             ["+bridge_tests"]),
        # Access just past the end of the SRAM: a load access fault
        # (docs/soc.md "Bus errors"; nothing aliases).
        Case("beyond_ram", "crashtest-beyondram", p(EXC),
             dict(state=PANIC, reason=EXC, mcause=5, mtval=4096 + 0x84)),
    ]


def tb_cases() -> list[Case]:
    bus = [("", []), ("_stall", ["+stall=30", "+seed=5"]),
           ("_1port", ["+single_port", "+stall=20", "+seed=9"])]
    out = [Case(f"selftest{sfx}", "selftest", 0,
                dict(magic=STATUS_MAGIC, state=EXITED, reason=0, spurious_irqs=1,
                     last_spurious_id=16 + 12, heartbeat=1), pa) for sfx, pa in bus]
    p = lambda r: PANIC_BASE + r
    out += [
        Case("illegal", "crashtest-illegal", p(EXC),
             dict(state=PANIC, reason=EXC, mcause=2, mtval=0)),
        Case("loadfault", "crashtest-loadfault", p(EXC),
             dict(state=PANIC, reason=EXC, mcause=5, mtval=0x30000000), ["+allow_bus_err"]),
        Case("fetchfault", "crashtest-fetchfault", p(EXC),
             dict(state=PANIC, reason=EXC, mcause=1, mepc=0x30000000), ["+allow_bus_err"]),
        Case("ecall", "crashtest-ecall", p(EXC), dict(state=PANIC, reason=EXC, mcause=11)),
        Case("nmi", "crashtest-nmi", p(NMI), dict(state=PANIC, reason=NMI, mcause=0x8000001F)),
        Case("stack_overflow", "crashtest-stackovf", p(STACK), dict(state=PANIC, reason=STACK)),
        Case("assert", "crashtest-assert", p(ASSERT), dict(state=PANIC, reason=ASSERT)),
        Case("exit_code", "crashtest-exitcode", 42, dict(state=EXITED, reason=42)),
        Case("dirty_restart", "crashtest-spin", p(DIRTY),
             dict(state=PANIC, reason=DIRTY), ["+reset_at=100000"]),
        Case("reset_during_boot", "crashtest-spin", -3, dict(state=RUNNING),
             ["+reset_at=20000", "+max_cycles=200000"]),
        Case("hal_safe_state", "halpanic", p(16), dict(state=PANIC, reason=16, detail=0x5AFE),
             ["+pe_stub"], pew=HALPANIC_PEW),
        Case("corrupt_image", "selftest", p(CRC), dict(state=PANIC, reason=CRC), patch="flip:40"),
        Case("truncated_load", "selftest", p(CRC), dict(state=PANIC, reason=CRC),
             patch="truncate:1024"),
    ]
    return out


def load_hex(path: Path) -> bytearray:
    data = bytearray()
    for line in path.read_text().split("\n"):
        if line.startswith("@") or not line.strip():
            continue
        data += bytes(int(b, 16) for b in line.split())
    return data


def write_hex(data: bytes, path: Path) -> None:
    lines = ["@00000000"] + [" ".join(f"{b:02X}" for b in data[i:i + 16])
                             for i in range(0, len(data), 16)]
    path.write_text("\n".join(lines) + "\n")


def run(case: Case, args, tmp: Path) -> tuple[bool, str]:
    img = FW / "build" / f"{args.arch}_{args.target}" / f"{case.image}.hex"
    if not img.exists():
        return False, f"missing {img} (make -C fw ARCH={args.arch} TARGET={args.target})"
    img_len = len(load_hex(img))
    if case.patch:
        kind, n = case.patch.split(":")
        data = load_hex(img)
        if kind == "flip":
            data[len(data) - int(n)] ^= 0x10
        else:
            data = data[:int(n)]
        img = tmp / f"{case.name}.hex"
        write_hex(data, img)
        img_len = len(data)
    status_addr = args.ram - 64
    # case plusargs first: the testbench takes the first match of each name
    cmd = [str(TBS[args.tb])] + case.plusargs + [
        f"+img={img}", f"+img_len={img_len}", f"+ram_size={args.ram}", f"+ram={args.ram}",
        f"+dump={status_addr:x}",
        "+max_cycles=1000000", "+verilator+rand+reset+2", f"+verilator+seed+{args.seed}"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    log = p.stdout + p.stderr
    (tmp / f"{case.name}.log").write_text(log)
    res = [l for l in log.splitlines() if l.startswith(("PASS", "FAIL"))]
    if not res:
        return False, "no result line"
    m = re.match(r"FAIL code=(-?\d+)", res[-1])
    code = 0 if res[-1].startswith("PASS") else int(m.group(1)) if m else -99
    errs = []
    if code != case.code:
        errs.append(f"exit code {code}, expected {case.code}")
    mem = [int(v, 16) for v in re.findall(r"MEM 0x[0-9a-f]+ (0x[0-9a-f]+)", log)]
    if case.pew is not None:
        got = [tuple(int(x, 16) for x in m) for m in
               re.findall(r"PEW (0x[0-9a-f]+) (0x[0-9a-f]+)", log)]
        if got != case.pew:
            first = next((i for i, (a, b) in enumerate(zip(got, case.pew)) if a != b),
                         min(len(got), len(case.pew)))
            errs.append(f"PE write #{first}: got "
                        f"{[f'{a:08x}={v:x}' for a, v in got[first:first + 2]]} expected "
                        f"{[f'{a:08x}={v:x}' for a, v in case.pew[first:first + 2]]}")
    if case.pins is not None:
        m = re.search(r"PINS uo_out=0x([0-9a-f]+) uio_oe=0x([0-9a-f]+)", log)
        got = (int(m.group(1), 16) & 0x7F, int(m.group(2), 16)) if m else None
        if got != case.pins:
            errs.append(f"pins (uo_out[6:0], uio_oe) = {got}, expected {case.pins}")
    for pat in case.log:
        if not re.search(pat, log):
            errs.append(f"log has no line matching {pat!r}")
    exp = {"magic": STATUS_MAGIC, **case.status}
    for k, v in exp.items():
        got = mem[ST[k]] if len(mem) > ST[k] else None
        if got != v:
            errs.append(f"status.{k}=0x{got:x} expected 0x{v:x}" if got is not None
                        else f"status.{k} missing")
    return not errs, "; ".join(errs) or res[-1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tb", choices=sorted(TBS), default="conv")
    ap.add_argument("--arch", default="rv32imc")
    ap.add_argument("-k", default="", help="only tests whose name contains this")
    ap.add_argument("--seed", type=int, default=7, help="random power-up state seed")
    args = ap.parse_args()
    args.target = "chip" if args.tb == "chip" else "tb"
    args.ram = TARGET_RAM[args.target]
    if not TBS[args.tb].exists():
        raise SystemExit(f"{TBS[args.tb]} missing: run test/fw/build_tb.sh {args.tb}")
    sel = [c for c in cases(args.target) if args.k in c.name]
    out_dir = FW / "build" / "tests" / f"{args.tb}_{args.arch}_s{args.seed}"
    out_dir.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(os.cpu_count() or 4) as ex:
        results = list(ex.map(lambda c: (c, *run(c, args, out_dir)), sel))
    bad = 0
    for c, ok, msg in results:
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL':5s} {c.name:18s} {msg}")
    print(f"{len(sel) - bad}/{len(sel)} passed (tb={args.tb} arch={args.arch} RAM {args.ram} "
          f"seed {args.seed}); "
          f"logs in {out_dir.relative_to(ROOT)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
