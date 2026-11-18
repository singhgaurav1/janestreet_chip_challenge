"""Reference CRCs, written independently of the engine's LFSR.

`crc_bytes` is the textbook table-free Rocksoft-model CRC (width, poly, init,
refin, refout, xorout). Tests compare engine results against it, so the
engine model is never checked against itself. `residue` gives the raw LFSR
state the engine holds after a correct frame (data + transmitted CRC), for use
with `match crc, W`.
"""
from __future__ import annotations


def _reflect(v: int, w: int) -> int:
    return int(f"{v:0{w}b}"[::-1], 2)


def crc_bytes(data: bytes, width: int, poly: int, init: int = 0, refin: bool = False,
              refout: bool = False, xorout: int = 0) -> int:
    top = 1 << (width - 1)
    m = (1 << width) - 1
    reg = init & m
    for byte in data:
        if refin:
            byte = _reflect(byte, 8)
        for i in range(7, -1, -1):
            bit = (byte >> i) & 1
            fb = bool(reg & top) ^ bit
            reg = (reg << 1) & m
            if fb:
                reg ^= poly
    if refout:
        reg = _reflect(reg, width)
    return reg ^ xorout


def crc_bits(bits: list[int], width: int, poly: int, init: int = 0) -> int:
    """Raw bit-serial LFSR over wire-order bits (what the engine computes)."""
    m = (1 << width) - 1
    reg = init & m
    for b in bits:
        fb = ((reg >> (width - 1)) & 1) ^ b
        reg = (reg << 1) & m
        if fb:
            reg ^= poly
    return reg


def residue(width: int, poly: int, init: int, data_bits: list[int], crc_wire_bits: list[int],
            reflect: bool = False) -> int:
    """Engine CRC state after data bits followed by the transmitted CRC bits.

    With reflect=True the value is laid out as the engine holds it when
    CRC_REFLECT=1 (bit-reversed within the width)."""
    r = crc_bits(data_bits + crc_wire_bits, width, poly, init)
    return _reflect(r, width) if reflect else r


def bits_lsb_first(data: bytes) -> list[int]:
    return [(b >> i) & 1 for b in data for i in range(8)]


# Common catalogue entries (width, poly, init, refin, refout, xorout, check of b"123456789")
CATALOGUE = {
    "CRC-5/USB": (5, 0x05, 0x1F, True, True, 0x1F, 0x19),
    "CRC-16/USB": (16, 0x8005, 0xFFFF, True, True, 0xFFFF, 0xB4C8),
    "CRC-15/CAN": (15, 0x4599, 0x0000, False, False, 0x0000, 0x059E),
    "CRC-8/SMBUS": (8, 0x07, 0x00, False, False, 0x00, 0xF4),
    "CRC-32/ISO-HDLC": (32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF, 0xCBF43926),
}
