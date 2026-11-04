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

Native validation of this migration is in progress. It is not a supported
default storage mode or a demonstrated startup/resource optimization.
