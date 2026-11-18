"""ISA encoding, assembler and register-packing unit tests."""
import subprocess

import pytest

from peemu import AsmError, EngineConfig, assemble, assemble_one, isa, to_c_header
from peemu.config import LAYOUT

# Every syntactic form, with its expected encoding (hand-computed from
# docs/pe_isa.md section 3, not from the encoder).
CASES = [
    ("jmp 5", 0x0005),
    ("jmp !x, 3", 0x0023),
    ("jmp x--, 31", 0x005F),
    ("jmp !y, 1", 0x0061),
    ("jmp y--, 2", 0x0082),
    ("jmp pin, 4", 0x00A4),
    ("jmp !osre, 6", 0x00C6),
    ("jmp t, 7", 0x00E7),
    ("wait 1 gpio 17", 0x2091),
    ("wait 0 pin 3", 0x2023),
    ("wait 1 evt 5", 0x20C5),
    ("wait 1 evt 2 rel keep", 0x20DA),
    ("wait 0 jmppin", 0x2060),
    ("in pins, 8", 0x4008),
    ("in x, 32", 0x4020),
    ("in null, 24", 0x4078),
    ("in bit, 1", 0x4081),
    ("in isr, 1", 0x40C1),
    ("in osr, 2", 0x40E2),
    ("out pins, 1", 0x6001),
    ("out y, 16", 0x6050),
    ("out null, 32", 0x6060),
    ("out pindirs, 2", 0x6082),
    ("out pc, 5", 0x60A5),
    ("out isr, 3", 0x60C3),
    ("out bit, 1", 0x60E1),
    ("push", 0x8010),
    ("push iffull noblock", 0x8020),
    ("pull noblock", 0x8040),
    ("pull ifempty", 0x8070),
    ("evt set 3", 0x8083),
    ("evt clr 7 rel", 0x809F),
    ("evt setwait 1", 0x80A1),
    ("sync", 0x80C0),
    ("assist on crc, coder", 0x80D5),
    ("assist off stuff", 0x80E2),
    ("crcinit", 0x80F0),
    ("mov x, y", 0xA022),
    ("mov pins, ~isr", 0xA00D),
    ("mov osr, ::crc", 0xA0D7),
    ("mov crc, ~::x", 0xA0F9),
    ("mov pc, status", 0xA084),
    ("nop", 0xA042),
    ("match isr, 8", 0xC008),
    ("match crc, 16", 0xC070),
    ("match pins, 32", 0xC080),
    ("set pins, 31", 0xE01F),
    ("set y, 0", 0xE040),
    ("set pindirs, 3", 0xE083),
]


@pytest.mark.parametrize("src,word", CASES)
def test_encoding(src, word):
    p = assemble_one(f".program t\n {src}\n")
    assert p.code == [word], f"{src}: got {p.code[0]:04x}"


@pytest.mark.parametrize("src,word", CASES)
def test_disasm_roundtrip(src, word):
    text = isa.disasm(word)
    p = assemble_one(f".program t\n {text}\n")
    assert p.code == [word], text


def test_every_opcode_covered():
    ops = {w >> 13 for _, w in CASES}
    assert ops == set(range(8))


def test_side_set_and_delay():
    p = assemble_one(".program t\n.side_set 2 opt\n nop side 3 [1]\n nop [3]\n nop side 0\n")
    # count=3 (2 pins + enable) -> delay has 2 bits
    assert [(w >> 8) & 31 for w in p.code] == [0b11101, 0b00011, 0b10000]
    sc = isa.SideCfg(3, True)
    assert sc.unpack(0b11101) == (3, 1) and sc.unpack(0b00011) == (None, 3)


def test_side_set_pindirs_flag():
    p = assemble_one(".program t\n.side_set 1 pindirs\n nop side 1\n")
    cfg = p.engine_config()
    assert cfg.side_count == 1 and cfg.side_pindirs == 1 and cfg.side_opt == 0


@pytest.mark.parametrize("src,msg", [
    (" nop [8]\n", "delay"),                       # no side-set: 5 delay bits ok... see below
    (" out bit, 2\n", "exactly one bit"),
    (" jmp nowhere\n", "undefined"),
    (" in foo, 1\n", "bad operand"),
    (" set x, 32\n", "does not fit"),
    (" wait 1 evt 9\n", "0..7"),
    (" assist on magic\n", "unknown assist"),
    (" frob\n", "unknown instruction"),
])
def test_errors(src, msg):
    if msg == "delay":
        src = ".side_set 2\n nop side 0 [8]\n"
    with pytest.raises(AsmError, match=msg):
        assemble(".program t\n" + src)


def test_program_too_long():
    with pytest.raises(AsmError, match="max 32"):
        assemble(".program t\n" + " nop\n" * 33)


def test_side_required_when_not_optional():
    with pytest.raises(AsmError, match="required"):
        assemble(".program t\n.side_set 1\n nop\n")


def test_labels_wrap_defines_and_expressions():
    p = assemble_one("""
.program t
.define N 3
.define public M (N << 2) + 1
start:
    set x, N
.wrap_target
loop: jmp x--, loop
    set y, M
.wrap
    jmp start
""")
    assert p.labels == {"start": 0, "loop": 1}
    assert (p.wrap_bottom, p.wrap_top) == (1, 2)
    assert p.code[2] & 31 == 13
    assert "M" in p.public


def test_label_named_like_mnemonic():
    p = assemble_one(".program t\nsync: sync\n jmp sync\n")
    assert p.code == [0x80C0, 0x0000]


def test_crc_directive_reflects_poly():
    p = assemble_one(".program t\n.crc 16 0x8005 reflect init_ones\n nop\n")
    assert p.config == {"crc_width": 16, "crc_poly": 0xA001, "crc_reflect": 1, "crc_init_ones": 1}


def test_config_enums_and_unknown_field():
    p = assemble_one(".program t\n.config coder_mode manchester\n.config stuff_mode ones\n nop\n")
    assert p.config == {"coder_mode": 3, "stuff_mode": 1}
    with pytest.raises(AsmError, match="unknown config"):
        assemble(".program t\n.config bogus 1\n nop\n")


def test_register_pack_roundtrip():
    import random
    rnd = random.Random(1)
    for _ in range(200):
        kw = {}
        for reg, name, lsb, width, (enc, dec) in LAYOUT:
            kw[name] = dec(rnd.randrange(1 << width))
        cfg = EngineConfig(**kw)
        if cfg.set_count > 5 or cfg.out_count > 32 or cfg.side_count > 5:
            continue
        assert EngineConfig.from_regs(cfg.to_regs()) == cfg


def test_register_fields_do_not_overlap():
    used = {}
    for reg, name, lsb, width, _ in LAYOUT:
        for b in range(lsb, lsb + width):
            assert (reg, b) not in used, f"{name} overlaps {used[(reg, b)]}"
            assert b < 32
            used[(reg, b)] = name


def test_c_header_compiles(tmp_path):
    from helpers import example
    for name, p in example("uart").items():
        h = tmp_path / f"{name}.h"
        h.write_text(to_c_header(p))
        c = tmp_path / f"{name}.c"
        c.write_text(f'#include "{h.name}"\nint main(void){{return {name}_program[0] == 0;}}\n')
        r = subprocess.run(["cc", "-std=c99", "-Wall", "-Werror", "-c", str(c), "-o", str(tmp_path / "x.o")],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_all_examples_assemble():
    from helpers import EXAMPLES
    n = 0
    for f in sorted(EXAMPLES.glob("*.pe")):
        for p in assemble(f.read_text()):
            assert len(p.code) <= isa.PROG_MAX
            p.engine_config().validate()
            n += 1
    assert n >= 8
