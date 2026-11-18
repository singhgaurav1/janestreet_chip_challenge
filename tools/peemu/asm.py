"""peasm — assembler for the Protocol Engine ISA.

Syntax is pioasm-like (see docs/pe_isa.md and examples/pe/). Example:

    .program uart_tx
    .side_set 1 opt
    .config out_shift_right 1
    .config autopull 1
    .config pull_thresh 8
        set x, 7        side 0 [7]   ; start bit
    bitloop:
        out pins, 1
        jmp x--, bitloop     [6]
        nop             side 1 [7]   ; stop bit

Directives: .program, .side_set N [opt] [pindirs], .wrap_target, .wrap,
.define [public] NAME expr, .config FIELD value. Comments: ';' and '//'.
"""
from __future__ import annotations

import ast
import json
import operator
import re
from dataclasses import dataclass, field

from . import isa
from .config import CODER_MODES, STUFF_MODES, SYNC_EDGES, EngineConfig


class AsmError(Exception):
    def __init__(self, msg: str, line: int | None = None, src: str = ""):
        self.line = line
        super().__init__(f"line {line}: {msg}" + (f"\n    {src.strip()}" if src else "") if line else msg)


@dataclass
class Program:
    name: str
    code: list[int] = field(default_factory=list)
    side: isa.SideCfg = field(default_factory=isa.SideCfg)
    wrap_bottom: int = 0
    wrap_top: int | None = None
    config: dict = field(default_factory=dict)      # EngineConfig overrides
    labels: dict = field(default_factory=dict)
    defines: dict = field(default_factory=dict)
    public: set = field(default_factory=set)
    lines: list[int] = field(default_factory=list)  # source line per instruction

    def engine_config(self, base: EngineConfig | None = None, **overrides) -> EngineConfig:
        """Config implied by the program, with loader overrides (pins, clkdiv, origin...)."""
        cfg = (base or EngineConfig()).updated(
            side_count=self.side.count, side_opt=int(self.side.opt),
            side_pindirs=int(self.side.pindirs), wrap_bottom=self.wrap_bottom,
            wrap_top=self.wrap_top if self.wrap_top is not None else len(self.code) - 1)
        cfg = cfg.updated(**self.config)
        return cfg.updated(**overrides)

    def listing(self) -> str:
        out = [f"; .program {self.name} ({len(self.code)} words)"]
        inv = {v: k for k, v in self.labels.items()}
        for i, w in enumerate(self.code):
            mark = ("w>" if i == self.wrap_bottom else "  ") + ("<w" if i == self.wrap_top else "  ")
            lab = f"{inv[i]}:" if i in inv else ""
            out.append(f"{i:2d} {mark} {w:04x}  {lab:<12} {isa.disasm(w, self.side)}")
        return "\n".join(out)


# ---- expressions ------------------------------------------------------------

_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.FloorDiv: operator.floordiv, ast.Div: operator.floordiv, ast.Mod: operator.mod,
           ast.LShift: operator.lshift, ast.RShift: operator.rshift, ast.BitOr: operator.or_,
           ast.BitAnd: operator.and_, ast.BitXor: operator.xor}
_UNOPS = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Invert: operator.invert}


def eval_expr(text: str, names: dict) -> int:
    text = text.strip()
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise ValueError(f"bad expression '{text}'") from None

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, int):
            return n.value
        if isinstance(n, ast.Name):
            if n.id not in names:
                raise ValueError(f"undefined symbol '{n.id}'")
            return names[n.id]
        if isinstance(n, ast.BinOp) and type(n.op) in _BINOPS:
            return _BINOPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UNOPS:
            return _UNOPS[type(n.op)](ev(n.operand))
        raise ValueError(f"unsupported expression '{text}'")

    return int(ev(tree))


# ---- parsing ----------------------------------------------------------------

_LABEL = re.compile(r"^(public\s+)?([A-Za-z_]\w*)\s*:(?!:)\s*(.*)$")
_DELAY = re.compile(r"\[([^\]]+)\]\s*$")
_SIDE = re.compile(r"\bside\s+(\S+)\s*$")
_ENUMS = {"coder_mode": CODER_MODES, "stuff_mode": STUFF_MODES, "sync_edge": SYNC_EDGES}


def _strip_comment(s: str) -> str:
    for c in (";", "//"):
        i = s.find(c)
        if i >= 0:
            s = s[:i]
    return s.strip()


def _split_mods(body: str) -> tuple[str, str | None, str | None]:
    """Peel trailing '[delay]' and 'side v' (either order) off an instruction."""
    side = delay = None
    for _ in range(2):
        m = _DELAY.search(body)
        if m and delay is None:
            delay, body = m.group(1), body[:m.start()].rstrip()
            continue
        m = _SIDE.search(body)
        if m and side is None:
            side, body = m.group(1), body[:m.start()].rstrip()
    return body, side, delay


def _args(rest: str) -> list[str]:
    return [a.strip() for a in rest.replace(",", " ").split() if a.strip()]


class _Asm:
    def __init__(self):
        self.programs: list[Program] = []
        self.p: Program | None = None
        self.pending: list[tuple[int, str, str, str | None, str | None]] = []
        self.global_defs: dict = {}

    # pass 1: directives, labels, collect instruction text
    def feed(self, text: str):
        for ln, raw in enumerate(text.splitlines(), 1):
            line = _strip_comment(raw)
            if not line:
                continue
            try:
                self._line(ln, line, raw)
            except AsmError:
                raise
            except (ValueError, KeyError) as e:
                raise AsmError(str(e), ln, raw) from None
        self._finish()

    def _need_prog(self):
        if self.p is None:
            raise ValueError("instruction or directive before .program")
        return self.p

    def _names(self):
        d = dict(self.global_defs)
        if self.p:
            d.update(self.p.defines)
        return d

    def _line(self, ln, line, raw):
        if line.startswith("."):
            parts = line.split(None, 1)
            d, rest = parts[0].lower(), parts[1] if len(parts) > 1 else ""
            if d == ".program":
                self._finish()
                self.p = Program(name=rest.strip())
                self.pending = []
            elif d == ".define":
                a = rest.split()
                pub = a and a[0] == "public"
                if pub:
                    a = a[1:]
                if len(a) < 2:
                    raise ValueError(".define needs NAME value")
                val = eval_expr(" ".join(a[1:]), self._names())
                tgt = self.p.defines if self.p else self.global_defs
                tgt[a[0]] = val
                if pub and self.p:
                    self.p.public.add(a[0])
            elif d == ".side_set":
                p = self._need_prog()
                a = rest.split()
                if not a:
                    raise ValueError(".side_set needs a pin count")
                n = eval_expr(a[0], self._names())
                opt, pd = "opt" in a[1:], "pindirs" in a[1:]
                p.side = isa.SideCfg(count=n + (1 if opt else 0), opt=opt, pindirs=pd)
            elif d == ".wrap_target":
                self._need_prog().wrap_bottom = len(self.pending)
            elif d == ".wrap":
                p = self._need_prog()
                if not self.pending:
                    raise ValueError(".wrap before any instruction")
                p.wrap_top = len(self.pending) - 1
            elif d == ".config":
                p = self._need_prog()
                a = rest.split(None, 1)
                if len(a) != 2:
                    raise ValueError(".config needs FIELD value")
                key, val = a[0], a[1].strip()
                if key in _ENUMS and val in _ENUMS[key]:
                    v = _ENUMS[key][val]
                else:
                    v = eval_expr(val, self._names())
                EngineConfig().updated(**{key: v})  # validates the field name
                p.config[key] = v
            elif d == ".crc":
                # .crc WIDTH POLY [reflect] [init_ones]  (POLY in normal form)
                p = self._need_prog()
                a = rest.split()
                if len(a) < 2:
                    raise ValueError(".crc needs WIDTH POLY [reflect] [init_ones]")
                w, poly = eval_expr(a[0], self._names()), eval_expr(a[1], self._names())
                fl = {x.lower() for x in a[2:]}
                if fl - {"reflect", "init_ones"}:
                    raise ValueError(f"bad .crc option(s): {', '.join(fl - {'reflect', 'init_ones'})}")
                if not 1 <= w <= 32 or not 0 <= poly < (1 << w):
                    raise ValueError("bad CRC width or polynomial")
                if "reflect" in fl:
                    poly = int(f"{poly:0{w}b}"[::-1], 2)
                p.config.update(crc_width=w, crc_poly=poly, crc_reflect=int("reflect" in fl),
                                crc_init_ones=int("init_ones" in fl))
            elif d in (".lang_opt", ".origin"):
                pass
            else:
                raise ValueError(f"unknown directive {d}")
            return
        p = self._need_prog()
        m = _LABEL.match(line)
        if m:
            name = m.group(2)
            if name in p.labels:
                raise ValueError(f"duplicate label '{name}'")
            p.labels[name] = len(self.pending)
            if m.group(1):
                p.public.add(name)
            line = m.group(3).strip()
            if not line:
                return
        body, side, delay = _split_mods(line)
        self.pending.append((ln, raw, body, side, delay))

    def _finish(self):
        p = self.p
        if p is None:
            return
        if len(self.pending) > isa.PROG_MAX:
            raise AsmError(f"program '{p.name}' has {len(self.pending)} instructions; max {isa.PROG_MAX}")
        names = {**self.global_defs, **p.defines, **p.labels}
        for ln, raw, body, side, delay in self.pending:
            try:
                w = self._encode(body, names)
                sv = eval_expr(side, names) if side is not None else None
                dl = eval_expr(delay, names) if delay is not None else 0
                w |= p.side.pack(sv, dl) << 8
            except (ValueError, KeyError, IndexError) as e:
                raise AsmError(str(e), ln, raw) from None
            p.code.append(w)
            p.lines.append(ln)
        if p.wrap_top is None:
            p.wrap_top = len(p.code) - 1
        self.programs.append(p)
        self.p = None
        self.pending = []

    # pass 2: encode one instruction
    def _encode(self, body: str, names: dict) -> int:
        parts = body.split(None, 1)
        mn, rest = parts[0].lower(), parts[1] if len(parts) > 1 else ""
        a = _args(rest)
        ev = lambda s: eval_expr(s, names)

        def pick(table, tok, what):
            tok = tok.lower()
            if tok not in table or tok is None:
                raise ValueError(f"bad {what} '{tok}'")
            return table.index(tok)

        if mn == "nop":
            return isa.NOP
        if mn == "jmp":
            if len(a) == 1:
                return isa.e_jmp(0, ev(a[0]))
            return isa.e_jmp(pick(isa.JMP_CONDS, a[0], "jmp condition"), ev(a[1]))
        if mn == "wait":
            if len(a) < 2:
                raise ValueError("wait needs polarity and source")
            pol, src = ev(a[0]), pick(isa.WAIT_SRCS, a[1], "wait source")
            if src == 3:
                return isa.e_wait(pol, 3, 0)
            idx = ev(a[2])
            flags = {x.lower() for x in a[3:]}
            if src == 2:
                if not 0 <= idx < 8:
                    raise ValueError("event flag must be 0..7")
                idx |= (8 if "rel" in flags else 0) | (16 if "keep" in flags else 0)
            elif flags:
                raise ValueError(f"unexpected '{' '.join(flags)}'")
            return isa.e_wait(pol, src, idx)
        if mn in ("in", "out"):
            if len(a) != 2:
                raise ValueError(f"{mn} needs src/dst and bit count")
            table = isa.IN_SRCS if mn == "in" else isa.OUT_DSTS
            code, n = pick(table, a[0], "operand"), ev(a[1])
            if a[0].lower() == "bit" and n != 1:
                raise ValueError(f"'{mn} bit' moves exactly one bit")
            return (isa.e_in if mn == "in" else isa.e_out)(code, n)
        if mn in ("push", "pull"):
            fl = {x.lower() for x in a}
            cond = ("iffull" if mn == "push" else "ifempty")
            bad = fl - {cond, "block", "noblock"}
            if bad:
                raise ValueError(f"bad {mn} option(s): {', '.join(bad)}")
            blk = 0 if "noblock" in fl else 1
            return (isa.e_push if mn == "push" else isa.e_pull)(int(cond in fl), blk)
        if mn == "evt":
            if len(a) < 2:
                raise ValueError("evt needs op and flag")
            op = pick(isa.EVT_OPS, a[0], "evt op")
            return isa.e_evt(op, ev(a[1]), int(len(a) > 2 and a[2].lower() == "rel"))
        if mn == "sync":
            return isa.e_ctl(0)
        if mn == "crcinit":
            return isa.e_ctl(3)
        if mn == "assist":
            if len(a) < 2 or a[0].lower() not in ("on", "off"):
                raise ValueError("assist on|off crc|stuff|coder")
            mask = 0
            for t in a[1:]:
                if t.lower() not in isa.ASSIST_BITS:
                    raise ValueError(f"unknown assist '{t}'")
                mask |= isa.ASSIST_BITS[t.lower()]
            return isa.e_ctl(1 if a[0].lower() == "on" else 2, mask)
        if mn == "mov":
            if len(a) != 2:
                raise ValueError("mov needs dst, src")
            src, op = a[1], 0
            for pfx, code in (("~::", 3), ("!::", 3), ("::", 2), ("~", 1), ("!", 1)):
                if src.startswith(pfx):
                    src, op = src[len(pfx):], code
                    break
            return isa.e_mov(pick(isa.MOV_DSTS, a[0], "mov destination"), op,
                             pick(isa.MOV_SRCS, src, "mov source"))
        if mn == "match":
            if len(a) != 2:
                raise ValueError("match needs src, n")
            return isa.e_match(pick(isa.MATCH_SRCS, a[0], "match source"), ev(a[1]))
        if mn == "set":
            if len(a) != 2:
                raise ValueError("set needs dst, value")
            return isa.e_set(pick(isa.SET_DSTS, a[0], "set destination"), ev(a[1]))
        raise ValueError(f"unknown instruction '{mn}'")


def assemble(text: str) -> list[Program]:
    a = _Asm()
    a.feed(text)
    if not a.programs:
        raise AsmError("no .program found")
    return a.programs


def assemble_one(text: str) -> Program:
    progs = assemble(text)
    if len(progs) != 1:
        raise AsmError(f"expected one program, found {len(progs)}")
    return progs[0]


# ---- output formats ---------------------------------------------------------

def to_hex(p: Program) -> str:
    return "\n".join(f"{w:04x}" for w in p.code) + "\n"


def to_json(p: Program) -> str:
    return json.dumps({"name": p.name, "code": p.code, "wrap_bottom": p.wrap_bottom,
                       "wrap_top": p.wrap_top, "side_set": {"count": p.side.count, "opt": p.side.opt,
                                                            "pindirs": p.side.pindirs},
                       "config": p.config, "labels": p.labels,
                       "defines": {k: p.defines[k] for k in p.public if k in p.defines}}, indent=2)


def to_c_header(p: Program) -> str:
    n = re.sub(r"\W", "_", p.name)
    cfg = p.engine_config()
    regs = cfg.to_regs()
    g = f"PE_PROG_{n.upper()}_H"
    out = [f"/* Generated by peasm from .program {p.name}. Do not edit. */",
           f"#ifndef {g}", f"#define {g}", "#include <stdint.h>", "",
           f"static const uint16_t {n}_program[{len(p.code)}] = {{"]
    for i, w in enumerate(p.code):
        out.append(f"    0x{w:04x}, /* {i:2d}: {isa.disasm(w, p.side)} */")
    out += ["};", f"#define {n.upper()}_LEN {len(p.code)}u"]
    for k in sorted(p.public):
        if k in p.labels:
            out.append(f"#define {n.upper()}_OFFSET_{k.upper()} {p.labels[k]}u")
        elif k in p.defines:
            out.append(f"#define {n.upper()}_{k.upper()} {p.defines[k]}")
    out.append("/* Default register images (pins, clkdiv and ORIGIN are set by the loader). */")
    for r, v in regs.items():
        out.append(f"#define {n.upper()}_{r} 0x{v:08x}u")
    out += ["", f"#endif /* {g} */", ""]
    return "\n".join(out)
