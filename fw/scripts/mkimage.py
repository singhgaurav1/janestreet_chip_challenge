#!/usr/bin/env python3
"""Turn a linked firmware ELF into a loadable image.

    mkimage.py <elf> <out-prefix> [--objcopy riscv64-unknown-elf-objcopy]

Writes <out-prefix>.bin (raw bytes from the boot address, what the host loads
over the SPI bridge) and <out-prefix>.hex (same bytes in the format
$readmemh/objcopy -O verilog uses, for the testbenches). Fills the image header
at offset 0x84 (see fw/include/fw.h): length and CRC-32 of the image with the
CRC field taken as zero. Prints a size report.
"""
from __future__ import annotations

import argparse
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

HDR_OFF = 0x84
MAGIC = 0x57464550  # "PEFW"
# libgcc routines too large for a 2-4 KB firmware RAM (64-bit divide alone is
# ~2.4 KB). Linking one is an error unless --allow-symbol names it.
FORBIDDEN = {"__udivdi3", "__umoddi3", "__divdi3", "__moddi3", "__clz_tab"}


def symbols(elf: Path, nm: str) -> dict[str, int]:
    out = subprocess.run([nm, str(elf)], check=True, capture_output=True, text=True).stdout
    syms = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            syms[parts[2]] = int(parts[0], 16)
    return syms


def make_image(raw: bytes) -> bytes:
    img = bytearray(raw)
    magic, _length, _crc, version = struct.unpack_from("<IIII", img, HDR_OFF)
    if magic != MAGIC:
        raise SystemExit(f"image header magic 0x{magic:08x} at 0x{HDR_OFF:x} is not PEFW")
    struct.pack_into("<II", img, HDR_OFF + 4, len(img), 0)
    crc = zlib.crc32(bytes(img)) & 0xFFFFFFFF
    struct.pack_into("<I", img, HDR_OFF + 8, crc)
    return bytes(img)


def to_hex(img: bytes) -> str:
    lines = ["@00000000"]
    for i in range(0, len(img), 16):
        lines.append(" ".join(f"{b:02X}" for b in img[i:i + 16]))
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("elf", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--objcopy", default="riscv64-unknown-elf-objcopy")
    ap.add_argument("--nm", default="riscv64-unknown-elf-nm")
    ap.add_argument("--allow-symbol", action="append", default=[])
    args = ap.parse_args()

    bad = sorted((FORBIDDEN - set(args.allow_symbol)) & set(symbols(args.elf, args.nm)))
    if bad:
        raise SystemExit(f"{args.elf}: links {', '.join(bad)} (64-bit division from libgcc, "
                         "about 2.4 KB). Avoid 64-bit / and %, or pass --allow-symbol.")

    with tempfile.TemporaryDirectory() as td:
        rawf = Path(td) / "raw.bin"
        subprocess.run([args.objcopy, "-O", "binary", "--only-section=.vectors",
                        "--only-section=.text", "--only-section=.rodata", "--only-section=.data",
                        str(args.elf), str(rawf)], check=True)
        raw = rawf.read_bytes()

    s = symbols(args.elf, args.nm)
    base = s.get("_vectors", 0)
    if len(raw) != s["__image_end"] - base:
        raise SystemExit(f"binary is {len(raw)} bytes but __image_end - base is "
                         f"{s['__image_end'] - base}: unexpected section layout")
    img = make_image(raw)
    args.out.with_suffix(".bin").write_bytes(img)
    args.out.with_suffix(".hex").write_text(to_hex(img))

    ram = s["fw_status"] + 64 - base
    bss = s["__bss_end"] - s["__bss_start"]
    stack = s["__stack_top"] - s["__stack_limit"]
    print(f"{args.out.name}: image {len(img)} B (code+rodata {s['__data_start'] - base}, "
          f"data {s['__image_end'] - s['__data_start']}), bss {bss} B, "
          f"stack {stack} B, RAM {ram} B, crc32 0x{struct.unpack_from('<I', img, HDR_OFF + 8)[0]:08x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
