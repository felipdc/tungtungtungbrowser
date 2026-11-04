# Shared storage in isolated runtime experiments

Firmware and operating-system devices can address the same underlying disk.
For this board, pflash 0 and `aux-pci` expose NVRAM, while pflash 1 and `root-pci`
expose the system disk. Giving each interface its own writable overlay forks
their views, even if the overlays have the same backing file. A successful
guest boot does not prove that writes through those interfaces stay coherent.

The public launcher delegates the original raw-file mappings to the pinned
runtime. The divergence was found in a separate experiment wrapper that added
independent qcow2 overlays. It is not an established default-launcher bug.

## Check the block-device behavior without a guest

Run the synthetic diagnostic with a QEMU AArch64 system emulator and `qemu-img`:

```sh
python3 scripts/check_shared_storage.py \
  --qemu /path/to/qemu-system-aarch64 \
  --output /path/to/new-storage-check
```

The default `virt` machine tests BlockBackends. Use `--machine vresearch101`
with the runtime's QEMU fork to instantiate its actual bdif and virtio block
devices. That mode uses a zero-filled synthetic ROM and no graphics device.
Both modes keep guest CPUs stopped, disable networking and use only temporary
synthetic disks. No prepared device or vendor firmware is required.

The diagnostic has two cases. Separate overlays must hide cross-interface
writes, establishing a negative control. Shared nodes must expose writes in
both directions. Each case verifies QEMU's exit, complete qemu-io operations,
persisted bytes throughout the exported images and an unchanged parent hash.
Command arguments, block graphs, process output and reports are retained in a
new evidence directory. Synthetic disk files are removed afterward. A timeout
or incomplete result fails the check; it does not count as correct isolation.

## Connect devices to one writable node

Create one overlay per logical disk. A named qcow2 node can serve separate raw
views for the two device backends. For example, an NVRAM overlay that already
contains the logical NVRAM payload can use:

```sh
-blockdev '{"driver":"qcow2","node-name":"shared-nvram","file":{"driver":"file","filename":"/path/to/nvram-overlay.qcow2"}}' \
-drive if=pflash,index=0,id=fw-nvram,driver=raw,file.driver=raw,file.file=shared-nvram \
-drive if=none,id=aux-pci,driver=raw,file.driver=raw,file.file=shared-nvram \
-global vmapple-virtio-blk.share-rw=on
```

Use another shared node for pflash 1 and `root-pci`; SEP storage stays separate.
`share-rw` permits the two device backends to request write access to the shared
node. It does not make independently opened qcow2 images coherent. Do not open
one qcow2 image twice with locking disabled: that creates separate metadata
caches. Legacy `file=shared-nvram` also treats the node name as a filename;
the nested raw views above refer to the named node.

The original NVRAM file has a 16 KiB container prefix. Apply the offset exactly
once. An overlay backed by the logical payload view already excludes it and
must not receive another offset. Record virtual sizes and inspect the live
block graph: the expected writable nodes should be unique, their parents
read-only, and flushes enabled.

## Migrate existing experiments deliberately

Preserve every existing fork and its hash. Choose and record one canonical
source for each logical disk; do not silently merge competing firmware and
operating-system writes. Create new children of those chosen sources and keep
the prior state available for reversal. Copy the renderer-cache state when
comparing trials, and retain source hashes before and after shutdown.

Synthetic coherence does not validate the chosen native state or its boot
compatibility. Require native cold startup, rendered local diagnostics,
calibrated input, Safari/Home continuity and external-page browsing. Then
repeat a clean shutdown and cold boot using the resulting coherent state.
Keep CPU, RTC, proxy and resource experiments separate from that migration.

## Initial native migration results

On 2026-11-04, a trial explicitly chose the operating-system-facing NVRAM and
system-disk views from a previously successful stopped session. It preserved
both older firmware-facing views. The live graph had three writable qcow2
images: shared NVRAM, shared system disk and separate SEP storage. The selected
parents remained read-only, and their hashes and copied shader-cache manifest
were unchanged after native shutdown.

That guest reached foreground Safari in 208 seconds and rendered the expected
local canvas with no WebGL error. Its dedicated worker replied in 14.00 seconds,
but the shared worker exceeded the 15-second deadline. The sequence stopped
before touch or external navigation. A clean shutdown took 7.4 seconds.

A second cold boot used fresh children of those coherent images and copied
their shader cache. Safari foreground readiness took 225 seconds. The local
canvas, WebGL and both workers passed, followed by calibrated native touch.
During Home/Safari testing, Safari was still present at the Home observation,
but the launch response identified a replacement process. The recorder failed
the continuity check and stopped before external navigation. The Home capture
also retained visual artifacts. A matching-boot crash report identified a
background `scene-update` watchdog with a 10-second allowance. Its sampled
main-thread stack included responder-window traversal and keyboard-layout
work; that sample does not establish which operation consumed the deadline.
The second guest then shut down cleanly in 16.1 seconds, with unchanged parents,
copied cache and original baseline files.

With two guest application CPUs, 2 GiB guest RAM, a 1024 MiB translation cache,
one software-rendering thread and a four-core/6656 MiB container limit, the
600-second startup windows sampled peaks of 6.45 and 6.11 GiB. Both averaged
about 2.20 CPU cores and recorded no memory-limit events or OOM kills. The
failed page gate in the first trial and changed session/cache state prevent
these observations from establishing a resource improvement.

This verifies a native boot and cold restart using the shared graph, with
preserved parent state. It does not establish reliable browser lifecycle
behavior, complete native visual fidelity or external-page acceptance for the
migrated state. The shared graph remains an experiment, and CPU/RTC settings
were unchanged between these two trials.

## Verify the active clock path before changing it

Read-only inspection after the second trial identified `ApplePL031RTC` in the
native service registry. The board source maps its PL031 registers at physical
address `0x20050000`. Two data-register reads three host seconds apart advanced
by three seconds and matched host UTC to the second. Native boot identity was
consistent between the registry inspection and final diagnostics.

The separate `avp-rtc` region at `0x30240000` read as zero, but the expected
`AppleVirtualPlatformRTC` service was not present in a completed registry walk.
That inactive region therefore does not establish a stopped active RTC or
justify a counter patch. Registry bus addresses also need to be reconciled
with the board's physical mapping before reading registers. No clock model or
native clock behavior was changed in these trials.
