#!/usr/bin/env python3
"""Engine configurations computed and checked at build time.

    pecfg.py FILE.pecfg -o OUT.h --pe-headers A_pe.h ... [-D NAME=VALUE ...]

Each line of a .pecfg file is

    <name> <program> [<field>=<expr> ...] [tick_hz=<expr>]

<program> is a program from one of the --pe-headers (its assembler defaults
are the starting point), <field> is a pe_field_t name without PE_F_ (e.g.
origin, out_base), tick_hz sets the divider. Expressions are Python
arithmetic over the -D names and SYS_HZ. '#' starts a comment.

The configuration is built and checked by the firmware's own C code
(src/pe_hal.c: pe_cfg_set, pe_cfg_set_tick_hz, pe_cfg_check), compiled for
the host, so a configuration that the HAL would refuse at run time fails
the build here, with the same rules. The output is `static const pe_cfg_t
<name>` for pe_configure_prechecked(), which skips the run-time check and
the ~0.8 KB of code behind it.
"""
from __future__ import annotations

import argparse
import ast
import operator
import subprocess
import sys
import tempfile
from pathlib import Path

FW = Path(__file__).resolve().parents[1]
REGS = ["CLKDIV", "EXECCTRL", "SHIFTCTRL", "PINCTRL", "ASSIST", "CRC_POLY", "TIMING"]
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.LShift: operator.lshift,
       ast.RShift: operator.rshift, ast.BitOr: operator.or_, ast.BitAnd: operator.and_}


def evaluate(expr: str, names: dict[str, int]) -> int:
    """Integer arithmetic only (no calls, no attribute access)."""
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, int):
            return n.value
        if isinstance(n, ast.Name) and n.id in names:
            return names[n.id]
        if isinstance(n, ast.BinOp) and type(n.op) in OPS:
            return OPS[type(n.op)](ev(n.left), ev(n.right))
        raise ValueError(f"unsupported expression {expr!r}")
    return int(ev(ast.parse(expr, mode="eval")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pecfg", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--pe-headers", nargs="*", default=[], type=Path)
    ap.add_argument("-D", dest="defs", action="append", default=[])
    ap.add_argument("--sys-hz", type=int, default=60_000_000)
    ap.add_argument("--cc", default="cc")
    a = ap.parse_args()

    names = {"SYS_HZ": a.sys_hz}
    for d in a.defs:
        k, v = d.split("=", 1)
        try:
            names[k] = int(v, 0)
        except ValueError:
            pass   # non-numeric (e.g. a variant name): not usable in expressions

    body, entries = [], []
    for lineno, raw in enumerate(a.pecfg.read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].split()
        if not line:
            continue
        if len(line) < 2:
            print(f"{a.pecfg}:{lineno}: need <name> <program>", file=sys.stderr)
            return 1
        name, prog = line[0], line[1].upper()
        where = f"{a.pecfg.name}:{lineno}"
        body.append(f"    pe_cfg_from_regs(&c, {', '.join(f'{prog}_{r}' for r in REGS)});")
        for item in line[2:]:
            k, _, v = item.partition("=")
            try:
                val = evaluate(v, names)
            except (ValueError, SyntaxError) as e:
                print(f"{a.pecfg}:{lineno}: {e}", file=sys.stderr)
                return 1
            if k == "tick_hz":
                call = f"pe_cfg_set_tick_hz(&c, {a.sys_hz}u, {val}u)"
            else:
                call = f"pe_cfg_set(&c, PE_F_{k.upper()}, {val}u)"
            body.append(f'    chk({call}, "{where}: {item} (= {val})");')
        body.append(f'    chk(pe_cfg_check(&c), "{where}: pe_cfg_check");')
        body.append(f'    emit("{name}", &c);')
        entries.append(name)

    incs = "\n".join(f'#include "{h.resolve()}"' for h in a.pe_headers)
    src = f"""#include <stdio.h>
#include <stdlib.h>
#include "pe_hal.h"
{incs}
static void chk(int r, const char *what)
{{
    if (r != PE_OK) {{ fprintf(stderr, "pecfg: %s rejected by the HAL (%d)\\n", what, r); exit(1); }}
}}
static void emit(const char *n, const pe_cfg_t *c)
{{
    printf("static const pe_cfg_t %s = {{0x%08xu, 0x%08xu, 0x%08xu, 0x%08xu, 0x%08xu, 0x%08xu, 0x%08xu}};\\n",
           n, c->clkdiv, c->execctrl, c->shiftctrl, c->pinctrl, c->assist, c->crc_poly, c->timing);
}}
int main(void)
{{
    pe_cfg_t c;
{chr(10).join(body)}
    return 0;
}}
"""
    with tempfile.TemporaryDirectory() as td:
        c, exe = Path(td) / "gen.c", Path(td) / "gen"
        c.write_text(src)
        cmd = [a.cc, "-std=c11", "-O1", "-Wall", "-Werror", "-DFW_HOST_TEST",
               f"-I{FW / 'include'}", f"-I{FW / 'src'}", f"-I{FW / 'tests/host'}",
               "-o", str(exe), str(c), str(FW / "src/pe_hal.c"), str(FW / "tests/host/mock.c")]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode:
            sys.stderr.write(p.stderr)
            return 1
        p = subprocess.run([str(exe)], capture_output=True, text=True)
        if p.returncode:
            sys.stderr.write(p.stderr)
            return 1
    guard = f"PECFG_{a.pecfg.stem.upper()}_H"
    defs = " ".join(a.defs)
    a.out.write_text(
        f"/* Generated by fw/scripts/pecfg.py from {a.pecfg.name} ({defs or 'no defines'}); "
        f"checked by pe_cfg_check(). Do not edit. */\n"
        f"#ifndef {guard}\n#define {guard}\n#include \"pe_hal.h\"\n#include \"platform.h\"\n"
        f"_Static_assert(FW_SYS_HZ == {a.sys_hz}u, \"pecfg: --sys-hz differs from FW_SYS_HZ\");\n"
        f"{p.stdout}#endif\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
