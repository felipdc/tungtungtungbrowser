#!/bin/bash
# Cross-compile a native iOS helper on Linux, without an Apple SDK.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [ "$#" -ne 1 ]; then echo "Usage: $0 NEW_EXTERNAL_OUTPUT_DIRECTORY" >&2; exit 2; fi
out=$(realpath -m -- "$1")
case "$out/" in "$root/"*) echo "Keep generated binaries outside the repository." >&2; exit 2;; esac
compiler=${TTT_TOUCH_CLANG:-clang-19}
linker=${TTT_TOUCH_LINKER:-ld64.lld-19}
signer=${TTT_TOUCH_LDID:-ldid}
command -v "$compiler" >/dev/null
command -v "$linker" >/dev/null
command -v "$signer" >/dev/null
mkdir -m 700 -- "$out"
"$compiler" --target=arm64-apple-ios16.0 -ffreestanding -fno-stack-protector \
  -fno-builtin -O2 -Wall -Wextra -Werror -c "$root/native/touch_helper.c" -o "$out/touch.o"
"$linker" -arch arm64 -platform_version ios 16.0 26.0 -o "$out/touch" \
  "$out/touch.o" "$root/native/touch_libSystem.tbd" -adhoc_codesign
"$signer" -S"$root/native/touch_entitlements.plist" "$out/touch"
python3 - "$root" "$out" "$compiler" "$linker" "$signer" <<'PY'
import hashlib, json, sys
from pathlib import Path
root, out = map(Path, sys.argv[1:3])
report = {'protocol': 1, 'binary': 'touch', 'binary_sha256': hashlib.sha256((out/'touch').read_bytes()).hexdigest(),
          'sources': {name: hashlib.sha256((root/'native'/name).read_bytes()).hexdigest()
                      for name in ('touch_helper.c', 'touch_libSystem.tbd', 'touch_entitlements.plist')},
          'tools': dict(zip(('compiler','linker','signer'), sys.argv[3:])),
          'installed': False, 'native_input_sent': False}
with (out/'build.json').open('x') as f: json.dump(report, f, indent=2); f.write('\n')
print(json.dumps(report, indent=2))
PY
