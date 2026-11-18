#!/usr/bin/env bash
# Run the converted-Ibex smoke test with Icarus Verilog. Exit status 0 = PASS.
set -euo pipefail
cd "$(dirname "$0")"
python3 gen_prog.py .
iverilog -g2012 -o sim.vvp tb_ibex_smoke.v ../../src/ibex_small.v
vvp -n sim.vvp | tee sim.log
grep -q "^PASS" sim.log
