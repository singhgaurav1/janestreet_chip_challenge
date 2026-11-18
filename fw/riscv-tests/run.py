#!/usr/bin/env python3
"""Build and run upstream riscv-tests on Ibex in the firmware testbench.

    fw/riscv-tests/run.py [--suite rv32ui,rv32um,rv32uc,rv32mi] [--stall PCT]
                          [--single-port] [--seed N] [-j N] [TEST ...]

Needs: deps/riscv-tests (fw/scripts/fetch_riscv_tests.sh), the testbench
(test/fw/build_tb.sh) and riscv64-unknown-elf-gcc. Exit status 0 only if every
selected test passes. Results go to fw/build/riscv-tests/results.<tb>.txt.

Tests listed in EXPECTED_FAIL are run too; each needs a written reason (they
probe features our Ibex configuration does not have). If one unexpectedly
passes, or fails differently, the run fails so the list stays honest.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RT = ROOT / "deps" / "riscv-tests"
ENV = Path(__file__).resolve().parent / "env"
TBS = {"conv": ROOT / "test" / "fw" / "obj" / "Vtb",      # src/ibex_small.v
       "ref": ROOT / "test" / "fw" / "obj" / "Vtb_ref"}  # upstream SV (differential)
OUT = ROOT / "fw" / "build" / "riscv-tests"
CC = os.environ.get("RISCV_CC", "riscv64-unknown-elf-gcc")
OBJCOPY = os.environ.get("RISCV_OBJCOPY", "riscv64-unknown-elf-objcopy")

# -march per suite: base-ISA tests are built without C so every 32-bit
# encoding is exercised; rvc tests need C.
MARCH = {"rv32ui": "rv32im_zicsr_zifencei", "rv32um": "rv32im_zicsr_zifencei",
         "rv32uc": "rv32imc_zicsr_zifencei", "rv32mi": "rv32im_zicsr_zifencei"}

# Tests that probe features this Ibex configuration does not implement.
# test name -> reason. Every entry must be justified from the Ibex docs.
EXPECTED_FAIL: dict[str, str] = {
    "rv32mi-pmpaddr": "needs PMP; our Ibex config has PMPEnable=0 (Ibex docs, integration parameters)",
    "rv32mi-breakpoint": "points mtvec at a 4-byte aligned label to catch the optional tcontrol "
                         "access; Ibex mtvec is vectored with a 256-byte aligned base, so the trap "
                         "lands elsewhere. Triggers are also off (DbgTriggerEn=0)",
}


def suite_tests(suite: str) -> list[str]:
    frag = (RT / "isa" / suite / "Makefrag").read_text()
    m = re.search(rf"{suite}_sc_tests\s*=\s*\\?(.*?)\n\s*\n", frag, re.S)
    if not m:
        raise SystemExit(f"cannot parse {suite}/Makefrag")
    return [t for t in m.group(1).replace("\\", " ").split() if t]


def build(suite: str, test: str) -> Path:
    src = RT / "isa" / suite / f"{test}.S"
    out = OUT / f"{suite}-{test}"  # shared by both testbenches (same image)
    elf, hexf = out.with_suffix(".elf"), out.with_suffix(".hex")
    subprocess.run([CC, f"-march={MARCH[suite]}", "-mabi=ilp32", "-static", "-mcmodel=medany",
                    "-fvisibility=hidden", "-nostdlib", "-nostartfiles",
                    "-I", str(ENV), "-I", str(RT / "env"), "-I", str(RT / "isa" / "macros" / "scalar"),
                    "-T", str(ENV / "link.ld"), str(src), "-o", str(elf)], check=True)
    subprocess.run([OBJCOPY, "-O", "verilog", "-j", ".text.init", "-j", ".text", "-j", ".data",
                    str(elf), str(hexf)], check=True)
    return hexf


def run(suite: str, test: str, args) -> tuple[str, str, str]:
    name = f"{suite}-{test}"
    try:
        hexf = build(suite, test)
    except subprocess.CalledProcessError as e:
        return name, "BUILD_ERROR", str(e)
    cmd = [str(TBS[args.tb]), f"+img={hexf}", f"+stall={args.stall}", f"+seed={args.seed}",
           "+max_cycles=200000",
           # randomise all uninitialised state (built with --x-initial unique)
           "+verilator+rand+reset+2", f"+verilator+seed+{args.seed}"] + (["+single_port"] if args.single_port else [])
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    (OUT / f"{name}.{args.tb}.log").write_text(p.stdout + p.stderr)
    res = [ln for ln in p.stdout.splitlines() if ln.startswith(("PASS", "FAIL"))]
    if not res:
        return name, "NO_RESULT", p.stdout[-300:]
    return name, res[-1].split()[0], res[-1]


def run_suite(jobs, args) -> tuple[int, list[tuple[str, str, str]]]:
    with cf.ThreadPoolExecutor(args.j) as ex:
        results = sorted(ex.map(lambda j: run(*j, args), jobs))
    bad, lines = 0, []
    for name, status, detail in results:
        exp = EXPECTED_FAIL.get(name)
        if exp:
            ok = status == "FAIL"
            tag = "XFAIL" if ok else "XPASS?"
        else:
            ok = status == "PASS"
            tag = status
        bad += not ok
        lines.append(f"{tag:12s} {name:28s} {detail}" + (f"  [expected: {exp}]" if exp else ""))
    summary = (f"{len(results) - bad}/{len(results)} as expected "
               f"(tb={args.tb} stall={args.stall}% seed={args.seed} single_port={int(args.single_port)})")
    (OUT / f"results.{args.tb}.txt").write_text("\n".join(lines + [summary]) + "\n")
    print("\n".join(lines))
    print(summary)
    return bad, results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="rv32ui,rv32um,rv32uc,rv32mi")
    ap.add_argument("--tb", choices=sorted(TBS) + ["both"], default="conv",
                    help="conv: converted src/ibex_small.v; ref: upstream SystemVerilog; "
                         "both: run both and require identical results and cycle counts")
    ap.add_argument("--stall", type=int, default=0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--single-port", action="store_true")
    ap.add_argument("-j", type=int, default=os.cpu_count() or 4)
    ap.add_argument("tests", nargs="*", help="suite-test names, e.g. rv32ui-add")
    args = ap.parse_args()
    tbs = sorted(TBS) if args.tb == "both" else [args.tb]
    for t in tbs:
        if not TBS[t].exists():
            raise SystemExit(f"{TBS[t]} missing: run test/fw/build_tb.sh {t}")
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(s, t) for s in args.suite.split(",") for t in suite_tests(s)]
    if args.tests:
        jobs = [j for j in jobs if f"{j[0]}-{j[1]}" in args.tests]
    bad, outs = 0, {}
    for t in tbs:
        args.tb = t
        b, outs[t] = run_suite(jobs, args)
        bad += b
    if len(tbs) == 2:
        diff = [(c[0], c[2], r[2]) for c, r in zip(outs["conv"], outs["ref"]) if c[2] != r[2]]
        for name, c, r in diff:
            print(f"MISMATCH {name}: conv '{c}' vs ref '{r}'")
        print(f"conv vs ref: {len(jobs) - len(diff)}/{len(jobs)} identical (result and cycle count)")
        bad += len(diff)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
