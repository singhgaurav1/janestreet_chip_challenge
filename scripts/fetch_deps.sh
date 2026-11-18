#!/usr/bin/env bash
# Fetch pinned third-party dependencies into ./deps (git-ignored).
# Pins are recorded here so every chat/CI run synthesizes the same sources.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEPS=$ROOT/deps
mkdir -p "$DEPS"

IBEX_URL=https://github.com/lowRISC/ibex.git
IBEX_REV=3d524f19ee8e464b3fb0e29c77dc9ff45934815a   # master, 2026-11-12

PDK_URL=https://github.com/IHP-GmbH/IHP-Open-PDK.git
PDK_REV=e73adb4045731ddec21634146d536380057f6a26    # main, 2026-11-10

fetch() { # name url rev [sparse paths...]
  local name=$1 url=$2 rev=$3; shift 3
  local dir=$DEPS/$name
  if [ ! -d "$dir/.git" ]; then
    git init -q "$dir"
    git -C "$dir" remote add origin "$url"
    if [ $# -gt 0 ]; then
      git -C "$dir" config core.sparseCheckout true
      git -C "$dir" sparse-checkout set --no-cone "$@"
    fi
  fi
  git -C "$dir" fetch -q --depth 1 --filter=blob:none origin "$rev"
  git -C "$dir" checkout -q FETCH_HEAD
  echo "$name @ $(git -C "$dir" rev-parse --short HEAD)"
}

fetch ibex "$IBEX_URL" "$IBEX_REV"
fetch IHP-Open-PDK "$PDK_URL" "$PDK_REV" \
  /ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib/ \
  /ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram/lib/
