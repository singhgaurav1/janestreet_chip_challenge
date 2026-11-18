#!/usr/bin/env bash
# Area check: synthesize Ibex (ibex_top) against IHP SG13CMOS5L standard cells.
#
# Ibex is SystemVerilog; it is read directly with the slang frontend bundled in
# the PyPI Yosys build (pip install yowasp-yosys). No sv2v needed.
#
# Usage: syn/ibex/synth_ibex.sh <config>   where <config> is micro | emc | small
# Needs: scripts/fetch_deps.sh run first.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
CFG=${1:-micro}
I=$ROOT/deps/ibex
P=$I/vendor/lowrisc_ip/ip
LIB=$ROOT/deps/IHP-Open-PDK/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib
YOSYS=${YOSYS:-yowasp-yosys}
OUT=$ROOT/syn/ibex/out; mkdir -p "$OUT"

case $CFG in
  micro) G="-G RV32E=1 -G RV32M=ibex_pkg::RV32MNone -G RV32B=ibex_pkg::RV32BNone" ;;
  emc)   G="-G RV32E=1 -G RV32M=ibex_pkg::RV32MFast -G RV32B=ibex_pkg::RV32BNone" ;;
  small) G="-G RV32E=0 -G RV32M=ibex_pkg::RV32MFast -G RV32B=ibex_pkg::RV32BNone" ;;
  *) echo "unknown config $CFG"; exit 1 ;;
esac
G="$G -G RegFile=ibex_pkg::RegFileFF"

PKGS="$P/prim/rtl/prim_util_pkg.sv $P/prim/rtl/prim_mubi_pkg.sv $P/prim/rtl/prim_count_pkg.sv \
$P/prim/rtl/prim_cipher_pkg.sv $P/prim/rtl/prim_secded_pkg.sv $P/prim_generic/rtl/prim_ram_1p_pkg.sv \
$I/rtl/ibex_pkg.sv"

# --threads 1: the WASM build has no threads.
# --compat vcs: accepts Ibex's `logic x = expr;` declarations as continuous assigns.
cat > "$OUT/$CFG.ys" <<EOF
read_slang --threads 1 --compat vcs --top ibex_top -D SYNTHESIS -D YOSYS \
  -I $P/prim/rtl -I $I/vendor/lowrisc_ip/dv/sv/dv_utils \
  -y $I/rtl -y $P/prim/rtl -y $P/prim_generic/rtl --libext .sv \
  $G $PKGS $I/rtl/ibex_top.sv
hierarchy -top ibex_top
synth -top ibex_top -flatten
dfflibmap -liberty $LIB
abc -liberty $LIB
opt_clean -purge
tee -o $OUT/$CFG.stat stat -liberty $LIB
EOF
$YOSYS -q -l "$OUT/$CFG.log" "$OUT/$CFG.ys" >/dev/null
AREA=$(grep "Chip area" "$OUT/$CFG.stat" | awk '{print $NF}')
CELLS=$(awk '$2 ~ /^[0-9.E+]+$/ && $3 ~ /^sg13/ {n+=$1} END{print n}' "$OUT/$CFG.stat")
echo "ibex $CFG: cells=$CELLS area_um2=$AREA"
