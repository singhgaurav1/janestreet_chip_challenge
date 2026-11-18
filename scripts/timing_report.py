#!/usr/bin/env python3
"""Print the timing/area summary of a LibreLane run into the CI log.

The GDS artifacts are large and not always reachable, so the numbers that
drive timing closure are printed where they can always be read: per-corner
setup/hold slack, violation counts, utilization, and the worst setup paths
at the slowest corner.   usage: scripts/timing_report.py [runs/wokwi]
"""
import glob
import json
import sys
from pathlib import Path

run = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/wokwi")
keys = ("timing__setup__ws", "timing__setup__tns", "timing__setup_vio__count",
        "timing__hold__ws", "timing__hold_vio__count", "design__instance__utilization",
        "design__instance__count", "design__instance__area", "design__die__area",
        "design__max_slew_violation__count", "design__max_cap_violation__count",
        "route__wirelength", "power__total")
metrics = run / "final" / "metrics.json"
if metrics.exists():
    m = json.loads(metrics.read_text())
    print("== final metrics")
    for k in sorted(m):
        if k.startswith(keys) and "__corner:" in k or k in keys:
            print(f"  {k} = {m[k]}")
else:
    print(f"no {metrics}")

sta = sorted(glob.glob(str(run / "*-openroad-stapostpnr")))
if sta:
    d = Path(sta[-1])
    for corner in sorted(d.glob("*slow*")) or sorted(d.iterdir()):
        for name in ("summary.rpt", "max.rpt"):
            f = corner / name
            if f.exists():
                print(f"== {f}")
                lines = f.read_text().splitlines()
                print("\n".join(lines[:260]))
else:
    print("no post-PnR STA directory found")
