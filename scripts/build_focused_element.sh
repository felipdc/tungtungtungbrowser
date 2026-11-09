#!/bin/bash
# Cross-compile against declarations only; all implementation comes from the guest.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [ "$#" -ne 1 ]; then echo "Usage: $0 NEW_EXTERNAL_OUTPUT_DIRECTORY" >&2; exit 2; fi
out=$(realpath -m -- "$1")
case "$out/" in "$root/"*) echo "Keep generated binaries outside the repository." >&2; exit 2;; esac
compiler=${TTT_FOCUS_CLANG:-clang-19}
linker=${TTT_FOCUS_LINKER:-ld64.lld-19}
signer=${TTT_FOCUS_LDID:-ldid}
command -v "$compiler" >/dev/null
command -v "$linker" >/dev/null
command -v "$signer" >/dev/null
mkdir -m 700 -- "$out"
"$compiler" --target=arm64-apple-ios16.0 -ffreestanding -fno-stack-protector \
  -fno-builtin -O2 -Wall -Wextra -Werror -c "$root/native/focused_element.c" -o "$out/focused-element.o"
"$linker" -arch arm64 -platform_version ios 16.0 26.0 -o "$out/focused-element" \
  "$out/focused-element.o" "$root/native/focused_element_libSystem.tbd" -adhoc_codesign
"$signer" -S"$root/native/focused_element_entitlements.plist" "$out/focused-element"
python3 - "$root" "$out" <<'PY'
import hashlib, json, sys
from pathlib import Path
root, out = map(Path, sys.argv[1:])
report = {'protocol': 1, 'binary': 'focused-element',
          'binary_sha256': hashlib.sha256((out/'focused-element').read_bytes()).hexdigest(),
          'sources': {name: hashlib.sha256((root/'native'/name).read_bytes()).hexdigest()
                      for name in ('focused_element.c', 'focus_guard.h', 'focus_guard_trace.h', 'address_field.h', 'focus_resolution.h', 'focused_element_libSystem.tbd', 'focused_element_entitlements.plist')},
          'installed': False}
with (out/'build.json').open('x') as f: json.dump(report, f, indent=2); f.write('\n')
print(json.dumps(report, indent=2))
PY
