#!/bin/bash
# Build the pinned CPU runtime on a supported Linux host.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
src=${TTT_RUNTIME_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/tungtungtungbrowser/vphone-linux}
state=${TTT_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/tungtungtungbrowser}
commit=888d17ca17268aaecd277c64f2d95385b184744b
mkdir -p "$(dirname "$src")" "$state"
if [ ! -d "$src/.git" ]; then
  git clone --no-checkout --filter=blob:none https://github.com/YahiaAngelo/vphone-linux.git "$src"
fi
git -C "$src" fetch origin "$commit"
git -C "$src" checkout --detach "$commit"
# Bound build memory on a 16 GiB CPU-only host.
export CARGO_BUILD_JOBS=2 CMAKE_BUILD_PARALLEL_LEVEL=2 MAKEFLAGS=-j2
export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.x86_64.json
if [ ! -f "$VK_DRIVER_FILES" ]; then export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.json; fi
export LP_NUM_THREADS=1
export VPHONE_CPUS=
cd "$src"
# Debian has no Ubuntu linux-headers-generic metapackage. Match the running kernel.
python3 - <<'PY'
from pathlib import Path
p = Path('setup/setup.sh')
s = p.read_text()
s = s.replace('else extra+=(linux-headers-generic); fi',
              'else extra+=("linux-headers-$(uname -r)"); fi')
p.write_text(s)
PY
./setup/setup.sh deps -y
if [ -r "$HOME/.cargo/env" ]; then . "$HOME/.cargo/env"; fi
./setup/setup.sh sources -y
# The vresearch101 CPU uses the M1 register layout. Keep user-space JIT
# permission writes on its user mask instead of the older A13 mapping.
sprr_patch="$root/patches/qemu-sprr-user-mask.patch"
if ! git -C qemu apply --reverse --check "$sprr_patch" 2>/dev/null; then
  git -C qemu apply --check "$sprr_patch"
  git -C qemu apply "$sprr_patch"
fi
./setup/setup.sh build -y
./setup/setup.sh release -y
vulkaninfo --summary > "$state/vulkan-summary.txt" 2>&1
git diff > "$state/runtime-build.patch"
date --iso-8601=seconds > "$state/build.ready"
