# Protocol Engine (PE) ISA — v0.1

Status: draft for RTL. The Python golden model (`tools/peemu/model.py`) is the
executable form of this document; where the two disagree, fix one of them and
add a test.

## 1. Overview

The chip has four Protocol Engines (PE0–PE3). Each is a cycle-exact sequencer
that executes one 16-bit instruction per **tick**. A tick is an enable pulse
from the engine's fractional clock divider, so engine time is decoupled from
the 60 MHz system clock.

Design rules that shape the ISA:

- **Nothing is protocol-specific.** Every assist (CRC/LFSR, line coder, bit
  stuffer, edge sync, timestamper) is a generic, configurable function that
  is off by default.
- **PIO-familiar.** Programmers who know RP2040 PIO can read PE code. The
  differences are additions (BIT path, MATCH, T flag, timeouts, SYNC,
  relocatable code), not changes to what PIO users expect.
- **Cheap in silicon.** No per-instruction-width surprises: every instruction
  is one tick unless it stalls, and every stall condition is explicit.

### 1.1 Engine state

| State | Width | Notes |
|---|---|---|
| `PC` | 5 | Offset from the engine's `ORIGIN` |
| `X`, `Y` | 32 | Scratch registers. `Y` is also the MATCH comparand |
| `ISR`, `OSR` | 32 | Input / output shift registers |
| `isr_cnt`, `osr_cnt` | 6 | Shift counters, saturate at 32 |
| `T` | 1 | Test flag (MATCH result, WAIT timeout, stuff error) |
| `SERR` | 1 | Sticky stuff-error flag (cleared by the CPU) |
| `delay` | 5 | Remaining delay ticks |
| TX FIFO, RX FIFO | 2 × 32 each | CPU → PE and PE → CPU |
| CRC state | 32 | LFSR register |
| Divider | 16.8 | Integer + fractional counter |

### 1.2 Shared state

| State | Notes |
|---|---|
| Instruction memory | 64 × 16, shared by all engines (one read port per engine) |
| Event flags | 8 bits, shared by all engines and the CPU |
| Pad registers | `pad_out` for 16 driveable pads, `pad_oe` for the 8 bidir pads |
| Pad owner table | Which engine (or the CPU) drives each pad |

## 2. Pin model

### 2.1 Pin index space

All engines see the same 32-entry pin index space:

| Index | Pad | Read | Write |
|---|---|---|---|
| 0–7 | `ui_in[0..7]` | synchronized input | ignored |
| 8–15 | `uio[0..7]` | synchronized input | `pad_out` / `pad_oe` |
| 16–23 | `uo_out[0..7]` | readback of `pad_out` | `pad_out` (OE writes ignored) |
| 24–31 | none | 0 | ignored |

Pin windows (`IN_BASE`, `OUT_BASE`, `SET_BASE`, `SIDE_BASE`) wrap modulo 32,
so `IN PINS, n` reads `n` bits starting at `IN_BASE`, rotated to bit 0.

### 2.2 Ownership (the crossbar)

Each driveable pad (index 8–23) has a 3-bit owner field: `0–3` = PE0–PE3,
`4` = CPU GPIO, `5` = system function (host bridge), `7` = none. A pin write
from an engine that does not own the pad is dropped. **Two engines can never
drive one pad**; this is true by construction and is also a formal property.

Pad registers live at the pad, not in the engines. This saves 96 flops over
per-engine output copies and changes nothing an engine can observe.

### 2.3 Open-drain

Each bidir pad has an `OD` bit. When set, the pad drives low when
`pad_out = 0` and floats when `pad_out = 1` (`oe = ~pad_out`, `out = 0`).
Setting `OD` also sets `pad_out ← 1`, so enabling open drain never glitches
the bus low.
Open-drain buses (I2C, SMBus, 1-Wire) then write data bits straight to
`PINS` with no `PINDIRS` juggling. `uo_out` pads have no output enable, so
`OD` exists only for `uio`.

### 2.4 Input timing

`ui_in` and `uio_in` pass through a shared 2-flop synchronizer. An engine
instruction executing in cycle `c` sees the pad value from cycle `c − 2`.
`uo_out` readback has no synchronizer: it sees `pad_out` as of the end of
cycle `c − 1`.

### 2.5 Output timing

A pin write in cycle `c` updates `pad_out`/`pad_oe` at the end of cycle `c`;
the pad shows the new value during cycle `c + 1`.

### 2.6 Sync pin, filter and edge detector

Each engine has one selected **sync pin** (`JMP_PIN`, any index). It feeds:

- the `JMP PIN` condition and `WAIT … JMPPIN`,
- an optional glitch filter (`FILTER=1`: 3-sample majority at system clock,
  adds one cycle of latency),
- an edge detector used by `SYNC` and `AUTOSYNC` (§5.4).

Filtered value `f(c) = FILTER ? maj(s(c), s(c−1), s(c−2)) : s(c)`.
An edge occurs in cycle `c` when `f(c) ≠ f(c−1)`. `SYNC_EDGE` selects any
edge (0), rising only (1) or falling only (2).

## 3. Instruction format

```
 15  13 12        8 7                0
+------+-----------+------------------+
|  op  | side/dly  |     operands     |
+------+-----------+------------------+
```

Bits `[12:8]` are shared between side-set and delay, as in PIO. With
`SIDE_COUNT = s` (0–5), the top `s` bits are side-set and the rest are delay.
With `SIDE_OPT = 1`, the top side-set bit is an enable, so only `s − 1` pins
are side-set and an instruction without `side` leaves them unchanged.
`SIDE_PINDIR = 1` sends side-set data to output enables instead of levels.

### 3.1 Opcode map

| op | Mnemonic | `[7:5]` | `[4:0]` |
|---|---|---|---|
| `000` | `JMP cond, addr` | cond | addr (offset from `ORIGIN`) |
| `001` | `WAIT pol, src, idx` | `[7]`pol `[6:5]`src | idx |
| `010` | `IN src, n` | src | n (0 = 32) |
| `011` | `OUT dst, n` | dst | n (0 = 32) |
| `100` | `PUSH/PULL/EVT/CTL` | see §3.6 | |
| `101` | `MOV dst, op src` | dst | `[4:3]`op `[2:0]`src |
| `110` | `MATCH src, n` | src | n (0 = 32) |
| `111` | `SET dst, imm` | dst | imm5 |

### 3.2 JMP conditions

| cond | Syntax | Jumps when | Side effect |
|---|---|---|---|
| 0 | *(none)* | always | |
| 1 | `!x` | `X == 0` | |
| 2 | `x--` | `X != 0` | `X ← X − 1` (always) |
| 3 | `!y` | `Y == 0` | |
| 4 | `y--` | `Y != 0` | `Y ← Y − 1` (always) |
| 5 | `pin` | sync pin is 1 | |
| 6 | `!osre` | `osr_cnt < PULL_THRESH` | |
| 7 | `t` | `T == 1` | |

PIO's `x!=y` is replaced by `t` (use `MATCH x, 32` then `JMP t`).

### 3.3 WAIT

`[7]` polarity, `[6:5]` source:

| src | Syntax | Waits for |
|---|---|---|
| 0 | `gpio n` | pin index `n` (absolute) `== pol` |
| 1 | `pin n` | pin index `IN_BASE + n` `== pol` |
| 2 | `evt n [rel] [keep]` | flag `n` `== pol`. `idx[3]` = rel, `idx[4]` = keep |
| 3 | `jmppin` | filtered sync pin `== pol` |

For `evt` with `pol = 1`, the flag is cleared when the wait completes unless
`keep` is set. `rel` adds the engine number to the flag index modulo 4
within its group of 4 (`idx[2] , (idx[1:0] + engine) mod 4`).

**Timeout.** If `TIMEOUT ≠ 0`, a WAIT that has seen its condition false on
`TIMEOUT` consecutive ticks completes anyway with `T ← 1`. A WAIT that
completes normally sets `T ← 0`. With `TIMEOUT = 0`, WAIT never times out and
does not modify `T`.

### 3.4 IN sources and OUT destinations

| code | IN source | OUT destination |
|---|---|---|
| 0 | `pins` (window at `IN_BASE`) | `pins` (window at `OUT_BASE`) |
| 1 | `x` | `x` |
| 2 | `y` | `y` |
| 3 | `null` (zeros) | `null` (discard) |
| 4 | `bit` — one bit from the serial RX path (§5) | `pindirs` |
| 5 | reserved | `pc` |
| 6 | `isr` | `isr` (also sets `isr_cnt ← n`) |
| 7 | `osr` | `bit` — one bit into the serial TX path (§5) |

`IN bit` and `OUT bit` always move exactly one bit; the assembler requires
`n = 1`.

Shift rules (`n` = 1–32):

- `OUT`, shift left (`OUT_SHIFTDIR=0`, MSB first): data = `OSR[31:32−n]`, `OSR ← OSR << n`.
- `OUT`, shift right (LSB first): data = `OSR[n−1:0]`, `OSR ← OSR >> n`.
- `IN`, shift left: `ISR ← (ISR << n) | data[n−1:0]`.
- `IN`, shift right: `ISR ← (ISR >> n) | (data[n−1:0] << (32 − n))`.
- `osr_cnt`/`isr_cnt` increase by `n`, saturating at 32.

**Autopull** (`AUTOPULL=1`): when an `OUT` issues with
`osr_cnt ≥ PULL_THRESH`, it pops the TX FIFO into `OSR`, clears `osr_cnt`
and shifts from the new `OSR` in the same tick. If the FIFO is empty the
`OUT` stalls.

**Autopush** (`AUTOPUSH=1`): an `IN` whose shift would bring `isr_cnt` to
`≥ PUSH_THRESH` stalls if the RX FIFO is full; otherwise it shifts, pushes
`ISR`, and clears `ISR` and `isr_cnt` in the same tick.

### 3.5 MOV

Destinations: `0 pins`, `1 x`, `2 y`, `3 pindirs`, `4 pc`, `5 isr`
(clears `isr_cnt`), `6 osr` (clears `osr_cnt`), `7 crc` (loads the CRC state,
masked to `CRC_WIDTH`).

Sources: `0 pins` (32 bits rotated by `IN_BASE`), `1 x`, `2 y`, `3 null`,
`4 status`, `5 isr`, `6 osr`, `7 crc` (raw CRC state).

Operations `[4:3]`: `0` none, `1` invert (`~`), `2` bit-reverse (`::`),
`3` invert then reverse (`~::`).

`status` = `{20'b0, rx_level[3:0], tx_level[3:0], rxf_full, txf_empty, SERR, T}`.

`nop` assembles to `mov y, y`.

### 3.6 Opcode 100 sub-operations

| `[7:6]` | Syntax | `[5]` | `[4]` | `[3:0]` |
|---|---|---|---|---|
| `00` | `push [iffull] [block\|noblock]` | iffull | block | 0 |
| `01` | `pull [ifempty] [block\|noblock]` | ifempty | block | 0 |
| `10` | `evt set\|clr\|setwait n [rel]` | `[5:4]` op: 0 set, 1 clr, 2 setwait | | `[3]` rel, `[2:0]` n |
| `11` | control, see below | `[5:4]` sub | | `[2:0]` mask |

Control sub-ops (`[5:4]`):

| sub | Syntax | Effect |
|---|---|---|
| 0 | `sync` | Arm a one-shot hard sync (frame start) of the divider (§5.4) |
| 1 | `assist on <list>` | Enable assists in `mask`: bit0 `crc`, bit1 `stuff`, bit2 `coder` |
| 2 | `assist off <list>` | Disable assists in `mask` |
| 3 | `crcinit` | CRC state ← all ones if `CRC_INIT_ONES` else 0 |

`assist on stuff` also resets the run counter. `assist on coder` resets the
TX line level and the RX previous level to `CODER_IDLE`. `assist on crc` only
gates feeding; use `crcinit` or `mov crc, …` to set the state.

PUSH/PULL follow PIO: blocking `PULL` stalls on an empty FIFO; non-blocking
`PULL` on an empty FIFO copies `X` to `OSR`. Blocking `PUSH` stalls on a full
FIFO; non-blocking `PUSH` on a full FIFO drops the data. Both clear the
register they move. `iffull`/`ifempty` make the operation a no-op unless the
shift count has reached its threshold.

`evt setwait` sets the flag on its first issue, then stalls until the flag is
clear.

### 3.7 MATCH

`MATCH src, n` sets `T ← (src[n−1:0] == Y[n−1:0])`. For `isr` with
`IN_SHIFTDIR = right`, the newest `n` bits sit at the top, so the compare uses
`ISR[31:32−n]` against `Y[31:32−n]` (keep the pattern left-aligned in `Y`).
The mask is a thermometer code from `n`; no shifter is needed. Sources: `0 isr`,
`1 osr`, `2 x`, `3 crc` (raw state), `4 pins` (rotated by `IN_BASE`);
5–7 reserved. Typical uses: target address match, command decode, CRC
residue check — with no CPU in the loop.

### 3.8 SET

Destinations: `0 pins`, `1 x`, `2 y`, `4 pindirs` (window at `SET_BASE`,
`SET_COUNT` pins). `X`/`Y` are zero-extended.

## 4. Execution timing

Per system clock cycle, each engine does the following, in order. All engines
read the same start-of-cycle snapshot of pins, event flags and FIFO levels,
so the result does not depend on engine order.

1. **Filter/edge.** Compute the filtered sync pin and edge (§2.6).
2. **Divider.** If the engine is enabled: if an armed hard sync sees a
   qualifying edge, snap the divider (§5.4), which may produce a tick.
   Otherwise step the divider; it may produce a tick.
3. **Forced instruction.** If the CPU wrote `INSTR`, execute it now (whether
   or not there is a tick or the engine is enabled). It ignores its delay
   field. If it stalls it is retried each cycle until it completes.
4. **Tick.** On a tick (and no forced instruction this cycle):
   1. Advance the Manchester half-bit timer (§5.2).
   2. If `delay > 0`: `delay ← delay − 1`. Done.
   3. Otherwise fetch `imem[(ORIGIN + PC) mod 64]` and issue it.
5. **Issue.** Side-set is applied every time the instruction issues, even if
   it stalls. Then:
   - **Stall:** `PC` unchanged, no delay is loaded.
   - **Re-execute** (stuff bit handled, §5.3): `PC` unchanged,
     `delay ← BIT_TICKS − 1`.
   - **Complete:** `PC ← target` if it jumped, else
     `PC ← (PC == WRAP_TOP) ? WRAP_BOTTOM : PC + 1`; `delay ← delay field`.
6. **Commit.** Pin writes (side-set overrides OUT/SET/MOV on the same pin),
   event flags (`flags ← (flags & ~clears) | sets`, so set wins), FIFO
   pushes/pops.

**Relocatable code.** `PC` and all jump targets are 5-bit offsets from
`ORIGIN` (6-bit, set per engine). A program can be loaded anywhere in the
64-word memory without patching, and several engines can share one program
(e.g. two UART receivers) by sharing an origin.

## 5. Serial assists (the BIT path)

`OUT bit` and `IN bit` move one bit between a shift register and the pin at
`OUT_BASE` / `IN_BASE` through a fixed chain of optional stages. `OUT pins`
and `IN pins` never pass through the assists.

```
TX:  OSR ─▶ CRC feed ─▶ stuffer ─▶ line coder ─▶ pin OUT_BASE (+1 if DIFF)
RX:  pin IN_BASE ─▶ line decoder ─▶ destuffer ─▶ CRC feed ─▶ ISR
```

`BIT_TICKS` (1–16) tells the stuffer and the Manchester coder how many ticks
one bit lasts in the running program. A bit loop must take exactly
`BIT_TICKS` ticks per bit.

### 5.1 CRC/LFSR

Bit-serial LFSR fed in wire order, `W = CRC_WIDTH` (1–32):

- `CRC_REFLECT = 0`: `fb = bit ^ state[W−1]; state ← (state << 1) & mask; if fb: state ^= POLY`.
- `CRC_REFLECT = 1`: `fb = bit ^ state[0]; state ← state >> 1; if fb: state ^= POLY`,
  with `CRC_POLY` holding the bit-reversed polynomial (the assembler and HAL
  compute it).

Feeding wire-order bits makes "reflected input" CRCs (USB, Ethernet) work
with no input reflection. `CRC_REFLECT` only changes how the state is laid
out: with it set, `state[0]` is the coefficient that goes on the wire first,
so a right-shifting OSR appends the CRC with a single `mov osr, crc` (or
`~crc`). Bitstream programs therefore pack frames in wire order, LSB first.

There is no init or xor-out register: `crcinit` covers the all-zeros and
all-ones cases, `mov crc, x` loads any other initial value, and `mov osr, ~crc`
/ `mov osr, ::crc` give xor-out and output reflection at transmit time. This
saves 64 flops per engine.

Residue checks use `match crc, W` with the expected residue in `Y`.
`peemu.crc.residue()` computes residues for a given configuration.

### 5.2 Line coder

`CODER_MODE`: `0` NRZ, `1` NRZI (transition on 0, as in USB), `2` NRZI
(transition on 1), `3` Manchester (IEEE 802.3: first half `~b`, second half
`b`). `CODER_DIFF=1` drives `OUT_BASE+1` with the complement, for differential
pairs. `CODER_IDLE` is the line level after `assist on coder`.

TX Manchester drives `~b` on the `OUT bit` tick and `b` exactly
`BIT_TICKS / 2` ticks later (rounded down).

RX decoding: NRZ and Manchester return the sampled level (sample Manchester
in the second half-bit); NRZI mode 1 returns `1` when the level did not change
since the previous `IN bit` sample, mode 2 the opposite.

When the coder assist is off, the coder behaves as NRZ.

### 5.3 Bit stuffer / destuffer

`STUFF_N` (1–8) and `STUFF_MODE`: `0` any value (a run of N identical bits,
as in CAN), `1` ones only (as in USB, counted before NRZI), `2` zeros only.

- **TX:** when `N` matching bits have been sent, the next `OUT bit` sends the
  complement instead, does not consume `OSR` or feed the CRC, and is
  re-executed `BIT_TICKS` ticks later.
- **RX:** when `N` matching bits have been received, the next `IN bit` sample
  is a stuff bit. If it is the complement it is dropped and the `IN` is
  re-executed `BIT_TICKS` ticks later. If not, it is a **stuff error**:
  `T ← 1`, `SERR ← 1`, the bit is delivered as data, and the run counter
  restarts from zero.

### 5.4 Clock recovery (`SYNC`, `AUTOSYNC`)

The divider counts down; it ticks when the counter is 0, then reloads with
`INT − 1 + carry` where `carry` comes from an 8-bit accumulator adding `FRAC`.
`INT = 0` means 65536. Average tick period: `INT + FRAC/256` cycles.

Two mechanisms, matching CAN's hard synchronisation and resynchronisation:

**Hard sync (frame start).** `sync` arms a one-shot. On the next qualifying
edge (cycle `e`) the counter is loaded with `SYNC_PHASE` and the accumulator
cleared; cycle `e` produces no tick, so the next tick is at
`e + 1 + SYNC_PHASE`. Programs arm it while the line is idle (e.g. during a
stop bit) and then `wait` for the start edge, which completes exactly on that
tick.

**Resync (in frame, `AUTOSYNC=1`).** Every other qualifying edge re-phases the
tick grid so that a tick lands at `e + 1 + SYNC_PHASE`, *without adding or
dropping ticks*. Let `D = INT` and `d = cnt − (SYNC_PHASE + 1)`,
the position of the pending tick relative to that new grid point:

| Condition | Meaning | Action |
|---|---|---|
| `2d < −D` | pending tick belongs to the previous slot (receiver late) | tick in cycle `e`; `cnt ← SYNC_PHASE` |
| `−D ≤ 2d < D` | same slot | no tick in `e`; `cnt ← SYNC_PHASE` |
| `2d ≥ D` | the last tick already served this slot (receiver early) | no tick in `e`; `cnt ← SYNC_PHASE + D` |

The accumulator is cleared. `SYNC_PHASE` must be less than `INT`. This is
nearest-slot phase snapping: a program's tick count per bit is preserved even
when the sender's clock is fast or slow, as long as the accumulated drift
between edges stays under half a tick. An armed hard sync takes precedence.
Clock recovery for UART, CAN and USB needs no protocol logic. Cost: one
17-bit subtract and two compares per engine.

### 5.5 Edge timestamper

With `STAMP_EN=1`, the engine compares the `STAMP_COUNT` pins at `IN_BASE`
every cycle. On any change it pushes `{delta[15:0], pins[15:0]}` to the RX
FIFO, where `delta` counts cycles since the previous record. If `delta`
saturates at `0xFFFF`, a record is pushed anyway so long gaps stay
measurable. If the FIFO is full, or the program pushed to it in the same
cycle (the FIFO has one write port), the record is dropped and `OVF` is set.
The program may keep running but should not push. This is the on-chip logic
analyzer and the capture path for unknown protocols.

## 6. CPU register map (draft)

Per engine `n`, base `0x100 * n` within the PE block. Field packing is a
draft; RTL may refine it, the semantics above are fixed.

| Offset | Register | Fields |
|---|---|---|
| `0x00` | `CTRL` | `[0]` EN, `[1]` RESTART (W1, clears counters, `PC←0`), `[2]` DIV_RESTART (W1) |
| `0x04` | `CLKDIV` | `[31:16]` INT, `[15:8]` FRAC |
| `0x08` | `EXECCTRL` | `[4:0]` WRAP_BOTTOM, `[9:5]` WRAP_TOP, `[14:10]` JMP_PIN, `[15]` FILTER, `[17:16]` SYNC_EDGE, `[18]` AUTOSYNC, `[21:19]` SIDE_COUNT, `[22]` SIDE_OPT, `[23]` SIDE_PINDIR, `[29:24]` ORIGIN |
| `0x0C` | `SHIFTCTRL` | `[0]` IN_SHIFTDIR (1 = right), `[1]` OUT_SHIFTDIR, `[2]` AUTOPUSH, `[3]` AUTOPULL, `[8:4]` PUSH_THRESH (0 = 32), `[13:9]` PULL_THRESH (0 = 32) |
| `0x10` | `PINCTRL` | `[4:0]` OUT_BASE, `[10:5]` OUT_COUNT (0–32), `[15:11]` SET_BASE, `[18:16]` SET_COUNT, `[23:19]` SIDE_BASE, `[28:24]` IN_BASE |
| `0x14` | `ASSIST` | `[1:0]` CODER_MODE, `[2]` CODER_DIFF, `[3]` CODER_IDLE, `[5:4]` STUFF_MODE, `[8:6]` STUFF_N−1, `[12:9]` BIT_TICKS−1, `[17:13]` CRC_WIDTH−1, `[18]` CRC_INIT_ONES, `[19]` STAMP_EN, `[23:20]` STAMP_COUNT−1, `[24]` CRC_REFLECT |
| `0x18` | `CRC_POLY` | `[31:0]` |
| `0x1C` | `TIMING` | `[15:0]` TIMEOUT (ticks, 0 = off), `[31:16]` SYNC_PHASE |
| `0x20` | `INSTR` | W: force-execute; R: current instruction |
| `0x24` | `TXF` | W: push TX FIFO |
| `0x28` | `RXF` | R: pop RX FIFO |
| `0x2C` | `STATUS` | `[4:0]` PC, `[5]` T, `[6]` SERR (W1C), `[7]` OVF (W1C), `[11:8]` TX level, `[15:12]` RX level, `[16]` stalled |

Shared, base `0x400`: `IMEM[0..63]` (16-bit, write-only while the engines
using it are disabled), `EVT` (R/W, plus W1S/W1C aliases), `IRQ_EN`,
`PAD_OWNER[8..23]` (3 bits each), `PAD_OD[8..15]`, CPU GPIO `OUT`/`OE`.

## 7. Differences from RP2040 PIO

| Area | PIO | PE | Why |
|---|---|---|---|
| Program memory | 32 words, absolute addresses | 64 words, 5-bit offsets from a per-engine `ORIGIN` | Room for UART + SPI + I2C both roles at once; relocatable code |
| Serial assists | none | `IN/OUT bit` through CRC, stuffer, line coder | USB, CAN, Ethernet framing with no protocol logic |
| Tests | `x!=y`, status compare | `MATCH src, n` against `Y` → `T`, `JMP t` | Address/command/residue match without the CPU |
| WAIT | no timeout | `TIMEOUT` ticks → `T` | I2C clock stretching and bus-idle detection cannot hang |
| WAIT sources | gpio, pin, irq | adds `jmppin` (filtered) | Glitch-filtered waits |
| Clock | fractional divider | adds hard sync on edges | Clock recovery for async protocols |
| IRQ | 8 flags | 8 event flags, plus `setwait` | Same model; CPU sees them as interrupts |
| Open drain | via pindirs tricks | per-pad OD mode | Simpler, shorter I2C code |
| Outputs | OR of all SMs | per-pad owner | No contention possible |
| `mov exec`, `out exec` | yes | no | Saves decode area; CPU can force instructions |

## 8. Decisions vs. the architecture plan

These refine the plan; none changes a fixed decision.

1. **Instruction memory is 64 × 16, not 32 × 16.** 32 words cannot hold
   UART, SPI and I2C in both roles concurrently (the plan's headline target).
   Jump targets stay 5 bits by making them relative to a per-engine `ORIGIN`.
   Area lever if needed: 48 words.
2. **Side-set/delay split is configurable (0–5 side bits), as in PIO**, rather
   than fixed at 2 side + 3 delay. Cost is a few muxes; UART and SPI need
   longer delays with 1 side pin.
3. **CRC has no init or xor-out registers.** Covered by `crcinit`,
   `mov crc, x` and `mov` invert; saves 64 flops per engine. A 1-bit
   `CRC_REFLECT` (right-shifting LFSR) makes CRC append a single `mov`.
4. **MATCH compares against `Y` over the low `n` bits** instead of a separate
   mask register; saves 64 flops per engine.
5. **Output pad registers live at the pads with an owner table**, instead of
   per-engine output registers.
6. **Two kinds of clock sync.** The plan had one `SYNC`. Testing showed a
   plain divider reload drops a tick when the sender is fast, so in-frame
   tracking (`AUTOSYNC`) uses nearest-slot snapping, and the `sync`
   instruction is the frame-start hard sync.
7. **Enabling open drain releases the pad**, so the bus is never pulled low
   by the reset value of `pad_out`.

## 9. Verification status

Executable spec: `tools/peemu/model.py`. Examples: `examples/pe/`. Tests:
`python -m pytest tools/tests` (242 tests). Every example program is checked
against an independent protocol model (`tools/tests/vip.py`); engine
semantics are checked against hand-derived cycle counts; a 60-seed random
program fuzz checks structural invariants. RTL lockstep against
`PEBlock.state()` is next.
