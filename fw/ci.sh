#!/usr/bin/env bash
# Everything the firmware CI runs, in order. Same command locally and in
# .github/workflows/fw.yaml. Needs: riscv64-unknown-elf-gcc, verilator,
# python3 + pytest, deps/ibex (scripts/fetch_deps.sh) and deps/riscv-tests
# (fw/scripts/fetch_riscv_tests.sh).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
step() { echo; echo "=== $*"; }

step "Testbenches: converted core, upstream SV core, upstream micro core"
test/fw/build_tb.sh conv
test/fw/build_tb.sh ref
test/fw/build_tb.sh ref_micro

step "Firmware build (rv32imc = Ibex small; rv32ec = micro fallback)"
make -C fw -j"$(nproc)" ARCH=rv32imc
make -C fw -j"$(nproc)" ARCH=rv32ec

step "HAL host unit tests (ASan/UBSan) and cross-check against the golden model"
make -C fw host-test
python3 -m pytest -q fw/tests/test_hal_crosscheck.py

step "riscv-tests: converted vs upstream, zero wait states, then random stalls + single port"
python3 fw/riscv-tests/run.py --tb both
python3 fw/riscv-tests/run.py --tb both --stall 30 --single-port --seed 3

step "Firmware runtime tests"
python3 fw/tests/run_fw_tests.py --tb conv
python3 fw/tests/run_fw_tests.py --tb ref
python3 fw/tests/run_fw_tests.py --tb ref_micro --arch rv32ec

step "Image sizes (RAM 4096)"
for a in rv32imc rv32ec; do
  for f in fw/build/${a}_ram4096/*.elf; do
    python3 fw/scripts/mkimage.py "$f" "${f%.elf}" | sed "s/^/$a /"
  done
done
echo; echo "fw/ci.sh: all passed"
