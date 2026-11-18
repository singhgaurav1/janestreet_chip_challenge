#!/usr/bin/env python3
"""Build Ibex firmware images (RV32IMC) with Zig's bundled clang.

    pip install ziglang==0.17.1
    python3 firmware/build.py            # builds every firmware/tests/*.c
    python3 firmware/build.py smoke      # one image

Output: firmware/images/<name>.hex — one 32-bit little-endian word per line,
from address 0 — which the host (and the cocotb tests) load over SPI. Images
are committed so simulation needs no toolchain; CI rebuilds and diffs them.
"""
from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
from pathlib import Path

FW = Path(__file__).resolve().parent
RAM_BYTES = 2048
CFLAGS = ["-target", "riscv32-freestanding-none", "-mcpu=generic_rv32+m+c", "-mabi=ilp32",
          "-Os", "-g0", "-ffreestanding", "-nostdlib", "-fno-pic", "-fno-builtin",
          "-ffunction-sections", "-fdata-sections", "-Wall", "-Wextra", "-Werror",
          f"-I{FW / 'common'}"]
LDFLAGS = ["-T", str(FW / "common" / "link.ld"), "-Wl,--gc-sections"]


def elf_image(elf: bytes) -> bytes:
    """Flatten the loadable segments of a 32-bit little-endian ELF into a RAM image."""
    assert elf[:4] == b"\x7fELF" and elf[4] == 1 and elf[5] == 1, "need ELF32 little-endian"
    phoff, = struct.unpack_from("<I", elf, 0x1C)
    phentsize, phnum = struct.unpack_from("<HH", elf, 0x2A)
    img = bytearray()
    for k in range(phnum):
        p_type, p_off, _vaddr, p_paddr, p_filesz, _memsz, _flags, _align = \
            struct.unpack_from("<8I", elf, phoff + k * phentsize)
        if p_type != 1 or p_filesz == 0:      # PT_LOAD with file contents
            continue
        end = p_paddr + p_filesz
        if end > RAM_BYTES:
            raise SystemExit(f"segment at 0x{p_paddr:x}+{p_filesz} exceeds {RAM_BYTES} B of SRAM")
        if len(img) < end:
            img.extend(b"\0" * (end - len(img)))
        img[p_paddr:end] = elf[p_off:p_off + p_filesz]
    img.extend(b"\0" * (-len(img) % 4))
    return bytes(img)


def build(name: str) -> Path:
    src = FW / "tests" / f"{name}.c"
    out = FW / "images" / f"{name}.hex"
    out.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        elf = Path(td) / f"{name}.elf"
        cmd = [sys.executable, "-m", "ziglang", "cc", *CFLAGS, *LDFLAGS, "-o", str(elf),
               str(FW / "common" / "crt0.S"), str(src)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            sys.stderr.write(r.stderr)
            raise SystemExit(f"build of {name} failed")
        img = elf_image(elf.read_bytes())
    words = struct.unpack(f"<{len(img) // 4}I", img)
    out.write_text("".join(f"{w:08x}\n" for w in words))
    print(f"{out.relative_to(FW.parent)}: {len(img)} of {RAM_BYTES} bytes")
    return out


def main(argv):
    names = argv[1:] or sorted(p.stem for p in (FW / "tests").glob("*.c"))
    for n in names:
        build(n)


if __name__ == "__main__":
    main(sys.argv)
