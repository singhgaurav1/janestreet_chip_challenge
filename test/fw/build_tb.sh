#!/usr/bin/env bash
# Build the standalone-Ibex firmware testbench with Verilator.
#   test/fw/build_tb.sh [conv|ref|ref_micro]
#     conv (default): converted core src/ibex_small.v  -> test/fw/obj/Vtb
#     ref:            upstream Ibex SystemVerilog (deps/ibex, same parameters,
#                     via ibex_ref_wrapper.sv)          -> test/fw/obj/Vtb_ref
#     ref_micro:      upstream Ibex, "micro" parameters (RV32EC) for the
#                     fallback-core firmware build      -> test/fw/obj/Vtb_ref_micro
#     chip:           the full chip (tt_um top, RTL source list taken from
#                     test/Makefile PROJECT_SOURCES, IHP SRAM behavioural
#                     model) with the RP2350 loader model, tb_chip_fw.sv
#                                                       -> test/fw/obj/Vtb_chip
# Both are built with --x-initial unique, so runs should pass
# +verilator+rand+reset+2 to randomise all uninitialised state.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
WHICH=${1:-conv}
OBJ=$HERE/obj
mkdir -p "$OBJ"

case $WHICH in
  conv)
    # pemu_cells.v: technology wrappers (behavioural unless PROTOEMU_ASIC)
    CORE=("$ROOT/src/ibex_small.v" "$ROOT/src/pemu_cells.v"); EXE=Vtb; DEFS=() ;;
  ref|ref_micro)
    P=$ROOT/deps/ibex/vendor/lowrisc_ip/ip
    [ -d "$ROOT/deps/ibex/rtl" ] || { echo "deps/ibex missing: run scripts/fetch_deps.sh"; exit 1; }
    DEFS=(-DSYNTHESIS -I"$P/prim/rtl" -I"$ROOT/deps/ibex/vendor/lowrisc_ip/dv/sv/dv_utils"
          -y "$ROOT/deps/ibex/rtl" -y "$P/prim/rtl" -y "$P/prim_generic/rtl")
    CORE=("$P/prim/rtl/prim_util_pkg.sv" "$P/prim/rtl/prim_mubi_pkg.sv" "$P/prim/rtl/prim_count_pkg.sv"
          "$P/prim/rtl/prim_cipher_pkg.sv" "$P/prim/rtl/prim_secded_pkg.sv"
          "$P/prim_generic/rtl/prim_ram_1p_pkg.sv" "$ROOT/deps/ibex/rtl/ibex_pkg.sv"
          "$ROOT/deps/ibex/rtl/ibex_cheriot_pkg.sv" "$HERE/ibex_ref_wrapper.sv")
    EXE=Vtb_$WHICH
    [ "$WHICH" = ref_micro ] && DEFS+=(-DIBEX_MICRO) ;;
  chip)
    # RTL sources and SRAM model exactly as the RTL chat's cocotb tests list
    # them in test/Makefile, so new blocks or a new macro are picked up.
    mkvar() {  # print a variable from test/Makefile, joining continuation lines
      sed -n "/^$1 *=/,/[^\\]\$/p" "$ROOT/test/Makefile" | sed "s/^$1 *=//; s/\\\\//g"
    }
    CORE=()
    for f in $(mkvar PROJECT_SOURCES); do CORE+=("$ROOT/src/$f"); done
    for f in $(mkvar SRAM_MODEL); do CORE+=("${f/\$(PWD)/$ROOT/test}"); done
    [ ${#CORE[@]} -gt 3 ] || { echo "could not read PROJECT_SOURCES/SRAM_MODEL from test/Makefile"; exit 1; }
    DEFS=(-DSIM -DFUNCTIONAL -I"$ROOT/src")
    TOP=tb_chip_fw; TBSRC=$HERE/tb_chip_fw.sv; EXE=Vtb_chip
    # Emulate the X->0 / X->1 edges of event-driven simulators. Internal reset
    # nets (the CPU reset behind CTRL.run) can power up low; in two-state
    # simulation they then never fall, Ibex's asynchronous-reset flops behind
    # its gated clock are never reset, and ~24% of random power-up states hang
    # at boot (measured, seeds 20-69). Silicon resets on the level, so this is
    # a simulation artefact; see fw/QUESTIONS.md C.
    XEDGE=${XEDGE---x-initial-edge} ;;
  *) echo "usage: $0 [conv|ref|ref_micro|chip]"; exit 2 ;;
esac

# Lint the testbench with -Wall. Warnings inside the core (generated netlist or
# third-party SV) are not ours and are not checked here.
if [ "$WHICH" = conv ]; then
  LINT=$(verilator --lint-only -Wall -Wno-DECLFILENAME -Wno-UNUSEDSIGNAL -Wno-UNUSEDPARAM \
    -Wno-PINCONNECTEMPTY -Wno-BLKSEQ -Wno-SYNCASYNCNET --top-module tb_ibex_fw --timing \
    "$HERE/tb_ibex_fw.sv" "${CORE[@]}" 2>&1 | grep "^%Warning.*tb_ibex_fw.sv" || true)
  if [ -n "$LINT" ]; then echo "$LINT"; echo "lint warnings in tb_ibex_fw.sv"; exit 1; fi
fi

verilator --binary -j "$(nproc)" -O2 --x-assign unique --x-initial unique ${XEDGE:-} \
  -Wno-fatal -Wno-lint -Wno-style -Wno-MULTIDRIVEN -Wno-UNOPTFLAT \
  "${DEFS[@]}" --top-module "${TOP:-tb_ibex_fw}" -Mdir "$OBJ/$WHICH" -o "../$EXE" \
  "${CORE[@]}" "${TBSRC:-$HERE/tb_ibex_fw.sv}" >"$OBJ/build_$WHICH.log" 2>&1 \
  || { tail -40 "$OBJ/build_$WHICH.log"; exit 1; }
echo "built $OBJ/$EXE"
