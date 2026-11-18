#!/usr/bin/env python3
"""Ibex SystemVerilog -> plain Verilog conversion and equivalence proof.

    scripts/ibex_flow.py convert [micro|small]   writes src/ibex_<cfg>.v
    scripts/ibex_flow.py equiv   [micro|small]   proves src/ibex_<cfg>.v == Ibex SV

Why: the Tiny Tapeout flow and Icarus simulation read plain Verilog; Ibex is
SystemVerilog. Conversion uses the slang frontend bundled with the PyPI Yosys
build (yowasp-yosys), keeping the module hierarchy. The equivalence proof
compares slang's default (flattened) elaboration of the original sources with
the converted file read back through Yosys's own Verilog reader, so it covers
the hierarchy-preserving mode, the Verilog writer and the reader.

Needs: scripts/fetch_deps.sh, pip install yowasp-yosys.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "syn" / "ibex" / "out"      # git-ignored scratch (the WASM Yosys sees only the tree)
YOSYS = os.environ.get("YOSYS", "yowasp-yosys")

IBEX = "deps/ibex"
PRIM = f"{IBEX}/vendor/lowrisc_ip/ip"

# Ibex top-level parameters per configuration (everything else at its default:
# BaseIsa RV32I, RV32ZC Zca+Zcb+Zcmp, no PMP, no icache, no SecureIbex).
CONFIGS = {
    "micro": {"RV32E": "1", "RV32M": "ibex_pkg::RV32MNone", "RV32B": "ibex_pkg::RV32BNone",
              "RegFile": "ibex_pkg::RegFileFF"},
    "small": {"RV32E": "0", "RV32M": "ibex_pkg::RV32MFast", "RV32B": "ibex_pkg::RV32BNone",
              "RegFile": "ibex_pkg::RegFileFF"},
}

PKGS = [f"{PRIM}/prim/rtl/prim_util_pkg.sv", f"{PRIM}/prim/rtl/prim_mubi_pkg.sv",
        f"{PRIM}/prim/rtl/prim_count_pkg.sv", f"{PRIM}/prim/rtl/prim_cipher_pkg.sv",
        f"{PRIM}/prim/rtl/prim_secded_pkg.sv", f"{PRIM}/prim_generic/rtl/prim_ram_1p_pkg.sv",
        f"{IBEX}/rtl/ibex_pkg.sv"]


def read_slang(cfg: str, keep_hierarchy: bool) -> str:
    g = " ".join(f"-G {k}={v}" for k, v in CONFIGS[cfg].items())
    hier = " --keep-hierarchy" if keep_hierarchy else ""
    return (f"read_slang --threads 1 --compat vcs --ignore-assertions{hier} --top ibex_top "
            f"-D SYNTHESIS -D YOSYS -I {PRIM}/prim/rtl -I {IBEX}/vendor/lowrisc_ip/dv/sv/dv_utils "
            f"-y {IBEX}/rtl -y {PRIM}/prim/rtl -y {PRIM}/prim_generic/rtl --libext .sv "
            f"{g} {' '.join(PKGS)} {IBEX}/rtl/ibex_top.sv")


def yosys(script: str, name: str) -> str:
    OUT.mkdir(parents=True, exist_ok=True)
    ys = OUT / f"{name}.ys"
    log = OUT / f"{name}.log"
    ys.write_text(script)
    r = subprocess.run([YOSYS, "-q", "-l", str(log.relative_to(ROOT)), str(ys.relative_to(ROOT))],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write(r.stdout[-3000:] + r.stderr[-3000:])
        raise SystemExit(f"yosys failed, see {log}")
    return log.read_text()


def pin(name: str) -> str:
    for line in (ROOT / "scripts" / "fetch_deps.sh").read_text().splitlines():
        if line.startswith(f"{name}_REV="):
            return line.split("=")[1].split()[0]
    return "?"


def clean_names(modules: list[str], prefix: str) -> dict[str, str]:
    """Map slang's per-instance names ('ibex_csr$ibex_top.u_ibex_core...u_mtvec_csr')
    to readable unique ones ('ibex_small__csr__u_mtvec_csr')."""
    parsed = []
    for m in modules:
        base, _, path = m.lstrip("\\").partition("$")
        parsed.append((m, base, path))
    count: dict[str, int] = {}
    for _, base, _ in parsed:
        count[base] = count.get(base, 0) + 1
    out = {}
    for m, base, path in parsed:
        if base == "ibex_top":
            new = f"{prefix}_top"
        else:
            short = base[5:] if base.startswith("ibex_") else base
            new = f"{prefix}__{short}"
            if count[base] > 1:
                new += "__" + re.sub(r"\W", "_", path.split(".")[-1])
        assert new not in out.values(), new
        out[m] = new
    return out


def convert(cfg: str) -> Path:
    prefix = f"ibex_{cfg}"
    il = OUT / f"{prefix}.il"
    log = yosys(f"""{read_slang(cfg, keep_hierarchy=True)}
hierarchy -top ibex_top
proc
opt_clean
tee -o {(OUT / f'{prefix}.modules').relative_to(ROOT)} ls
write_rtlil {il.relative_to(ROOT)}
""", f"convert_{cfg}_1")
    mods = [l.strip() for l in (OUT / f"{prefix}.modules").read_text().splitlines()
            if l.startswith("  ") and l.strip()]
    renames = clean_names(mods, prefix)
    dst = ROOT / "src" / f"{prefix}.v"
    tmp = OUT / f"{prefix}.v"
    yosys(f"read_rtlil {il.relative_to(ROOT)}\n"
          + "".join(f"chtype -map {old} {new}\nrename {old} {new}\n" for old, new in renames.items())
          + f"hierarchy -top {prefix}_top\ncheck -assert\n"
          + f"write_verilog -noattr {tmp.relative_to(ROOT)}\n", f"convert_{cfg}_2")
    params = ", ".join(f"{k}={v}" for k, v in CONFIGS[cfg].items())
    header = f"""// Ibex ({cfg}) converted to plain Verilog. GENERATED FILE, DO NOT EDIT.
//
// Source:     lowRISC Ibex {pin('IBEX')} (https://github.com/lowRISC/ibex), Apache-2.0
// Top:        ibex_top, renamed {prefix}_top; submodules {prefix}__*
// Parameters: {params}; all others at their defaults
// Generator:  scripts/ibex_flow.py convert {cfg}   (yosys-slang, hierarchy kept)
// Proof:      scripts/ibex_flow.py equiv {cfg}     (formal equivalence with the SV sources)
"""
    dst.write_text(header + tmp.read_text())
    n = sum(1 for l in dst.read_text().splitlines() if l.startswith("module "))
    print(f"wrote {dst.relative_to(ROOT)}: {n} modules, {dst.stat().st_size // 1024} KiB")
    return dst


def equiv(cfg: str) -> None:
    prefix = f"ibex_{cfg}"
    # IBEX_EQUIV_SRC overrides the file under test (used to check the proof catches mutants)
    src = Path(os.environ.get("IBEX_EQUIV_SRC", ROOT / "src" / f"{prefix}.v")).resolve().relative_to(ROOT)
    log = yosys(f"""# gold: original SystemVerilog, slang's default flattened elaboration
{read_slang(cfg, keep_hierarchy=False)}
hierarchy -top ibex_top
proc
memory
opt_clean
async2sync
rename ibex_top gold
design -stash gold

# gate: the committed plain-Verilog conversion, read by Yosys's own frontend
read_verilog {src}
hierarchy -top {prefix}_top
proc
flatten
memory
opt_clean
async2sync
rename {prefix}_top gate
design -stash gate

design -copy-from gold -as gold gold
design -copy-from gate -as gate gate
equiv_make gold gate equiv
hierarchy -top equiv
# fast pass: two-valued
equiv_simple -seq 2
equiv_induct -seq 2
# remaining points: model X. Where the SV leaves a value undefined ('x defaults,
# unique-case violations) the conversion may do anything; everywhere else it
# must match exactly (gate refines gold).
equiv_simple -undef -seq 2
equiv_induct -undef -seq 2
tee -o {(OUT / f'equiv_{cfg}.txt').relative_to(ROOT)} equiv_status -assert
""", f"equiv_{cfg}")
    print((OUT / f"equiv_{cfg}.txt").read_text().strip().splitlines()[-1])


def main(argv):
    if len(argv) < 2 or argv[1] not in ("convert", "equiv"):
        raise SystemExit(__doc__)
    cfgs = argv[2:] or ["small"]
    for c in cfgs:
        if c not in CONFIGS:
            raise SystemExit(f"unknown config {c}; choose from {', '.join(CONFIGS)}")
        (convert if argv[1] == "convert" else equiv)(c)


if __name__ == "__main__":
    main(sys.argv)
