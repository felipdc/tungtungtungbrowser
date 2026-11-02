#!/bin/bash
# Select an installed USB/IP-capable kernel; does not reboot or stop jobs.
set -euo pipefail
ttt_kernel=$(printf '%s\n' /boot/vmlinuz-*-amd64 | sed 's@/boot/vmlinuz-@@' | grep -v -- '-cloud-' | sort -V | tail -1)
test -f "/boot/vmlinuz-$ttt_kernel"
test -f "/boot/initrd.img-$ttt_kernel"
test -d "/lib/modules/$ttt_kernel/build"
modinfo -k "$ttt_kernel" vhci_hcd >/dev/null
modinfo -k "$ttt_kernel" usbip_core >/dev/null
mkdir -p /etc/default/grub.d
printf 'GRUB_DEFAULT="Advanced options for Debian GNU/Linux>Debian GNU/Linux, with Linux %s"\n' "$ttt_kernel" > /etc/default/grub.d/99-tungtungtungbrowser.cfg
update-grub
echo "Selected $ttt_kernel for the next boot; no reboot performed."
