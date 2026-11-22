#!/usr/bin/env python3
"""Static worst-case stack check for a firmware image.

    stack_check.py <elf> <objdir> [--objdump ...] [--nm ...]

Frame sizes come from GCC's -fstack-usage (*.su in <objdir>), the call graph
from the disassembly. Worst case for the main stack:

    deepest path from _start  +  interrupt entry frame  +  fw_irq_dispatch
    +  the deepest function an indirect call could reach (interrupt handlers)

Interrupts do not nest on Ibex, so one interrupt frame is enough. The fatal
trap handlers run on the separate panic stack, checked on its own.

Fails (exit 1) if either worst case exceeds the space the linker left, or if
a function has a dynamic stack frame or takes part in recursion: in a 4 KiB
RAM the stack must be bounded by construction.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Hand-written assembly: frame sizes (bytes). start.S's _trap_irq frame is
# 64 bytes for RV32I, 48 for RV32E; the larger is used.
ASM_FRAMES = {"_start": 0, "_trap_irq": 64, "_trap_exception": 0, "_trap_nmi": 0,
              "_irq_stack_overflow": 0, "_vectors": 0, "_reset": 0}
# libgcc arithmetic helpers on RISC-V are leaf assembly that use no stack.
LEAF_LIBGCC = re.compile(r"^(__hidden_)?__(u?(div|mod)si3|mulsi3|muldi3|ashldi3|lshrdi3|ashrdi3)$")
MARGIN = 32  # bytes on top of the computed worst case
PANIC_ENTRIES = ["fw_trap_exception", "fw_trap_nmi", "fw_trap_stack_overflow"]


def frames(objdir: Path) -> tuple[dict[str, int], list[str]]:
    sizes, problems = dict(ASM_FRAMES), []
    for su in objdir.glob("*.su"):
        for line in su.read_text().splitlines():
            loc, size, kind = line.rsplit("\t", 2)
            name = loc.rsplit(":", 1)[-1]
            sizes[name] = max(sizes.get(name, 0), int(size))
            if kind != "static":
                problems.append(f"{name}: {kind} stack frame")
    return sizes, problems


def call_graph(elf: Path, objdump: str, code_syms: set) -> tuple[dict[str, set], set]:
    """code_syms: text symbols only. Absolute linker symbols (FW_STACK_MIN...)
    can share an address with code and must not start a new function."""
    dis = subprocess.run([objdump, "-d", "--no-show-raw-insn", str(elf)], check=True,
                         capture_output=True, text=True).stdout
    graph: dict[str, set] = {}
    indirect: set = set()
    cur = None
    for line in dis.splitlines():
        m = re.match(r"^[0-9a-f]+ <([^>]+)>:$", line)
        if m and m.group(1) in code_syms:
            cur = m.group(1)
            graph.setdefault(cur, set())
            continue
        if cur is None:
            continue
        m = re.search(r"\s(jal|call|j|tail|beqz|bnez|beq|bne|blt|bge|bltu|bgeu|bleu|bgtu)\s.*<([^>+]+)(\+0x[0-9a-f]+)?>",
                      line)
        if m:
            target = m.group(2)
            if target != cur and target in code_syms:
                # jal/call keep this frame. In compiled code a jump or branch
                # into another function is a tail call, made after this frame
                # is popped. Hand-written assembly gets no such credit.
                graph[cur].add((target, m.group(1) in ("jal", "call") or cur in ASM_FRAMES))
        elif re.search(r"\sjalr\s", line) and not re.search(r"\sjalr\s+zero", line):
            indirect.add(cur)
        elif re.search(r"\sjr\s+(?!ra\b)", line):
            indirect.add(cur)
    return graph, indirect


def frame_name(fn: str, sizes) -> str:
    """GCC names clones 'f.constprop.0' / 'f.isra.0' in the ELF and
    'f.constprop' in the .su file."""
    if fn not in sizes:
        base = re.sub(r"\.\d+$", "", fn)
        if base in sizes:
            return base
    return fn


def worst(fn: str, graph, sizes, unknown: set, stack=()) -> int:
    if fn in stack:
        raise RecursionError(" -> ".join(stack + (fn,)))
    if frame_name(fn, sizes) not in sizes and not LEAF_LIBGCC.match(fn) and not fn.startswith("."):
        unknown.add(fn)
    here = sizes.get(frame_name(fn, sizes), 0)
    deepest = here
    for c, is_call in graph.get(fn, ()):
        w = worst(c, graph, sizes, unknown, stack + (fn,))
        deepest = max(deepest, here + w if is_call else w)
    return deepest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("elf", type=Path)
    ap.add_argument("objdir", type=Path)
    ap.add_argument("--objdump", default="riscv64-unknown-elf-objdump")
    ap.add_argument("--nm", default="riscv64-unknown-elf-nm")
    args = ap.parse_args()

    sizes, problems = frames(args.objdir)
    syms, code_syms = {}, set()
    for line in subprocess.run([args.nm, str(args.elf)], check=True, capture_output=True,
                               text=True).stdout.splitlines():
        p = line.split()
        if len(p) == 3:
            syms[p[2]] = int(p[0], 16)
            if p[1] in "Tt":
                code_syms.add(p[2])
    graph, indirect = call_graph(args.elf, args.objdump, code_syms)
    unknown: set = set()
    try:
        main_path = worst("_start", graph, sizes, unknown)
        # Indirect calls (registered interrupt handlers): any function with a
        # C frame could be the target; take the deepest one.
        c_funcs = [f for f in graph if f in sizes and f not in ASM_FRAMES]
        handler = max((worst(f, graph, sizes, unknown) for f in c_funcs), default=0)
        irq = worst("_trap_irq", graph, sizes, unknown) + (handler if indirect else 0)
        panic = max((worst(f, graph, sizes, unknown) for f in PANIC_ENTRIES if f in graph),
                    default=0)
    except RecursionError as e:
        print(f"{args.elf.name}: recursion: {e}")
        return 1
    # fw_irq_dispatch's indirect call only matters if interrupts can be enabled
    has_irq = "irq_enable" in syms or "pe_evt_irq_enable" in syms
    need = main_path + (irq if has_irq else 0)
    avail = syms["__stack_top"] - syms["__stack_limit"] - 4          # guard word
    panic_avail = syms["__panic_stack_top"] - syms["__stack_top"]
    print(f"{args.elf.name}: stack worst case {need} B (main path {main_path}"
          f"{f' + interrupt {irq}' if has_irq else ', no interrupts'}) of {avail} B; "
          f"panic stack {panic} of {panic_avail} B")
    if unknown:
        problems.append("no frame size for: " + ", ".join(sorted(unknown)))
    if need + MARGIN > avail:
        problems.append(f"main stack: worst case {need} B + {MARGIN} B margin > {avail} B available")
    if panic > panic_avail:
        problems.append(f"panic stack: worst case {panic} B > {panic_avail} B")
    for p in problems:
        print(f"{args.elf.name}: ERROR {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
