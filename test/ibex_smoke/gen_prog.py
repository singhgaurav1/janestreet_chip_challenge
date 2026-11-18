#!/usr/bin/env python3
"""Hand-encoded RV32IMC smoke program for the converted Ibex (no toolchain needed).

Writes prog.hex ($readmemh, 32-bit words from address 0) and expect.hex
(word address, expected value) for tb_ibex_smoke.v. Ibex boots at
boot_addr + 0x80.
"""
import sys
from pathlib import Path


def r(f7, rs2, rs1, f3, rd, op=0x33):
    return f7 << 25 | rs2 << 20 | rs1 << 15 | f3 << 12 | rd << 7 | op


def i(imm, rs1, f3, rd, op=0x13):
    return (imm & 0xFFF) << 20 | rs1 << 15 | f3 << 12 | rd << 7 | op


def s(imm, rs2, rs1, f3):
    return ((imm >> 5) & 0x7F) << 25 | rs2 << 20 | rs1 << 15 | f3 << 12 | (imm & 31) << 7 | 0x23


def b(off, rs2, rs1, f3):
    o = off & 0x1FFF
    return ((o >> 12) & 1) << 31 | ((o >> 5) & 0x3F) << 25 | rs2 << 20 | rs1 << 15 | f3 << 12 \
        | ((o >> 1) & 0xF) << 8 | ((o >> 11) & 1) << 7 | 0x63


def jal(off, rd):
    o = off & 0x1FFFFF
    return ((o >> 20) & 1) << 31 | ((o >> 1) & 0x3FF) << 21 | ((o >> 11) & 1) << 20 \
        | ((o >> 12) & 0xFF) << 12 | rd << 7 | 0x6F


addi = lambda rd, rs1, imm: i(imm, rs1, 0, rd)
add = lambda rd, a, c: r(0, c, a, 0, rd)
mul = lambda rd, a, c: r(1, c, a, 0, rd)
divu = lambda rd, a, c: r(1, c, a, 5, rd)
lw = lambda rd, imm, rs1: i(imm, rs1, 2, rd, 0x03)
sw = lambda rs2, imm, rs1: s(imm, rs2, rs1, 2)
sb = lambda rs2, imm, rs1: s(imm, rs2, rs1, 0)
bne = lambda a, c, off: b(off, c, a, 1)
lui = lambda rd, imm20: imm20 << 12 | rd << 7 | 0x37
C_ADDI_X7_1, C_NOP = 0x0385, 0x0001

PROG = [                                 # address
    addi(1, 0, 5),                       # 0x80
    addi(2, 0, 7),                       # 0x84
    add(3, 1, 2),                        # 0x88  x3 = 12
    mul(4, 3, 2),                        # 0x8c  x4 = 84
    sw(3, 0x100, 0),                     # 0x90
    sw(4, 0x104, 0),                     # 0x94
    lw(5, 0x100, 0),                     # 0x98
    addi(5, 5, 1),                       # 0x9c  x5 = 13
    sw(5, 0x108, 0),                     # 0xa0
    addi(6, 0, 10),                      # 0xa4
    addi(7, 0, 0),                       # 0xa8
    add(7, 7, 6),                        # 0xac  loop: x7 += x6
    addi(6, 6, -1),                      # 0xb0
    bne(6, 0, -8),                       # 0xb4  -> 0xac ; x7 = 55
    C_NOP << 16 | C_ADDI_X7_1,           # 0xb8  c.addi x7, 1 ; c.nop  -> 56
    sw(7, 0x10C, 0),                     # 0xbc
    sb(1, 0x110, 0),                     # 0xc0  byte store into a pre-filled word
    lui(8, 0x12345),                     # 0xc4
    sw(8, 0x114, 0),                     # 0xc8
    divu(9, 4, 2),                       # 0xcc  84 / 7 = 12
    sw(9, 0x118, 0),                     # 0xd0
    addi(10, 0, 1),                      # 0xd4
    sw(10, 0x1FC, 0),                    # 0xd8  done marker
    jal(0, 0),                           # 0xdc  spin
]
EXPECT = {0x100: 12, 0x104: 84, 0x108: 13, 0x10C: 56, 0x110: 0xDEADBE05,
          0x114: 0x12345000, 0x118: 12, 0x1FC: 1}
PREFILL = {0x110: 0xDEADBEEF}


def main(outdir: Path):
    mem = [0] * 512
    for k, w in enumerate(PROG):
        mem[(0x80 >> 2) + k] = w
    for a, v in PREFILL.items():
        mem[a >> 2] = v
    (outdir / "prog.hex").write_text("\n".join(f"{w:08x}" for w in mem) + "\n")
    rows = [f"{a >> 2:08x}{v:08x}" for a, v in EXPECT.items()]
    rows += ["f" * 16] * (16 - len(rows))          # unused slots (the TB table has 16)
    (outdir / "expect.hex").write_text("\n".join(rows) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
