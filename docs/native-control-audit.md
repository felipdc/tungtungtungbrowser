# Native browser control audit

Source inspection on 2026-11-02. Live Safari behavior remains a separate
check; this audit establishes the control path, not fingerprint parity.

## Pinned sources

- vphone-linux `888d17ca17268aaecd277c64f2d95385b184744b`.
- vphone-cli release `2.14.2`.
- Its [workspace dependency lock](https://github.com/Lakr233/vphone-cli/blob/2.14.2/VPhone.xcworkspace/xcshareddata/swiftpm/Package.resolved)
  pins `owngoal-dev/icli` 0.10.0 at
  `2fbdb5ee61d8c320732deb9768b22506f5e3d411`.

## Navigation

1. `tungtungtungbrowser/guest.py` accepts only absolute HTTP(S) URLs without
   embedded credentials. It explicitly supplies Safari's bundle identifier.
2. vphoned's [`GuestAPI.swift`](https://github.com/Lakr233/vphone-cli/blob/2.14.2/VPhoneDaemon/Daemon/GuestAPI.swift)
   handles `apps.open_url` by calling `openAppURL(url, bundleID:)`.
3. IcliKit's [`Apps.swift`](https://github.com/owngoal-dev/icli/blob/2fbdb5ee61d8c320732deb9768b22506f5e3d411/Sources/IcliKit/Apps/Apps.swift)
   delegates to `icli_open_url_in_app`.
4. On iOS, [`AppHandlers.m`](https://github.com/owngoal-dev/icli/blob/2fbdb5ee61d8c320732deb9768b22506f5e3d411/Sources/IcliPrivate/AppHandlers.m)
   uses LaunchServices' resource-opening operation for the supplied app,
   with native URL-opening fallbacks. It does not evaluate page scripts.

The operation can return success after starting an asynchronous open.
Therefore the controller's evidence manifest deliberately leaves navigation
unverified until screenshots and page content have been inspected.

## Reusing a tab

The higher-level `Browser.goto` defaults to explicit native URL opening.
Its experimental `reuse_tab=True` option uses the pinned native `input.key` and
`input.paste` methods when Safari is verified in the foreground. It confirms
Safari's accessibility address field before text entry and reads the URL back
before pressing Return. IcliKit's `pasteText` emits a Unicode HID event; it does
not run page code or use the administrative JavaScriptCore bridge. The native
open-URL path is used when Safari is not the verified foreground app. Tab reuse
has not passed sustained page-load validation and is not the default.

## Native time and startup ordering

Source review and live ordering checks on 2026-11-03.

The pinned daemon's
[`GuestScreenUnlock.swift`](https://github.com/Lakr233/vphone-cli/blob/2.14.2/VPhoneDaemon/Daemon/GuestScreenUnlock.swift)
calculates unlock deadlines with Foundation `Date`. Moving the guest wall clock
forward while this operation is running can therefore expire its deadline.
The native operation may send several Home-button events within that deadline;
the controller's once-only rule concerns the unlock RPC, not the number of
events generated inside the daemon.

When launcher clock maintenance is enabled, `scripts/measure_ready.py` waits
for the launcher's first completed automatic-time offer before making its own
health, unlock or app-launch requests. It validates the launch identity and
retains the original boot deadline. A stale, failed or manual-time state stops
readiness before input. The observer does not send a duplicate clock offer.
Periodic offers can continue after this initial barrier; it does not replace
the daemon's wall-clock deadlines with monotonic deadlines.

One live cold-start check completed the initial correction before unlock and
subsequently obtained native Safari foreground verification. Its first capture
still contained incomplete page rendering and address-bar artifacts, so this
checks startup ordering rather than page readiness or improved startup speed.

The launcher also joins its clock worker before requesting native shutdown.
The final clock state and native shutdown result are recorded in `exit.json`.
This sequencing has been exercised on a live guest: the worker reached
`stopped` before shutdown was requested, and iOS exited without forced
termination. This does not establish behavior for every shutdown failure.

## Screen evidence

The same vphoned dispatcher handles `screen.screenshot` through
`takeScreenshot(base64: true, nativeResolution: true)`. The pinned Icli
[native implementation](https://github.com/owngoal-dev/icli/blob/2fbdb5ee61d8c320732deb9768b22506f5e3d411/Sources/IcliPrivate/IcliPrivate.m)
uses the native screen image path, including `_UICreateScreenUIImage`, and
encodes JPEG. It does not take the screenshot through browser JavaScript.

When a local QMP socket is configured, `tungtungtungbrowser/display.py` instead
captures the rendered display through QEMU's `screendump` operation. It passes
an anonymous write-only file descriptor using `SCM_RIGHTS` and `add-fd`, reads
raw PPM, and removes the descriptor set. After closing the monitor connection,
the controller validates the RGB geometry and row stride and encodes PNG. The
pinned QEMU's [PPM writer](https://github.com/YahiaAngelo/qemu-vphone/blob/4da65a96e039413faff00ed52e3d956bf931bfd9/ui/ui-qmp-cmds.c)
includes pixman row padding; the controller removes that padding while retaining
every RGB pixel. The QMP session has a deadline, raw and encoded output have
size limits, and the final PNG is checked for structure and checksums. This path
avoids guest image encoding and preserves the framebuffer's pixel dimensions;
it does not determine UIKit's coordinate scale. It also includes system dialogs
and overlays visible on the display. The QMP socket remains a privileged local
control interface and must not be exposed as a browser-facing endpoint.

A foreground bundle identifier and a valid screenshot do not prove that the page
rendered. The startup report records these separately and keeps visual review
pending. Expected page content must be checked separately.

## Native TLS observation

A passive capture on the guest's network interface on 2026-11-03 UTC recorded
a ClientHello for the HTTPS origin requested through native Safari navigation.
It offered `h2` and `http/1.1` through ALPN and advertised protocol versions
`0x0304` and `0x0303` among its supported-version values. The experiment did
not install a TLS interception certificate or modify page scripts. Packet
captures, destination details and deployment configuration remain outside
this source repository.

This establishes a guest-side handshake observation. It does not establish
which application protocol was negotiated, what the destination observed
after the configured network route, or equivalence with a physical iPhone.
Those require separate measurements. An accepted native open request and a
completed handshake also do not prove that the requested page rendered.

## Scope and remaining checks

The source search of `VPhoneDaemon`, `VPhoneGuestComponents` and the firmware
patcher found no `evaluateJavaScript`, `WKUserScript`,
`WKUserContentController` or `navigator.` injection calls in those paths.
This is a bounded source check, not a proof about every dependency or the
behavior of prebuilt guest binaries. The controller's contract tests
separately check the native Safari request and reject scripting methods and
non-web URL schemes before transport.

vphoned also offers a general administrative JavaScriptCore bridge upstream.
This project's browser controller does not expose or invoke it. Its presence
is not evidence that scripts are being injected into Safari.

A guest daemon is re-signed during installation, so its whole-file hash can
differ from the upstream release. For provenance checks, compare its Mach-O
text sections to the pinned release and keep installation-specific hashes
with deployment records outside the source repository.

Runtime validation should cover native device/display coherence, workers,
graphics, locale, TLS and network routing. No JavaScript fingerprint overrides
or page-script injection are part of the browser controller.
