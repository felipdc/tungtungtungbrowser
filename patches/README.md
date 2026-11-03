# Pinned runtime patches

`lavapipe-release-backing.patch` is an optional Mesa 25.0.7 experiment that
discards an ordinary allocation's backing pages before its file range can be
reused. It is not applied by the default runtime build. See the
[driver comparison, regression test and remaining validation](../docs/lavapipe-memory.md).

`vusb-unlink.patch` applies to the `vphone-linux` revision in
`runtime.lock.json`. The creation script checks/applies it before starting
the USB bridge and runs `scripts/check_usbip_protocol.py` against that source.

The unpatched bridge consumed only 24 bytes of a 48-byte UNLINK request and
sent a 24-byte reply. The remaining zero padding became the next command,
causing `unsupported USB/IP command 0` and disconnecting RestoreOS during
its long filesystem verification. During the original failure the guest was alive in
`mtree` when usbmuxd dropped the device and the restore client failed.

The patch follows the
[Linux v6.12 USB/IP protocol](https://github.com/torvalds/linux/blob/v6.12/Documentation/usb/usbip_protocol.rst):

- Consume and emit full 48-byte UNLINK frames.
- Return `-ECONNRESET` when cancelling an active or queued request, and zero
  when its submission has already completed.
- Emit no duplicate submission reply after a successful unlink.
- Abort an active transfer while retaining the endpoint lock, so a queued
  transfer cannot start until the previous one has been cancelled.
- Treat a submission response being written as completed, preventing a late
  cancellation from interrupting a frame.

The wire check reproduced the original stream failure locally, then passed
against the patch. It covers unknown and completed targets, active reads,
queued reads and a following request on the same connection. Firmware setup
also extends the bridge's idle URB deadline from 300 to 1800 seconds to allow
CPU-emulated filesystem verification to finish without an artificial timeout.
The full firmware restore passed on a CPU-rendered Linux host on
2026-11-02: image transfer, `mtree`, APFS sealing, final host acknowledgement
and the resumable `restore` marker all completed. The USB stage handoff
remains an intermittent upstream issue distinct from this framing defect.

`VUSB_TRACE_STAGES=1` adds focused reset, descriptor and abort diagnostics.
The stage follower includes exception types in its retry messages; previously
both timeout and stall exceptions could appear as empty parentheses. This
instrumentation is for the separate intermittent recovery-to-RestoreOS handoff.

`auto-lock-timeout.patch` fixes the upstream first-boot helper's two distinct
limits: its previous `timeout=60` only changed the host socket timeout, leaving
the guest administrative operation at 30 seconds. CPU-only setup exceeded that
limit after migration completed. The patch gives the native OS preference
operation 120 seconds and the host transport 150. It does not change page code.


`qemu-sprr-user-mask.patch` applies inside the pinned QEMU source tree. The
runtime build script checks/applies it after fetching sources. On the M1-style
CPU layout, the user permission mask is `C3_0`; the original handler read
`C3_1`, which is the privileged mask. Safari's JIT memory remained read-only
after WebKit requested a writable scope, crashing its content process. The
patch preserves the older A13 mapping and changes only the M1 path.
The rebuilt emulator now performs the requested RW/RX transitions, and Safari
loaded our diagnostic page with native JavaScript, workers, canvas and WebGL.
The modified upstream file is AGPL-3.0-or-later; see
[third-party notices](../THIRD_PARTY_NOTICES.md). This patch changes native
emulator permission handling and does not modify browser page code.
