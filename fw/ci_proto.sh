#!/usr/bin/env bash
# F4 firmware protocol tests: real firmware on the full chip (Icarus, cocotb),
# checked by third-party protocol models. Slow (minutes per protocol), so a
# separate step from fw/ci.sh. Needs: iverilog, pip install -r
# test/fw/cocotb/requirements.txt, and the images (make -C fw).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
make -s -C "$ROOT/fw" ARCH=rv32imc >/dev/null
cd "$ROOT/test/fw/cocotb"
rm -f results.xml
make MODULE="${MODULE:-test_uart}" 2>&1 | grep -E '^ *\*\* |Error|AssertionError' || true
python3 - <<'PY'
import sys, xml.etree.ElementTree as ET
r = ET.parse("results.xml").getroot()
cases = list(r.iter("testcase"))
bad = [c.get("name") for c in cases if c.find("failure") is not None or c.find("error") is not None]
print(f"{len(cases) - len(bad)}/{len(cases)} protocol tests passed" + (f"; failed: {bad}" if bad else ""))
sys.exit(1 if bad or not cases else 0)
PY
