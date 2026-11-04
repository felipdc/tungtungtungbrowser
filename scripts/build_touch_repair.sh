#!/bin/bash
# Build the exact native cache installer on Linux; do not access a guest.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [ "$#" -ne 1 ]; then echo "Usage: $0 NEW_EXTERNAL_OUTPUT_DIRECTORY" >&2; exit 2; fi
out=$(realpath -m -- "$1")
case "$out/" in "$root/"*) echo "Keep generated binaries outside the repository." >&2; exit 2;; esac
compiler=${TTT_REPAIR_CLANG:-clang-19}
linker=${TTT_REPAIR_LINKER:-ld64.lld-19}
signer=${TTT_REPAIR_LDID:-ldid}
command -v "$compiler" >/dev/null
command -v "$linker" >/dev/null
command -v "$signer" >/dev/null
mkdir -m 700 -- "$out"
python3 "$root/scripts/touch_repair_build.py" recipe "$out"
"$compiler" --target=arm64-apple-ios16.0 -ffreestanding -fno-stack-protector \
  -fno-builtin -O2 -Wall -Wextra -Werror -I"$out" \
  -c "$root/native/touch_repair.c" -o "$out/touch-repair.o"
"$linker" -arch arm64 -platform_version ios 16.0 26.0 -o "$out/touch-repair" \
  "$out/touch-repair.o" "$root/native/touch_repair_libSystem.tbd" -adhoc_codesign
"$signer" -S"$root/native/touch_repair_entitlements.plist" "$out/touch-repair"
python3 "$root/scripts/touch_repair_build.py" manifest "$out" "$compiler" "$linker" "$signer"
