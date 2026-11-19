#!/usr/bin/env bash
# Everything the firmware CI runs, in order. Same command locally and in
# .github/workflows/fw.yaml. Needs: riscv64-unknown-elf-gcc, verilator,
# python3 + pytest, deps/ibex (scripts/fetch_deps.sh) and deps/riscv-tests
# (fw/scripts/fetch_riscv_tests.sh).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
step() { echo; echo "=== $*"; }

step "Testbenches: converted core, upstream SV small and micro, full chip"
test/fw/build_tb.sh conv
test/fw/build_tb.sh ref
test/fw/build_tb.sh ref_micro
test/fw/build_tb.sh chip

step "Firmware build: chip (2 KiB, docs/soc.md) and standalone testbench; small and micro"
for t in chip tb; do
  for a in rv32imc rv32ec; do make -C fw -j"$(nproc)" TARGET=$t ARCH=$a; done
done

step "HAL host unit tests (ASan/UBSan) and cross-check against the golden model"
make -C fw host-test
python3 -m pytest -q fw/tests/test_hal_crosscheck.py

step "riscv-tests on Ibex alone: converted vs upstream, then random stalls + single port"
python3 fw/riscv-tests/run.py --tb both | tail -3
python3 fw/riscv-tests/run.py --tb both --stall 30 --single-port --seed 3 | tail -3

step "riscv-tests on the full chip, each loaded over the host SPI pins"
python3 fw/riscv-tests/run.py --tb chip | grep -v '^PASS'

step "Firmware runtime tests: standalone cores"
python3 fw/tests/run_fw_tests.py --tb conv
python3 fw/tests/run_fw_tests.py --tb ref
python3 fw/tests/run_fw_tests.py --tb ref_micro --arch rv32ec

step "Firmware runtime tests: full chip, three power-up seeds"
for s in 7 11 23; do python3 fw/tests/run_fw_tests.py --tb chip --seed $s; done

echo; echo "fw/ci.sh: all passed"
