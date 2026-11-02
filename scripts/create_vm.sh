#!/bin/bash
# One-time firmware preparation/restore. This is not a browser startup benchmark.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
src=${TTT_RUNTIME_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/tungtungtungbrowser/vphone-linux}
state=${TTT_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/tungtungtungbrowser}
test -s "$state/build.ready"
mkdir -p "$state"
for patch in vusb-unlink auto-lock-timeout; do
  if ! git -C "$src" apply --reverse --check "$root/patches/$patch.patch" 2>/dev/null; then
    git -C "$src" apply --check "$root/patches/$patch.patch"
    git -C "$src" apply "$root/patches/$patch.patch"
  fi
done
python3 "$root/scripts/check_usbip_protocol.py" --source "$src"
# Debian's libusbmuxd reports positive errno after a disconnect; normalize it
# for libimobiledevice so the final restore acknowledgement cannot spin forever.
cc -shared -fPIC -O2 -Wall -Wextra -Werror "$root/native/usbmuxd_send_fix.c" \
  -o "$state/libusbmuxd-send-fix.so" -ldl -pthread
export LD_PRELOAD="$state/libusbmuxd-send-fix.so${LD_PRELOAD:+:$LD_PRELOAD}"
for module in usbip_core vhci_hcd vhost_vsock; do
  if ! /usr/sbin/modinfo "$module" >/dev/null 2>&1; then
    echo "Missing kernel module $module. Boot the standard Debian kernel and rebuild linux-apfs-rw before creating a VM." >&2
    exit 2
  fi
done
# A kernel change invalidates the APFS helper used to prepare the SSH ramdisk.
apfs=$src/.build/linux-apfs-rw
apfs_kernel=$(/usr/sbin/modinfo -F vermagic "$apfs/apfs.ko" 2>/dev/null | cut -d' ' -f1 || true)
if [ "$apfs_kernel" != "$(uname -r)" ]; then
  test -d "$apfs"
  make -C "$apfs" clean
  make -C "$apfs" -j2
fi
export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.json
if [ ! -f "$VK_DRIVER_FILES" ]; then export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.x86_64.json; fi
export VK_ICD_FILENAMES=$VK_DRIVER_FILES
export LP_NUM_THREADS=1 MEM=6G SMP=4 WINDOW=0 TOOLBAR=0 VPHONE_CPUS=
# Slow CPU-only filesystem verification can leave a USB bulk-IN request idle
# beyond the exporter's 300-second default. Keep that request pending while
# the guest's mtree pass runs; driver disconnects still cancel it immediately.
export VUSB_URB_TIMEOUT=1800
cd "$src"
# Keep device, OS, display scale and destination choices in the upstream CLI.
# Firmware and prepared VM bundles stay in the user's external setup workspace.
./setup/create-vm.sh "$@"
