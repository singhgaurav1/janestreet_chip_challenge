#!/usr/bin/env bash
# Fetch the pinned upstream riscv-tests (and its env submodule) into
# deps/riscv-tests (git-ignored). Firmware-chat dependency; RTL deps live in
# scripts/fetch_deps.sh.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DIR=$ROOT/deps/riscv-tests
URL=https://github.com/riscv-software-src/riscv-tests.git
REV=3df703cd52d5f0d8224e3c50bf03c6cb70efff98      # master, 2026-10-19
ENV_URL=https://github.com/riscv/riscv-test-env.git
ENV_REV=6de71edb142be36319e380ce782c3d1830c65d68  # submodule pin at REV

fetch() { # dir url rev
  local dir=$1 url=$2 rev=$3
  if [ ! -d "$dir/.git" ]; then
    rm -rf "$dir"; git init -q "$dir"; git -C "$dir" remote add origin "$url"
  fi
  git -C "$dir" fetch -q --depth 1 origin "$rev"
  git -C "$dir" checkout -q FETCH_HEAD
}
fetch "$DIR" "$URL" "$REV"
fetch "$DIR/env" "$ENV_URL" "$ENV_REV"
echo "riscv-tests @ $(git -C "$DIR" rev-parse --short HEAD), env @ $(git -C "$DIR/env" rev-parse --short HEAD)"
