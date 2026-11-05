# Experimental refresh control for warmed display comparisons

The optional [refresh patch](../patches/qemu-display-refresh-toggle.patch) adds
the host QOM boolean `x-refresh-on-update` to the Apple graphics device. It
defaults to `false` and is not applied by the runtime build. It lets a diagnostic
compare display requests with the same already-running guest, Safari process
and saved tabs. It is not a qualified rendering fix or a performance improvement.

This follows the [cached-surface observations](display-trace.md#corrected-sequence-and-external-browsing).
The earlier environment-controlled candidate failed cold readiness before its
warm comparison. The QOM version can leave the experiment disabled throughout
startup and enable it explicitly afterward. That separates its warm display
effect from changes made before startup; it does not remove warm-up, ordering
or renderer-cache effects from a comparison.

## Behavior and limits

When enabled, a host display-update request marks the frame dirty if no render
is running, then follows the existing encode/readback path. When a render is
already running, the request retains the original completion behavior. This
does not promise the latest guest frame, fix guest composition or substitute
pixels from a native screenshot. It changes no browser property or page script.

Both property accessors run under QEMU's big lock. The setter parses a boolean
and rejects a change while any display has a render outstanding. A rejection
does not change the mode. Changing the property itself does not request a
render, reset dirty flags, clear caches or cancel pending presents. Disabling
it therefore does not undo work already performed with it enabled.

## Build and check

Use the pinned QEMU source
`14c9913d2231080bd234c69421ed4f293a25f767`, retaining the original runtime.
The offline checker requires Git and a C compiler with AddressSanitizer and
UndefinedBehaviorSanitizer:

```sh
python3 scripts/check_qemu_display_refresh.py \
  --qemu-source /external/qemu-candidate \
  --output /external/refresh-check
```

The input display files must match the pinned unmodified versions. Evidence
must be outside both this repository and the supplied QEMU checkout. The
checker copies those files, applies the trace patch to the control copy and the
refresh patch to a second copy, then tests the actual extracted functions.
It leaves the input checkout unchanged and starts no guest or GPU workload.

Fourteen render scenarios passed with the mode disabled and enabled, and with
tracing disabled and enabled. Disabled-mode outputs matched the traced control.
Only the clean-surface request and changed-source-without-present scenarios
changed their render counts. The fixtures also cover overlapping requests,
present/completion ordering, failures, target changes, row padding and reference
counts. Separate tests exercise the real property callbacks with synthetic
visitors, including default/readback, invalid input, reversible changes and busy
rejection across the fixture's display array. These do not exercise real QMP
registration or establish correct native pixels.

To build the candidate, apply both patches in order to a separate runtime source
checkout with the control's dependency pins and build settings:

```sh
git -C /external/qemu-candidate apply --check /project/patches/qemu-display-trace.patch
git -C /external/qemu-candidate apply /project/patches/qemu-display-trace.patch
git -C /external/qemu-candidate apply --check /project/patches/qemu-display-refresh-toggle.patch
git -C /external/qemu-candidate apply /project/patches/qemu-display-refresh-toggle.patch
```

The separate [native floating-point boundary correction](native-fp.md) is still
required for the tested native-FP configuration. Neither display patch replaces
it. Record the complete source diff and resulting binary hash.

A full QEMU build passed. A separate paused `vresearch101` machine realized
`apple-gfx-mmio` using a 64 MiB shared RAM backend, synthetic zero-filled ROM,
null disk backends and Mesa's CPU renderer. QMP reported the boolean property,
read its initial `false`, accepted `true`, rejected a string value without
changing state, and accepted `false` again. The machine remained paused and
quit successfully. No Apple firmware, guest execution or hardware GPU was used.
This verifies property registration on a realized device, not native Safari
behavior; the driver in that mechanical check also differs from the native
browser control's pinned driver.

## Compare within a warm session

First complete and retain the original readiness result. Verify the candidate
binary, actual graphics-device type and initial `false` value. Discover the
device's QOM path with `qom-list`; anonymous device numbers vary across machine
configurations. Do not hardcode a number from another run.

After negotiating QMP capabilities, substitute the observed path below:

```json
{"execute":"qom-get","arguments":{"path":"/machine/unattached/device[N]","property":"x-refresh-on-update"},"id":1}
{"execute":"qom-set","arguments":{"path":"/machine/unattached/device[N]","property":"x-refresh-on-update","value":true},"id":2}
{"execute":"qom-get","arguments":{"path":"/machine/unattached/device[N]","property":"x-refresh-on-update"},"id":3}
```

Retain each original request, response and host timestamp. A busy rejection
leaves the previous mode unchanged; an observation timeout leaves the outcome
unknown. Do not automatically repeat the mutation. Inspect current state
separately before proceeding. Disable it with a new, recorded `false` request
and verify that value after the experimental window.

Record the same page's run identity and DOM/canvas counters in sequential
QMP/native/QMP captures, using equivalent tracing settings and fixed observation
times. Preserve already-visible content that later disappears. Pair performance
samples with the entire workload, including extra encodes and native captures.
Repeated, ordered control/experimental windows are needed to assess cache and
order effects. A faster screenshot reply or a successful callback alone does
not establish faster usable browsing, physical-device fidelity or consistent
external-page behavior. Native qualification remains outstanding.
