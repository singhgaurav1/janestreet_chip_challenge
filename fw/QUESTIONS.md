# Firmware questions and findings for the architecting chat

From the firmware chat, 2026-11-18. Updated in the evening after running
firmware on the SoC skeleton (`soc-skeleton`, `docs/soc.md`); status per
item below. Firmware is written from
`docs/pe_isa.md` and the Ibex docs only; these are the places where those
specs leave a decision open, or where firmware results bear on the
architecture. Firmware works around each one today (marked **Now**), but
several are hardware behaviours that cannot be patched after tapeout.

## Status (2026-11-21, after PR #5: register map v1, bus errors, boot guard)

| Item | Status |
|---|---|
| A1 SRAM size | **Resolved:** 4 KiB (PR #3) |
| A2 bus errors, A13 above SRAM, A14 unused sysctrl offsets | **Resolved** in `docs/soc.md`; chip tests `loadfault`, `fetchfault`, `beyond_ram` |
| A3 PE interrupts | **Resolved:** flag n → ID 16+n, engine FIFO IRQs → 24–27; HAL and host tests |
| A4 FIFO edge cases | **Resolved:** sticky TXOVF/RXUNF; chip test `pehal_fifo` (raw overflow, underflow, clear) |
| A5 shared offsets, owner NONE | **Resolved:** `pe_isa.md` 6.2; chip test `pehal_pads` |
| A6 reset values | **Resolved**, but the two specs disagree on pad owners: see A17 |
| A7 CTRL/RESTART, A8 forced instruction | **Resolved:** FORCE_PENDING; chip test `pehal_fifo` (forced blocking push stays pending until a pop) |
| A9 ENGINE_CTRL, A10 SAFE_STATE | **Resolved:** HAL uses both; chip tests `pehal_engines` (two engines start in one write) and `pehal_pads` (panic releases pads and stops engines, read back over the bridge) |
| A11a reload before run | **Resolved:** boot guard; chip tests `restart_refused*`, `restart_*_reload` |
| A12, A15, A16 | Closed (host heartbeat; `fw/` result convention adopted; shared build infrastructure) |
| B1–B3 bridge frame rules | **Resolved:** `docs/soc.md` "Frame rules"; checked by `bridge_while_running` |
| **A17 (new) reset pad owner** | Open, docs only, see below |
| **A18 (new) output pins low until boot** | Open, needs an owner/architecture decision, see below |

**A17 Reset pad owner: the specs disagree.** `docs/pe_isa.md` 6.2 "Reset
values" says every pad owner resets to NONE except pad 23. `docs/soc.md`
says owner 5 (system) is the reset value for all pads 8–23, and the chip
does that (`pehal_fifo` check 3 reads all 16). The firmware follows
`soc.md`. *Please align `pe_isa.md` 6.2* (e.g. "every pad owner resets to
the chip's choice; on this chip all are system"). Safety note: with every
pad on the system at reset, `SAFE_STATE` does not release them; the pins
stay safe only because the runtime also clears the sysctrl outputs
(`fw_platform_safe_state`). Both steps are needed, and `pin_panic` /
`pehal_pads` check the pins after a panic.

**A18 Output pins are low from reset until the firmware runs.** Every
`uo_out` pin resets to 0 (sysctrl `GPIO_OUT` = 0, all pads owned by the
system), and `uo_out` has no output enable. A UART TX on `uo_out` idles
*high*, so from power-up through the whole image load (~2–7 ms at the
bridge's SCK) the far end sees a break, and may read a `0x00` byte or a
framing error. The F4 UART test hit exactly this (the independent UART model
read a `0x00`) and now attaches only once the firmware drives the line high.
I2C/open drain on `uio` is not affected (OE off, the external pull-up wins).
*Options:* (a) document it ("TX on `uo_out` shows a break until boot; put
UART TX on a `uio` pad with a pull-up, which idles high with OE off"),
(b) reset sysctrl `GPIO_OUT` to all ones (the `chip`/`pin_panic` tests and
the safe state assume 0, so the safe state would need the same choice), or
(c) a reset-value strap. Firmware can only do (a) on its side; I recommend
(a) now and deciding (b) with the safe-state definition.

## A. Decisions needed (hardware, before RTL freeze)

**A1. Firmware SRAM size: 2 KiB is not viable; 4 KiB is tight.**
**Status: open and now blocking.** The SoC was built with the 2 KiB macro.
On it, runtime-only firmware runs (all chip tests pass), but runtime plus PE
HAL is ~3.1 KB, so **no protocol firmware can run on the chip as built**.
`docs/area.md` shows the PE block over budget, so this needs deciding
together with the area options there. For example, Ibex `micro` (−0.11 mm²)
would more than pay for the 4 KiB macro (+0.06 mm²). Firmware already builds
and passes on micro (rv32ec).

Measured, rv32imc, `-Os`: runtime ~1.16 KiB, PE HAL ~1.75 KiB, status block
plus panic stack 192 B, minimum stack 512 B. With 2 KiB the runtime alone
leaves ~50 B. With 4 KiB about 350 B are left for protocol drivers and PE
program arrays. The plan's area table assumes the 2 KiB macro.
*Recommend:* the 1024×32 macro as the minimum. Consider 2×1024×32
(8 KiB, +0.14 mm²) if area allows, because the sign-off requires real
firmware drivers for six protocols. *Now:* the linker fails the build if
code plus minimum stack exceed RAM; CI prints sizes.

**A2. Bus errors for unmapped addresses.** **Status: resolved in
`docs/soc.md` and verified on the chip** (unmapped load and fetch take access
faults that the firmware records). Please make the interconnect
answer accesses outside RAM and registers with `data_err_i`/`instr_err_i`.
Ibex then takes an access fault, which the firmware turns into a recorded
panic and safe state (tested: load, fetch). Returning garbage would let a
wild pointer run on silently.

**A3. PE interrupt wiring.** The spec has `IRQ_EN` but does not say which
Ibex line(s) it drives or what can interrupt. *Proposal:* event flag n →
`irq_fast_i[n]` (IDs 16–23), plus one line per engine for FIFO level (RX not
empty or TX not full, maskable) on IDs 24–27. All level-sensitive and cleared
at the source. *Now:* `platform.h` assumes flag n → ID 16+n.

**A4. FIFO edge cases.** What happens on a `TXF` write when full and an
`RXF` read when empty? *Proposal:* the write is dropped and the read returns
0, each setting a sticky error bit in `STATUS`. *Now:* the HAL checks the
level before every access and never does either.

**A5. Shared-block registers: offsets, packing, readback.** §6 lists
`IMEM`, `EVT`, `IRQ_EN`, `PAD_OWNER`, `PAD_OD` and GPIO but gives no
offsets. Safety point: if several pads' owner fields share one register that
is not readable, changing one pad means rewriting the others blind, which
could take the host-bridge pins. *Proposal:* one owner register per pad (or
readable registers), and set/clear aliases for `PAD_OD`, `GPIO_OUT` and
`GPIO_OE`. Please also define what CPU GPIO writes do for a pad the CPU does
not own (proposal: dropped, as for engines) and what a pad with owner `NONE`
does (proposal: OE off). *Now:* stub offsets in `pe_regs.h`, one owner
register per pad, shadow copies for the bitmask registers.

**A6. Reset values.** The spec gives none for the engine or pad registers.
The pins' state between power-up and firmware start matters on a real bus.
*Proposal:* engines disabled, every pad owner `NONE` with OE off (except the
host-bridge pins), event flags and `IRQ_EN` zero. *Now:* `pe_hal_init()`
writes a full known state; the HAL always writes complete configuration
images.

**A7. `CTRL` semantics.** (a) Is `EN|RESTART` in one write defined, and in
which order? (b) Exactly what does `RESTART` clear: FIFOs, ISR/OSR and
counters, `T`, `delay`, CRC state, coder level? *Now:* the HAL writes
`RESTART|DIV_RESTART` with EN=0, then EN.

**A8. Forced instruction (`INSTR`).** How does the CPU know a forced
instruction has completed? Does `STATUS.stalled` refer to it? What happens
if `INSTR` is written while the previous one is still stalled? *Now:*
`pe_exec()` polls `STATUS.stalled` with a timeout.

**A9. Starting several engines at once.** Per-engine `CTRL` cannot start
two engines on the same cycle (the CPU needs one write each). *Proposal:* a
shared `ENABLE` mask and `DIV_RESTART` mask, as in PIO's `CTRL`.
*Workaround:* programs wait on an event flag that the CPU sets after
enabling all of them.

**A10. One-write hardware safe state (cheap, recommended).** A shared
register whose write disables all engines and releases every pad not owned
by the system. After a crash, a single store is more robust than the
firmware's ~10-write sequence. *Now:* `pe_safe_state()` does the sequence;
its exact order is tested on the core.

**A11. Boot path (F3) requirements from firmware.** **Status:** (c) and
(d) are resolved by `docs/soc.md` and verified on the chip. (a) is still
open: `CTRL.run` 0→1 restarts the CPU without a reload; the firmware detects
and refuses this (tested on the chip), but the RP2350 loader must always
reload. (b): the loader model reads the image back and verifies it, so the
real RP2350 loader should do the same.
(a) Every reset release must follow a fresh image load. The firmware
detects a restart without reload and refuses to run (tested), but the bridge
should not allow it. (b) A load shorter than ~1 KiB is not detectable by
firmware, because its own checker would be missing. The bridge or host must
check length (and ideally CRC). (c) The host must be able to read RAM (the
status block at the top) while Ibex runs or after it halts. (d) Boot address
must be 256-byte aligned (Ibex `mtvec`); firmware assumes RAM at
`0x0000_0000` = boot address.

**A12. `irq_nm_i` and a watchdog.** Is the NMI tied off? There is no
watchdog: a hung firmware keeps its pins as they are. *Proposal:* the
RP2350 loader watches `fw_status.heartbeat` over the bridge and resets the
chip when it stops. That is host-side, so nothing in RTL.

**A13. SRAM aliasing (new).** `docs/soc.md`: the SRAM aliases above
`0x7FF` across `0x0000_0000`–`0x0FFF_FFFF`. A wild pointer or stack
underflow past the end of RAM then silently reads and writes real memory
instead of faulting. *Proposal:* decode errors above the SRAM size (cheap: a
comparison on the high address bits), as for other unmapped addresses.

**A14. sysctrl window.** What do reads and writes to unused offsets in the
4 KiB sysctrl window do? *Proposal:* bus error, as above.

**A15. One result convention (new).** The RTL chat's `firmware/` writes
main's raw return value to SCRATCH and `0xDEAD0000 | mcause` on a trap. `fw/`
uses the riscv-tests convention (1 = pass, `(n << 1) | 1` = fail) plus a
status block at the top of RAM. The RP2350 loader needs one convention.
*Proposal:* the `fw/` one, which riscv-tests also use, documented in
`docs/soc.md`.

**A16. Two firmware trees (new, for the owner).** The brief gives `fw/` to
the firmware chat, but `soc-skeleton` adds a second tree, `firmware/` (its own
crt0, linker script, Zig clang). CLAUDE.md now lists "firmware drivers
validating every protocol" under `firmware/`. *Proposal:* keep
`firmware/tests/smoke.c` as the RTL chat's hardware smoke test (it is useful
as an independent check), and do protocol firmware only in `fw/`, so the
independence rule holds.

## B. Spec and model notes (small)

- `tools/peemu/config.py` `validate()` does not enforce `SYNC_PHASE < INT`
  (§5.4) or "`SIDE_OPT` needs `SIDE_COUNT ≥ 1`". The HAL does. Otherwise
  the C HAL and the model pack 3,000 random configurations identically.
- `PINCTRL.SET_COUNT` is 3 bits but only 0–5 are legal (`SET` has a 5-bit
  immediate). Fine, but worth one line in §6.
- FIFOs are now 2 deep (`pe_isa.md` 1.1); `STATUS` levels are still 4 bits.
  The HAL treats any level ≥ 2 as full, so a 4-bit level that ever reads
  2–15 is never written into.
- **B1 Aborted WRITE (observed, unspecified).** A WRITE frame cut after 2
  of 4 data bytes leaves memory unchanged. *Please specify:* "a partial word
  is discarded" (what the RTL does).
- **B2 Unaligned WRITE/READ address (observed, unspecified).** A WRITE to
  base + 2 lands on the aligned word. *Please specify:* "`addr[1:0]` are
  ignored", or that the host must align.
- **B3 Unknown commands (observed, unspecified).** Frames starting with
  `0x00` or `0xFF` are ignored and the next frame works. *Please specify.*

## C. For the RTL chat (not hardware bugs)

- **Verilator needs `--x-initial-edge` for full-chip simulation.** On the
  SoC, with random power-up state, ~24% of seeds hung at boot (minimal
  probe, seeds 20–69). The image was intact and the CPU was running, but no
  code ran; one `CTRL.run` 0→1 cycle fixed every failing seed. Cause: the
  internal CPU reset can power up low, so in two-state simulation it never
  falls and Ibex's asynchronous-reset flops (behind the gated clock) are
  never reset. Event-driven simulators (Icarus, used by cocotb here) see X→0
  as an edge, and silicon resets on the level, so this is a simulation
  artefact. With `--x-initial-edge`, 60/60 seeds pass. The plan's F4
  "Verilator full-chip sim" must use it.

- **Simulation reset.** Ibex flops use asynchronous reset behind a gated
  core clock. A testbench that holds `rst_n` low from time 0 never produces
  a `negedge`, so those flops keep their initial simulator value. In the
  firmware testbench the hart started in **U-mode** and every CSR access
  trapped. `test/ibex_smoke/tb_ibex_smoke.v` starts with `rst_n = 0` too; it
  passes only because its program uses no CSRs. cocotb and gate-level
  benches should drive a real reset edge and ideally randomise initial
  state (`test/fw` does both).
- **Extra evidence for the Ibex conversion.** The same 67 riscv-tests give
  identical results and identical cycle counts on `src/ibex_small.v` and on
  upstream Ibex SV under Verilator (an independent frontend from
  yosys-slang). This also holds with 30% random bus stalls and single-port
  arbitration.
- **`regs.h` naming for F1.** Proposed in `fw/include/pe_regs.h`:
  `PE_<REG>_OFF`, `PE_<REG>_<FIELD>_LSB/_WIDTH`, `PE_SH_<REG>_OFF`. If the
  generator uses these names, switching the HAL is a one-line include
  change.

No `docs/issues/` entries: no mismatch between spec and hardware has been
found. Tested against `docs/soc.md`: the host SPI protocol (ID, STATUS, CTRL,
WRITE, READ with auto-increment, at 10 MHz and slower), the memory map,
sysctrl ID/GPIO/CYCLE/SCRATCH, SRAM aliasing, and bus errors. All match the
spec.
