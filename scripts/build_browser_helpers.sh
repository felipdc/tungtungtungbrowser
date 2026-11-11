#!/usr/bin/env bash
# Build all four helpers together; installers independently verify source hashes.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
if [[ $# != 1 ]]; then
  echo "Usage: $0 NEW_EXTERNAL_BUILD_DIRECTORY" >&2
  exit 2
fi
output="$(realpath -m -- "$1")"
case "$output/" in "$root/"*) echo 'Keep helper builds outside the repository' >&2; exit 2;; esac
mkdir -m 700 -- "$output"
for helper in touch_helper keyboard_helper focused_element inspector_bridge; do
  bash "$root/scripts/build_${helper}.sh" "$output/$helper"
done
