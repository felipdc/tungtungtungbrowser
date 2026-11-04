# Guarded native touch repair installation

The experimental installer builds a small arm64 iOS program and invokes it
through the guest daemon's administrative process. No code executes in Safari.
The helper accepts only the two complete cache hashes in the
[repair recipe](touch-repair.md#exact-supported-input). It has no option for
arbitrary patch bytes, paths or unsupported builds.

The pinned cache directory is
`/System/Cryptexes/OS/System/Library/Caches/com.apple.dyld`. The tested guest's
legacy `/System/Library/Caches/com.apple.dyld` path is a symbolic link. The
helper uses the verified real directory and keeps `O_NOFOLLOW` on directory
and file opens; it does not resolve an arbitrary replacement target at runtime.

The installer passed an APFS reversal/application round trip and a post-reboot
native Safari touch check on the exact supported build. Worker startup did not
meet the diagnostic's original deadline; see the validation scope below.

## Prerequisites and build

Use the exact supported custom-firmware VM, with its native trust prerequisites.
Keep its `PatchReceipt.plist` outside this repository. The controller requires
that receipt to declare `kernel-boot-amfi_trustcache` for `kernelcache` and
`txm-boot-trustcache_bypass` for `TXM`. This checks supplied build evidence;
it does not attest that the receipt belongs to the running firmware.

Start from a clean boot with the System volume read-only. The VM must retain
disk changes across a clean shutdown and restart; disposable `-snapshot`
overlays discard installation changes. Retain a recoverable VM backup and allow
space for a complete 90,800,128-byte backup on the Data volume and a complete
staging copy beside the System cache. Each apply/revert attempt retains its own
backup and journal. The helper does not remove earlier attempts.

Build on Linux with Python 3.11+, Clang/LLD 19 and `ldid`, as for the
[native touch helper](native-touch-helper.md). Generated files belong in a new
external directory. The compiler, linker and signer may be selected through
`TTT_REPAIR_CLANG`, `TTT_REPAIR_LINKER` and `TTT_REPAIR_LDID`.

```sh
export TTT_REPAIR_BUILD=/path/to/private-workspace/touch-repair-build
export TTT_REPAIR_RECEIPT=/path/to/private-workspace/PatchReceipt.plist
bash scripts/build_touch_repair.sh "$TTT_REPAIR_BUILD"
```

The build records hashes of the native sources, generated recipe and signed
binary in `build.json`. The controller checks those hashes against its checkout
and verifies the uploaded bytes before making the helper executable available
at its digest-specific guest path. The manifest is an integrity record, not a
cryptographic signature authenticating the builder. Keep the build directory
for later inspection, reversal and reboot verification.

## Explicit installation, replacement and reboot checks

Run these commands where the guest's VSOCK endpoint is available. Every output
directory must be new. `--cid` selects a guest CID; the default is 3.

```sh
python3 scripts/manage_touch_repair.py install --build "$TTT_REPAIR_BUILD" \
  --output /path/to/private-workspace/repair-helper-install

python3 scripts/manage_touch_repair.py inspect --build "$TTT_REPAIR_BUILD" \
  --output /path/to/private-workspace/repair-before

python3 scripts/manage_touch_repair.py apply --build "$TTT_REPAIR_BUILD" \
  --firmware-receipt "$TTT_REPAIR_RECEIPT" \
  --output /path/to/private-workspace/repair-apply
```

`install` uploads and checks the helper; it does not replace the framework.
Select an already installed helper by retaining the same build directory;
do not repeat installation before each inspection or operation.

`apply` first checks the original full hash and read-only System mount. It
remounts System writable, invokes the native operation once and reads back
the durable journal. The helper takes an exclusive lock, makes and verifies
a complete backup, creates a separate staging file, changes the two byte spans,
checks the entire result, copies native metadata, and rechecks the live source.
Only then does it rename the staged file over the live cache path and verify
the replacement. Processes already mapping the old inode continue using it.

A successful apply report has `status: reboot_required`. The controller leaves
shutdown and restart to the runtime operator. Cleanly stop the guest through
the runtime, confirm exit code zero without forced termination, and start the
same persistent disks. Then run:

```sh
python3 scripts/manage_touch_repair.py verify --build "$TTT_REPAIR_BUILD" \
  --previous /path/to/private-workspace/repair-apply/report.json \
  --output /path/to/private-workspace/repair-after-reboot
```

`reboot_verified` requires a different native boot UUID, a read-only System
mount, the expected full cache hash and the same retained native journal.
It does not establish correct Safari rendering or input. Run the
[native touch diagnostic](native-touch-helper.md#validation) and retain its
native screenshot and requested URL separately. `browser_verified` remains
false in installation reports.

To deliberately restore the exact original cache, use `revert` with a new
output directory and the same firmware receipt. It requires the known repaired
hash and follows the same full-backup, staged-replacement, clean-reboot and
verification sequence. It does not restore an arbitrary file from a backup.

## Failure and recovery evidence

The host report is flushed before each request. Guest events are flushed to
`/private/var/root/ttt-touch-repair-v1/<attempt>/events.jsonl`; the complete
backup is `backup.cache` in that directory. The staging file, if it remains,
is `.ttt-touch-<attempt>.staging` beside the cache. The attempt ID is recorded
in the host report. Inspection can retrieve that journal explicitly:

```sh
python3 scripts/manage_touch_repair.py inspect --build "$TTT_REPAIR_BUILD" \
  --attempt 0123456789abcdef0123456789abcdef \
  --output /path/to/private-workspace/repair-recovery-inspection
```

An inspection never creates the lock or modifies the cache. A busy lock means
the original helper may still be running. Keep the original process and its
evidence; do not launch another mutation to resolve a delayed response.

A failed request may leave System writable even when no cache replacement
occurred. A lost response does not establish whether a native operation ran.
The controller makes no automatic write retry, rollback or reboot. Check the
retained request response, journal, boot identity and current full hash before
choosing a subsequent operation. `replacement_performed` identifies failures
after rename when a response is available. An incomplete journal is not a
completed installation. The normal reboot verifier requires a complete
apply/revert report.

Failures before rename preserve the live cache. Failures afterward can leave
the known replacement in place; a deliberate `revert` can recover that known
repaired state after a clean boot. Unknown hashes refuse both apply and revert.
Recovery from unrelated corruption requires the retained VM/file backups and
manual investigation. Atomic pathname replacement and flushes do not guarantee
recovery from every host, storage or power failure.

The native adapter requests ACL, stat and extended-attribute copying with
`fcopyfile`, and verifies mode, owner, group, flags and the complete content
hash. It does not independently enumerate and compare ACLs or every extended
attribute. Host tests exercise real filesystem operations and injected
failures, but do not validate Darwin's ABI or APFS behavior.

## Native validation and limits

The tested Linux runtime used CPU emulation, two application processors,
2 GiB of guest RAM, software rendering and persistent copy-on-write disks.
The public helper restored the original cache, preserved a verified full
backup, and passed verification after a clean reboot. It then applied the
repair, preserved a second full backup, and passed another clean-reboot
verification. Both checks required a new native boot UUID, a read-only System
mount, the expected complete cache hash and the retained journal.

Safari rendered the local diagnostic page with its baseline canvas hash and
no WebGL error or context loss. A single calibrated native contact on that
same page passed all four property-reader checks: touch radii 5/5, force zero,
pointer-down dimensions 10/10 and pointer-up dimensions 0/0. The native
screenshot showed the page and its recorded-touch acknowledgement.

Both dedicated and shared workers exceeded the original 15-second diagnostic
deadline. That failed measurement is retained independently from the passing
installation, rendering and touch checks. These results do not demonstrate
lower memory consumption, faster startup or general worker responsiveness.

A later cold boot of the same persistent trial did not establish Safari
foreground readiness within 600 seconds. Its separate, longer worker
measurement therefore did not start. The display remained on Home with
rendering artifacts, and the container reached its 6.5 GiB memory limit without
an OOM kill. These observations do not establish the cause of the startup
failure. Repeated startup and browser reliability remain unqualified.

Creation and startup do not install this repair automatically. Other framework
builds, physical devices and complete equivalence to a physical iPhone remain
outside the supported validation scope.
