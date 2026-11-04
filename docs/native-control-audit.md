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
Safari's accessibility address field and editing state before text entry,
selects the existing text, and reads the complete URL back before pressing
Return. IcliKit's `pasteText` emits a Unicode HID event; it does
not run page code or use the administrative JavaScriptCore bridge. The native
open-URL path is used when Safari is not the verified foreground app. Tab reuse
has not passed sustained page-load validation and is not the default.

Live checks on 2026-11-03 showed that Command-L can expose a visible, enabled
URL field without putting it into editing mode. In that state, acknowledged
text input left the old URL unchanged; the existing readback check prevented
Return. A separate native tap at the field's frame center changed its traits
from `0x2200040000` to `0x2200240000`. The difference is `1 << 21`, the private
`kAXIsEditingTrait` value listed in the
[RPC project's iOS API reference](https://rpc-project.readthedocs.io/en/stable/api_reference/rpcclient.ios.html).
This is an observed private-interface contract for the tested runtime, not an
Apple public API guarantee.

The controller now requires that editing trait. If it is absent, it validates
the native field frame, makes one native tap at its center, and checks the
field again before sending text. The tree must identify the same Safari
process, with exactly one visible, enabled URL field. Invalid geometry,
unconfirmed focus, failed input or mismatched URL readback stops the operation;
none repeats native input.

A later trial returned a truncated accessibility tree after 48 seconds. Its
editing element had the old URL value and expected frame, but an empty
identifier and label. The controller stopped before text entry or Return.
A separate read-only inspection later returned the `URL` identifier and editing
trait for the same Safari process. The omitted identifier was not accepted as
proof of ownership, and the original failed navigation remains a failed result.

For a completed response marked `truncated: true` with no usable URL field,
the controller now permits one additional read after `input_settle`. Both
responses must identify the expected native Safari process. It does not repeat
Command-L, a tap, text entry or Return as part of that read. Ambiguous fields,
owner mismatches, untruncated missing fields and transport errors stop without
another query. A second incomplete response still stops before further input.
The configured transport timeout applies separately to each read; this is a
bounded query count, not an overall navigation deadline. Contract tests cover
recovery, exhaustion, transport failure, owner changes and ambiguous results.
The first live follow-up with this change received a different native
accessibility-query error and stopped before typing; it did not exercise the
automatic recheck branch. Immediate native recovery through that branch is
still unverified.

Two local navigations through the updated SDK rendered the expected canvas,
returned both worker results, and preserved the saved-tab count and active
tab ID. Their fields were already editing, so they did not exercise the new
automatic focus branch. A separate manual focus-and-navigation sequence
passed. A native Tab-key perturbation also left editing active. A native
Escape-key cancellation removed the URL field, and the SDK stopped without
sending text or Return. Unit tests cover successful automatic focus, failed or
unconfirmed focus, and invalid geometry. These initial functional checks ran
after clock maintenance had failed; they establish neither timing accuracy nor
memory savings or sustained external
page reliability.

A subsequent cold boot used the published controller and the longer clock
timeout described below. Its first local navigation exercised automatic focus
recovery: Command-L exposed a nonediting field, one native tap changed its
editing trait, and the SDK verified the complete new URL before Return. The
page displayed the expected canvas, returned both worker results, and reported
no WebGL error or lost context. Saved-tab count remained 32 and the active tab
ID was unchanged. The navigation request itself took 83.9 seconds, including
a 26.5-second foreground query and a 37.5-second accessibility query. This is
functional validation, not a fast navigation result.

Tab metadata came from sequential read-only copies of Safari's database and
WAL, with successful SQLite integrity checks. Those copies are not an atomic
snapshot, and the saved title and URL lagged the displayed local navigation.
The rendered page and its new callback established the navigation result;
database metadata alone did not.

A subsequent external HTTPS navigation in that same tab took 32.0 seconds to
submit. Expected page content was visible in the captures at 90, 210 and 330
seconds after submission. The final database copy retained the same 32 tabs
and active tab ID, with the expected page title and URL. Clock maintenance
had completed 12 offers and remained active at the end of those captures.
These two navigations establish a bounded local and external page check;
they do not establish sustained workload reliability or memory savings.

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

A later run completed its startup and browsing measurements, then a periodic
clock offer timed out while a native accessibility query was also outstanding.
The clock manager retained the failure and sent no further offers. Temporal
overlap does not establish causation: the pinned daemon dispatches requests
concurrently, and its native clock operation has multiple bounded waits.
Clock maintenance under slow native inspection remains a reliability task;
the initial startup barrier does not solve that later failure.

The launcher previously used the same five-second socket timeout for health
and clock requests. The pinned
[`GuestAPI+Clock.swift`](https://github.com/Lakr233/vphone-cli/blob/2.14.2/VPhoneDaemon/Daemon/GuestAPI%2BClock.swift)
first reads automatic-time state, then may make six native offers with up to
0.3 seconds of settling after each. Each state read and offer has a three-second
native wait in
[`vphoned_clock.m`](https://github.com/Lakr233/vphone-cli/blob/2.14.2/VPhoneDaemon/Native/vphoned_clock.m).
Those explicit waits can total 22.8 seconds for the time-only request; lock
contention, scheduling and framework work can add time. Five seconds can
therefore expire before the native operation's own waits finish.

The launcher now gives clock offers a 30-second socket timeout while keeping
startup health checks at five seconds through a separate client. It still
sends each request once and joins the clock worker before shutdown. A local
HTTP transport regression test accepts a valid clock reply arriving after
five seconds, preserves it during stop, and confirms there was only one offer.

A native run with these settings completed a clock offer in 20.004 seconds
during Safari startup and another in 5.612 seconds during address-bar
navigation. Both responses exceeded the former five-second timeout and were
retained without repeating the request. The same run reached verified Safari
foreground at 255.9 seconds; its first image was still a blank page. It also
completed the slow foreground and accessibility queries described above while
clock maintenance remained active. This validates accepting slow native replies
in that run. It does not prove clock accuracy between offers, rule out future
timeouts, explain every earlier failure, or establish faster cold startup.

## Reading the guest clock

`Browser.clock_state()` and the `clock-state` CLI command send `time.clock`
with empty parameters. The pinned daemon's
[`executeClock`](https://github.com/Lakr233/vphone-cli/blob/2.14.2/VPhoneDaemon/Daemon/GuestAPI%2BClock.swift)
uses its read path when both `time` and `automatic` are absent. The controller
validates a finite native Unix timestamp and preserves automatic mode, including
an unavailable value of `null`. It sends no correction, changes no setting and
does not restart clock maintenance after a failure.

A native SDK check completed this read in 26 ms, with exactly one empty-parameter
request and the original failed clock worker unchanged. The guest was then
about 9.2 seconds ahead of the host, despite an earlier explicit correction
whose immediate readback was within 0.24 seconds. Host timestamps bracketed
both reads. This demonstrates why clock observation and synchronization results
must be kept separate; sustained accuracy remains unresolved.

## Tap timing

The pinned Icli [native input helper](https://github.com/owngoal-dev/icli/blob/2fbdb5ee61d8c320732deb9768b22506f5e3d411/Sources/IcliPrivate/IcliPrivate.m)
maps each phase of `input.tap` through screenshot-backed interface geometry.
Its geometry cache expires after one second. A slow refresh between finger down
and finger up can extend contact beyond the requested 50 ms. In one CPU-only
native trial, the request took 11.47 seconds and opened the Home icon's
long-press menu.

`Browser.tap` now uses the daemon's
[`input.touch_sequence`](https://github.com/Lakr233/vphone-cli/blob/2.14.2/VPhoneDaemon/Daemon/GuestAPI+Input.swift)
operation. IcliKit's
[`touchSequence`](https://github.com/owngoal-dev/icli/blob/2fbdb5ee61d8c320732deb9768b22506f5e3d411/Sources/IcliKit/Input/RawInput.swift)
maps all coordinates before dispatching the first contact. The SDK explicitly
supplies `normalized: false` for screen-point coordinates, then one down event
with a 50 ms delay and one up event at the same position. It requires a native
reply reporting two events, point coordinates, a released finger and a
nonnegative measured duration. Transport failures, native errors and malformed
replies never cause a second gesture or an `input.tap` fallback. Contract tests
cover those outcomes, including a timeout after sending the request.

A direct native sequence trial reported 146 ms for the gesture and a released
finger. The complete RPC took 9.56 seconds, including work before contact. It
avoided the long-press menu and triggered application activation, but the app
then exceeded its window-creation deadline. That trial establishes neither
successful page interaction nor faster application startup. The sequence uses
the existing native contact fields; it does not resolve the radius/force
discrepancies tracked in the [roadmap](roadmap.md).

A subsequent x86_64 Safari check used the SDK sequence for automatic address
field focus and loaded the local diagnostic page in the existing tab. The
page rendered its reference canvas, returned both worker observations and
reported no WebGL error or lost context. A separate `Browser.tap` on its button
produced trusted pointer, touch and click events, saved an input callback and
visibly changed the button text. The native gesture reported 156 ms; the whole
RPC took 20.26 seconds. Native geometry preparation and page event delivery
remain slow even when contact itself is brief.

These checks preserved a failed cold-launch request and a later clock-offer
timeout. They validate functional input, not reliable startup or sustained
clock accuracy. Constructed Touch objects returned the expected values through
all four property-reading paths. Native contacts retained the same unexpected
radius and force values through every path, including prototype getters. The
remaining field discrepancy is therefore not explained by the recorder's read
order.

Read-only inspection of the tested guest's compiled frameworks found a more
specific discrepancy. The pointer-event constructor reads the two native radii
and pressure correctly. The touch-event handler instead routes the second
radius into `Touch.radiusX`, rotation into `Touch.radiusY`, and the first radius
into `Touch.force`. An isolated AArch64 instruction probe reproduced that
mapping, including the observed equal-radius, zero-pressure cases. A candidate
change to two loads restored the expected mapping for those cases, a distinct
value case and a released contact.

That candidate subsequently passed a live Safari experiment. The repair checked
the complete shared-cache file hash and both original instruction words, saved
rollback data, and changed the two loads together with the affected code page's
SHA-256 signature slot. The host-side candidate check verified every code-page
slot. The first guest write attempt stopped at a read-only-volume error without
changing bytes. A separate application after the supported native remount path
verified the complete resulting file hash. After a clean shutdown and new boot,
the file still matched and the System volume was read-only again.

Updating the instructions alone is insufficient: the pinned runtime's
[`DyldSharedCacheCodeSignature.swift`](https://github.com/Lakr233/vphone-cli/blob/2.14.2/VPhoneExecutable/VPhoneCommand/FirmwarePatcher/DyldSharedCache/DyldSharedCacheCodeSignature.swift)
explains the guest's page-hash validation and the existing custom-firmware trust
prerequisites. A repair must preserve the code/signature pair in both directions.
Do not apply offsets or signature assumptions to another framework build.

With the framework repair, the existing SDK tap returned equal X/Y touch radii
and zero force through all four property readers. Its oversized contact and
nonzero pointer-up dimensions remained. A separate native HID helper then
tested four calibrated contacts, clearing contact radii on release. Safari
reported radii `5 / 5`, `11 / 11`, approximately `7 / 7`, and approximately
`24.1702 / 24.1702`, all with zero force. Pointer-down dimensions were twice
those radii, pointer pressure was zero, and pointer-up dimensions were zero.
Every tap produced a trusted callback, and all four property-reading paths
agreed. The local page rendered its canvas and completed both worker checks.

A restoration control then wrote back the original instructions and signature
slot, verified the original complete-file hash, and rebooted. The same SDK tap
again returned `radiusX: 4260`, `radiusY: 0` and `force: 4260`. The local canvas
and worker checks still passed. This reversible result establishes the repair's
effect on those touch fields; it does not establish the original file's stock
provenance or the outcome of unrelated browsing workloads.

This is a build-specific experimental result. The public
[offline preparation tool](touch-repair.md) can create and reverse the exact
guarded code/signature repair on a separate extracted file. An optional
[native tap backend](native-touch-helper.md) packages calibrated HID input and
its installation. Runtime creation still does not install the framework repair,
and the default tap path remains the pinned native sequence. A fresh checkout
therefore does not automatically have corrected input.
The nonzero requested-pressure case still produced zero native
pressure; it does not establish force support. Drag, multitouch, stylus/twist,
other framework builds and complete physical-device equivalence remain untested.

The framework metadata agrees between WebCore and WebKit, but metadata alone
does not establish stock provenance. An attempted comparison with the original
System snapshot failed in the read-only filesystem reader. Keep that limitation
and the original instruction bytes with the private evidence. Also check the
platform conditionals when consulting upstream source:
[`Touch.h`](https://github.com/WebKit/WebKit/blob/b3cbb6622217b135a0d82e81adf30ab65d6ad0a2/Source/WebCore/dom/Touch.h)
selects `WebKitAdditions/TouchIOS.h` for iOS. The generic `Touch.cpp` constructor
is not the implementation of that path. Any repair must verify the actual
framework build and original bytes, preserve rollback data, and pass a native
Safari test before adoption. The live tap results above do not resolve the
stock-provenance limitation.

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
The optional [calibrated touch backend](native-touch-helper.md) invokes a fixed
`proc.run` expression there to launch a content-addressed native HID helper.
That session runs outside Safari. It accepts no caller-provided script or page
execution context, and the public generic call method still rejects
`script.eval`. Default navigation and input do not use this bridge.

A guest daemon is re-signed during installation, so its whole-file hash can
differ from the upstream release. For provenance checks, compare its Mach-O
text sections to the pinned release and keep installation-specific hashes
with deployment records outside the source repository.

Runtime validation should cover native device/display coherence, workers,
graphics, locale, TLS and network routing. No JavaScript fingerprint overrides
or page-script injection are part of the browser controller.
