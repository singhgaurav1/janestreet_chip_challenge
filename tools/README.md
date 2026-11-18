# peemu — Protocol Engine toolchain

Assembler, cycle-accurate golden model and test benches for the Protocol
Engine ISA ([docs/pe_isa.md](../docs/pe_isa.md)). Pure Python, no
dependencies beyond `pytest` for the tests.

## Assemble

```sh
cd tools
python -m peemu asm ../examples/pe/uart.pe            # listing
python -m peemu asm ../examples/pe/uart.pe -f c       # C header for Ibex firmware
python -m peemu asm ../examples/pe/uart.pe -f hex     # $readmemh image
python -m peemu asm ../examples/pe/uart.pe -f json    # code + config + labels
```

Syntax is pioasm-like. Additions: `out bit`/`in bit` (serial assists),
`match`, `evt`, `sync`, `assist on|off crc,stuff,coder`, `crcinit`, `jmp t`,
`wait … jmppin`, `.config FIELD value` for any engine register field, and
`.crc WIDTH POLY [reflect] [init_ones]`.

## Simulate

```python
from peemu import PEBlock, assemble, load_engine

blk = PEBlock()                                   # 4 engines, 64-word shared imem
progs = {p.name: p for p in assemble(open("../examples/pe/uart.pe").read())}
load_engine(blk, 0, progs["uart_tx"], origin=0, own=(16,), out_base=16, side_base=16,
            clkdiv_int=65, clkdiv_frac=27)        # 115200 baud at 60 MHz, 8 ticks/bit
blk.engines[0].tx_push(ord("A"))
for _ in range(6000):
    blk.step()                                    # one 60 MHz cycle
print(blk.uo_out)
```

`PEBlock.state()` returns the architectural state per cycle; the RTL lockstep
bench compares against it.

## Layout

| Path | Contents |
|---|---|
| `peemu/isa.py` | Encodings, decode, disassembler (single source of truth) |
| `peemu/config.py` | Engine config fields and CPU register packing |
| `peemu/asm.py` | Assembler and output formats |
| `peemu/model.py` | Cycle-accurate golden model |
| `peemu/crc.py` | Independent reference CRCs and residues |
| `peemu/randprog.py` | Random legal programs (fuzzing, RTL lockstep stimulus) |
| `tests/vip.py` | Independent UART/SPI/I2C/bitstream models |
| `tests/` | ISA, semantics, protocol and system tests |

## Test

```sh
pip install -r tools/requirements.txt
python -m pytest tools/tests -q
```

| Suite | What it checks | Against |
|---|---|---|
| `test_isa_asm.py` | Every encoding, disassembly round trip, errors, register packing, C header compiles | Hand-computed encodings |
| `test_engine_core.py` | Cycle-exact semantics: latency, delays, divider, stalls, wrap, events, forced instructions, crossbar, open drain, sync, timestamper, assists, CRC catalogue | Hand-derived cycle counts; Rocksoft CRC reference |
| `test_protocols.py` | UART TX/RX (±2.5% baud error), SPI modes 0–3, I2C controller (stretching, Fast-mode timing) and target, NRZI/stuffed/CRC bitstream TX and loopback RX | Independent protocol models in `vip.py` |
| `test_system.py` | All 4 engines concurrently from one memory; resync property under ±4% drift; 60-seed random-program fuzz with invariants | Properties |
