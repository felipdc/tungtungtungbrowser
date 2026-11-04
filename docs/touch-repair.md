# Experimental native touch repair

`scripts/prepare_touch_repair.py` reproduces the framework change validated in
the [native control audit](native-control-audit.md#tap-timing). It operates on
an extracted shared-cache file on a Linux host and writes a separate copy.
It requires only Python 3.11 or newer; it needs neither a GPU nor a running VM.
The source repository contains the repair recipe and synthetic tests, with no
firmware, extracted frameworks or guest binaries.

This repairs a native field-mapping error observed in one tested framework.
The touch handler read the second radius as `radiusX`, rotation as `radiusY`,
and the first radius as `force`. Two corrected AArch64 loads read the two radii
and force from their respective native fields. It does not inject page code
or replace JavaScript properties.

## Exact supported input

| Property | Required value |
| --- | --- |
| Tested iOS build | 26.6.2 / 23G90 |
| WebKit and WebCore metadata | 8624.5.1.10.3 |
| Cache file | `dyld_shared_cache_arm64e.11` |
| File size | 90,800,128 bytes |
| Original SHA-256 | `86cb4b372e883bc57921100cc3aaa694c07fedc67d8d9e931bf0d199eef60e86` |
| Repaired SHA-256 | `be0895305390a4cae7fb9abf721c50fcc233acca82edbac36712332678ebe5d9` |
| Code offset | 12,536,592; two four-byte instructions |
| Signature-slot offset | 90,644,610; one 32-byte SHA-256 hash |
| Signed pages | 5,531 pages of 16 KiB |

The full hash is the compatibility gate. A matching iOS version, filename or
WebKit version alone is insufficient. Unknown hashes and sizes fail before
creating an output directory. There is no option to bypass the hash check.
“Original” means the file retained from the tested custom-firmware guest;
comparison with an unmodified stock image remains unverified.

The recipe writes eight instruction bytes and 32 signature bytes into the
copy; 36 of those 40 bytes actually differ. The changed code page's signature
must change with it. Updating instructions alone fails page-hash validation.
The pinned upstream
[shared-cache signing implementation](https://github.com/Lakr233/vphone-cli/blob/2.14.2/VPhoneExecutable/VPhoneCommand/FirmwarePatcher/DyldSharedCache/DyldSharedCacheCodeSignature.swift)
documents why the custom firmware also needs its existing trust-cache patches.
The offline tool verifies file structure and hashes; it does not establish
that an installation has those firmware prerequisites.

## Prepare and reverse a copy

Keep the extracted source and all generated outputs in a private external
workspace. Set these variables to your own paths; each output directory must
be new and its parent must already exist:

```sh
export TTT_TOUCH_SOURCE=/path/to/extracted/dyld_shared_cache_arm64e.11
export TTT_TOUCH_OUTPUT=/path/to/private-workspace/touch-repaired
export TTT_TOUCH_REVERT=/path/to/private-workspace/touch-original

python3 scripts/prepare_touch_repair.py inspect "$TTT_TOUCH_SOURCE"
python3 scripts/prepare_touch_repair.py apply "$TTT_TOUCH_SOURCE" \
  --output "$TTT_TOUCH_OUTPUT"
python3 scripts/prepare_touch_repair.py revert \
  "$TTT_TOUCH_OUTPUT/dyld_shared_cache_arm64e.11" \
  --output "$TTT_TOUCH_REVERT"
cmp -- "$TTT_TOUCH_SOURCE" "$TTT_TOUCH_REVERT/dyld_shared_cache_arm64e.11"
```

`inspect` opens the file read-only. `apply` accepts only the original hash;
`revert` accepts only the repaired hash. Both leave the input file's contents
unchanged. They reject source symlinks, nonregular files, incompatible file
locks, existing output paths and output directories within this checkout.
Reads and copying use bounded buffers, without loading the whole cache into
a Python byte array.

Before preparation, the tool checks the complete file hash, instruction and
signature bytes, signature layout, and every signed code page. It repeats
those checks on the generated file and rechecks the source before publishing
the output filename. A successful output has both the cache file and
`receipt.json` with `status: complete`, before/after hashes, verified page
counts and the reversible byte spans. The file and receipt are flushed to
storage, followed by the output directory.

A failed attempt keeps its files for inspection and exits nonzero. A `.partial`
file or a directory without a complete receipt is not a prepared result. Use
a new directory for a later deliberate attempt; the tool never overwrites an
earlier attempt. This is artifact preparation, not an atomic guest installation
or a guarantee against every storage-device failure.

## Installation and validation scope

The tool does not mount a VM disk, remount iOS, install files, reboot a guest
or change `Browser.tap`. The runtime's creation and startup scripts do not
install this repair automatically. Integrating installation into firmware
preparation, with its native trust prerequisites and rollback, remains work
on the [roadmap](roadmap.md).

The earlier native experiment applied these exact code/signature bytes,
verified the complete file hash, cleanly rebooted, and verified the hash again
with the System volume read-only. Safari then returned the corrected touch
fields through all four property readers. Restoring the original bytes and
rebooting reproduced the original field error. That live experiment is
separate from the offline preparation tool's validation.

The public tool subsequently passed an offline round trip on that retained
90,800,128-byte cache: inspect the repaired candidate, restore an original copy,
prepare a repaired copy, and inspect it again. Every operation verified all
5,531 signed code pages. A byte-for-byte comparison matched the previously
live-tested candidate, and the restored copy matched the original full hash.
No guest ran during this check. Synthetic tests separately exercise wrong
builds, corrupt signatures, unrelated damaged pages, source changes during
copying, failed writes and preservation of previous attempts.

This framework repair also leaves a separate HID issue unresolved: the pinned
input helper supplies oversized contact radii and retains pointer dimensions
on release. A calibrated native helper passed four single-contact checks.
The public controller now has an optional
[calibrated tap backend](native-touch-helper.md) with separate build and
installation steps. Use the local diagnostic page and
[`check_touch_probe.py`](../scripts/check_touch_probe.py) to verify trusted
touch/pointer fields and inspect the native screenshot. Native request
acknowledgements alone do not establish correct input or page rendering.

Other builds, pressure support, drags, multitouch, stylus behavior and complete
physical-device equivalence remain unverified. This repair makes no claim
about memory savings, boot time or external-site access.

The project-authored script and recipe use this repository's MIT license.
That license does not relicense the user's firmware or other dependencies.
