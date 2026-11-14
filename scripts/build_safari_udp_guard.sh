#!/bin/bash
# Build the Safari UDP policy helper on Linux without an Apple SDK.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [ "$#" -ne 1 ]; then echo "Usage: $0 NEW_EXTERNAL_OUTPUT_DIRECTORY" >&2; exit 2; fi
out=$(realpath -m -- "$1")
case "$out/" in "$root/"*) echo "Keep generated binaries outside the repository." >&2; exit 2;; esac
compiler=${TTT_SAFARI_UDP_GUARD_CLANG:-clang-19}
linker=${TTT_SAFARI_UDP_GUARD_LINKER:-ld64.lld-19}
signer=${TTT_SAFARI_UDP_GUARD_LDID:-ldid}
command -v "$compiler" >/dev/null
command -v "$linker" >/dev/null
command -v "$signer" >/dev/null
mkdir -m 700 -- "$out"
"$compiler" --target=arm64-apple-ios16.0 -ffreestanding -fno-stack-protector \
  -fno-builtin -O2 -Wall -Wextra -Werror -c "$root/native/safari_udp_guard.c" \
  -o "$out/safari-udp-guard.o"
"$linker" -arch arm64 -platform_version ios 16.0 26.0 -o "$out/safari-udp-guard" \
  "$out/safari-udp-guard.o" "$root/native/safari_udp_guard_libSystem.tbd" -adhoc_codesign
"$signer" -S"$root/native/process_observer_entitlements.plist" "$out/safari-udp-guard"
python3 - "$root" "$out" "$compiler" "$linker" "$signer" <<'PY'
import hashlib, json, sys
from pathlib import Path
root, out = map(Path, sys.argv[1:3])
report = {'protocol': 1, 'binary': 'safari-udp-guard',
          'binary_sha256': hashlib.sha256((out/'safari-udp-guard').read_bytes()).hexdigest(),
          'sources': {name: hashlib.sha256((root/'native'/name).read_bytes()).hexdigest()
                      for name in ('safari_udp_guard.c', 'safari_udp_guard_libSystem.tbd',
                                   'process_observer_entitlements.plist')},
          'tools': dict(zip(('compiler', 'linker', 'signer'), sys.argv[3:])),
          'installed': False, 'native_executed': False}
with (out/'build.json').open('x') as file:
    json.dump(report, file, indent=2); file.write('\n')
print(json.dumps(report, indent=2))
PY
