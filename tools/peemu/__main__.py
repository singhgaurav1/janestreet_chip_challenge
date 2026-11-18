"""Command line: python -m peemu asm prog.pe [-f hex|c|json|list] [-o out]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .asm import AsmError, assemble, to_c_header, to_hex, to_json


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="peemu", description="Protocol Engine tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("asm", help="assemble a .pe source file")
    a.add_argument("src", type=Path)
    a.add_argument("-f", "--format", choices=["hex", "c", "json", "list"], default="list")
    a.add_argument("-o", "--out", type=Path, help="output file (default stdout)")
    args = ap.parse_args(argv)

    try:
        progs = assemble(args.src.read_text())
    except AsmError as e:
        print(f"{args.src}: {e}", file=sys.stderr)
        return 1
    fmt = {"hex": to_hex, "c": to_c_header, "json": to_json, "list": lambda p: p.listing() + "\n"}
    text = "\n".join(fmt[args.format](p) for p in progs)
    if args.out:
        args.out.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
