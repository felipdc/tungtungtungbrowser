#!/bin/bash
# Build the optional bounded native session helpers on Linux.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [ "$#" -ne 1 ]; then echo "Usage: $0 NEW_EXTERNAL_OUTPUT_DIRECTORY" >&2; exit 2; fi
out=$(realpath -m -- "$1")
case "$out/" in "$root/"*) echo "Keep generated binaries outside the repository." >&2; exit 2;; esac
compiler=${TTT_RESIDENT_CLANG:-clang-19}
linker=${TTT_RESIDENT_LINKER:-ld64.lld-19}
signer=${TTT_RESIDENT_LDID:-ldid}
command -v "$compiler" >/dev/null
command -v "$linker" >/dev/null
command -v "$signer" >/dev/null
mkdir -m 700 -- "$out"
for kind in lease focus control; do
  source_dir="$root/native/resident/$kind"
  mkdir -m 700 -- "$out/$kind"
  names=server
  entitlements=focused_element_entitlements.plist
  if [ "$kind" = lease ]; then names='guardian controller'; entitlements=entitlements.plist; fi
  for name in $names; do
    "$compiler" --target=arm64-apple-ios16.0 -ffreestanding -fno-stack-protector \
      -fno-builtin -O2 -Wall -Wextra -Werror -Wno-unused-function -I "$root/native" \
      -c "$source_dir/$name.c" -o "$out/$kind/$name.o"
    "$linker" -arch arm64 -platform_version ios 16.0 26.0 -o "$out/$kind/$name" \
      "$out/$kind/$name.o" "$source_dir/libSystem.tbd" -adhoc_codesign
    "$signer" -S"$source_dir/$entitlements" "$out/$kind/$name"
  done
done
python3 "$root/scripts/resident_browser.py" manifest "$out"
