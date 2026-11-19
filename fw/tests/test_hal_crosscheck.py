"""Cross-check the C HAL's register packing against the golden model's.

Both were written independently from docs/pe_isa.md section 6 (the HAL by
the firmware chat, tools/peemu/config.py by the RTL chat), so agreement on
thousands of random configurations is evidence that both read the spec the
same way. Needs fw/build/host/test_pe_hal (make -C fw host-test).

    python -m pytest fw/tests/test_hal_crosscheck.py
"""
from __future__ import annotations

import random
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from peemu.config import LAYOUT, EngineConfig, clkdiv_for  # noqa: E402

EXE = ROOT / "fw" / "build" / "host" / "test_pe_hal"
REGS = ["CLKDIV", "EXECCTRL", "SHIFTCTRL", "PINCTRL", "ASSIST", "CRC_POLY", "TIMING"]

# Architectural ranges (pe_isa.md), field name -> (lo, hi)
RANGES = {"clkdiv_int": (0, 65535), "clkdiv_frac": (0, 255), "wrap_bottom": (0, 31),
          "wrap_top": (0, 31), "jmp_pin": (0, 31), "filter": (0, 1), "sync_edge": (0, 2),
          "autosync": (0, 1), "side_count": (0, 5), "side_opt": (0, 1), "side_pindirs": (0, 1),
          "origin": (0, 63), "in_shift_right": (0, 1), "out_shift_right": (0, 1),
          "autopush": (0, 1), "autopull": (0, 1), "push_thresh": (1, 32), "pull_thresh": (1, 32),
          "out_base": (0, 31), "out_count": (0, 32), "set_base": (0, 31), "set_count": (0, 5),
          "side_base": (0, 31), "in_base": (0, 31), "coder_mode": (0, 3), "coder_diff": (0, 1),
          "coder_idle": (0, 1), "stuff_mode": (0, 2), "stuff_n": (1, 8), "bit_ticks": (1, 16),
          "crc_width": (1, 32), "crc_init_ones": (0, 1), "stamp_en": (0, 1),
          "stamp_count": (1, 16), "crc_reflect": (0, 1), "crc_poly": (0, 2**32 - 1),
          "timeout": (0, 65535), "sync_phase": (0, 65535)}


def encode(lines: list[str]) -> list[str]:
    if not EXE.exists():
        pytest.skip(f"{EXE} missing: run make -C fw host-test")
    out = subprocess.run([str(EXE), "--encode"], input="\n".join(lines) + "\n",
                         capture_output=True, text=True, check=True).stdout
    return out.splitlines()


def test_same_field_names():
    assert {name for _r, name, *_ in LAYOUT} == set(RANGES)


def test_random_valid_configs_pack_identically():
    rng = random.Random(1)
    cfgs, lines = [], []
    while len(cfgs) < 3000:
        kw = {k: rng.randint(lo, hi) for k, (lo, hi) in RANGES.items()}
        # Cross-field rules the HAL enforces (pe_isa.md 5.4; side-set opt bit)
        if kw["clkdiv_int"] and kw["sync_phase"] >= kw["clkdiv_int"]:
            kw["sync_phase"] = rng.randint(0, kw["clkdiv_int"] - 1)
        if kw["side_opt"] and kw["side_count"] == 0:
            kw["side_count"] = 1
        cfg = EngineConfig(**kw)
        cfgs.append(cfg)
        lines.append(" ".join(f"{k}={v}" for k, v in kw.items()))
    for cfg, got in zip(cfgs, encode(lines)):
        regs = cfg.to_regs()
        want = " ".join(f"{regs[r]:08x}" for r in REGS)
        assert got == want, f"{cfg}: C {got} vs model {want}"


@pytest.mark.parametrize("field", sorted(RANGES))
def test_out_of_range_rejected(field):
    lo, hi = RANGES[field]
    bad = [v for v in (lo - 1, hi + 1) if v >= 0 and v < 2**32]
    if not bad:
        pytest.skip("full 32-bit range")
    assert encode([f"{field}={v}" for v in bad]) == ["EINVAL"] * len(bad)


def test_tick_divider_matches_model():
    rng = random.Random(2)
    rates = [115200 * 8, 9600 * 16, 400000 * 4, 1_000_000 * 10, 1_500_000 * 8, 12_000_000,
             60_000_000, 30_000_000] + [rng.randint(916, 60_000_000) for _ in range(300)]
    for t in rates:
        got = subprocess.run([str(EXE), "--tick", "60000000", str(t)], capture_output=True,
                             text=True, check=True).stdout.split()
        try:
            want = list(map(str, clkdiv_for(60e6, t)))
        except ValueError:
            want = ["EINVAL"]
        assert got == want, f"tick {t}: C {got} vs model {want}"
