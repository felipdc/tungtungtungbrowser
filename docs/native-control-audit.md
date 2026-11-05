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

## Launch and state-query cost

A foreground-only trial on 2026-11-04 retained a separate tab-reuse failure:
the first address-field query returned native error `-25215` after 38.9 seconds.
The controller had sent Command-L, but had not sent the requested URL or Return.
No input was repeated. Three subsequent, distinct default `goto()` requests
used native URL opening in the same Safari process. They were acknowledged in
2.06, 0.50 and 0.50 seconds; local diagnostic callbacks arrived after 21.07,
15.02 and 17.02 seconds. Both workers, the expected canvas hash and WebGL checks
passed on each page.

Those callbacks did not establish prompt visual completion. The first and third
immediate captures were blank or incomplete; the second showed the diagnostic
canvas. A later capture of the final page, 113.9 seconds after its navigation
request, showed the expected content and saved observation identifier, while
toolbar rendering artifacts remained. The native touch check passed and the
Safari PID/start time remained unchanged. Sequential database copies showed
saved tabs increasing from 10 to 13; their live atomic consistency was not
proven. This establishes an observed latency/rendering gap and tab accumulation,
not sustained navigation reliability or a resource improvement. The default
controller and runtime pin remain unchanged.

Source inspection and a native experiment on 2026-11-04 examined repeated
application enumeration in the pinned daemon. `apps.launch` reads running apps
before and after launching, then queries foreground identity. `apps.foreground`
also combines foreground identity with installed/running-app lookups. Those
operations can add substantial work under CPU translation.

An experimental native helper instead selected Safari by executable path,
checked its process-start identity and bundle ID, and read its RunningBoard
focal assertions. A resident version returned two warm state queries in 23.1
and 16.8 milliseconds after a 419.7-millisecond first query. Those observations
cover a narrow focal-state check; they do not replace the pinned implementation's
multi-app ambiguity handling, establish displayed content, or measure cold boot.

The [subsequent Home/Safari transition failed](crash-diagnostics.md#observed-transition-failure),
including a fresh Safari watchdog termination. The experimental helper is not
shipped as an SDK backend. Fast warm observations are insufficient grounds to
change startup or control defaults.

## Focused address-field query experiment

A later native trial on 2026-11-04 reached verified foreground Safari in
269.6 seconds from its original cold launch. The first local page completed
its diagnostic callback at 339.1 seconds from launch, or 66.8 seconds after
navigation was requested. Both workers responded within their 15-second
limits, although the shared worker took 14.2 seconds. Foreground readiness and
completed page diagnostics are separate milestones; this single successful
cold sequence does not establish repeated startup reliability.

After the original 600-second cold resource window, one distinct default
navigation reused the running Safari process. Its request was acknowledged in
1.20 seconds and its diagnostic callback arrived in 23.19 seconds. Both workers,
the expected canvas hash and WebGL checks passed. The 15-second capture was
blank; the 30- and 60-second captures showed the expected page and matching
saved observation identifier. The saved-tab count increased from 10 to 11 in
sequential database/WAL copies, whose live atomic consistency was not proven.
This is one warm-page observation, not a sustained speed or memory improvement.

An experimental read-only address-field helper then attempted to reduce the
cost of serializing a full accessibility tree. Its local synthetic validation
passed 13 scenarios under address and undefined-behavior sanitizers. On the
native guest, however, it exited at the symbol-binding stage before querying
the field. The existing native tree query subsequently found the enabled,
visible `URL` field in 17.82 seconds. The original comparison remains failed;
no follow-up field tap, URL text or Return was sent. The helper is not shipped
as a controller backend, and its 5.16-second failed RPC is not a successful
query latency measurement.

The proposed helper also verifies process identity rather than foreground
state. Any future use for input must retain the original foreground checks and
ambiguity handling. Reducing attribute reads must not weaken those checks,
accept an unidentified field, or convert a timed-out operation into repeated
native input. Resolve native symbol availability and repeat the read-only
comparison before considering integration.

### Native loader and process-path follow-up

A subsequent read-only firmware export inspection and live symbol inventory
identified the two missing client-context names:

| Earlier lookup | Name resolved in the tested native runtime |
| --- | --- |
| `__AXSetRequestingClient` | `_AXSetRequestingClient` |
| `_AXOverrideRequestingClientType` | `AXOverrideRequestingClientType` |

Mach-O symbol-table names include a leading underscore that is not part of the
corresponding C name supplied to `dlsym`. The pinned upstream implementation
makes these two lookups optional; the experimental helper required them and
stopped. The live inventory called no accessibility functions or native input.
This verifies the names on this build, not compatibility with other releases.

The next comparison also exposed two incorrect test assumptions. An unfocused
Safari toolbar returned a text label rather than a `URL` field; that check
stopped before keyboard input. After one verified Command-L, the original tree
found the field, but the helper's fixed `/Applications` executable-path guard
rejected Safari's application-container installation. The revised diagnostic
accepts an exact expected path from the native bundle/process records and
compares it with the kernel path before and after its query. It still checks
PID, start time and element ownership; matching a basename alone is insufficient.

The corrected helper passed symbol loading and process identity, then failed
its first root accessibility request with native error `-25216`. The existing
full-tree observer had returned the same error in a separate completed request.
The helper visited no elements; its failed RPC took 2.48 seconds, including
0.67 seconds in the root request. No field tap, URL text or Return followed,
and Command-L was not repeated. At that stage, a successful focused field query
and a latency improvement remained unverified. The experimental helper remains outside the
published controller backend.

Offline inspection then found a branch in this firmware's attribute-copy path
that converts `0x10000004` and `0x10004003` to `-25216`. Apple's
[Mach message definitions](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/mach/message.h)
identify those values as send and receive timeouts. This supports investigating
the native messaging deadline; it does not identify the slow endpoint or prove
that a larger budget will resolve the failure. The focused probe allowed
0.15 seconds for its root request, while the pinned original query configures
0.5 seconds. That evidence motivated the subsequent comparison below, varying the root
budget while retaining the overall deadline and original failed requests.

That run reached verified foreground Safari in 225.0 seconds, but its cold
page's shared worker missed the 15-second deadline. A distinct warm navigation
was acknowledged in 0.74 seconds, completed its callback in 17.32 seconds and
passed both worker, canvas and WebGL checks in the same Safari process. The
expected diagnostic canvas was visible at 15 seconds; captures at 30 and
60 seconds showed the matching saved observation identifier. The cold failure
remains separate from the warm success. These sequential observations do not
establish repeated cold reliability, sustained tab reuse or resource savings.

### Root timeout and absent identifiers

A subsequent native run compared 0.15-second and 2-second root-request budgets,
retaining the 0.15-second budget for other attributes, eight-second scan deadline,
20-second process alarm, exact process identity and field-owner checks.

The first attempt stopped before either root query because accessibility was
not already enabled. Calling the published controller to initialize its normal
accessibility path then exceeded the client's 45-second response timeout. That
request was not repeated, and its response remains unavailable. A later daemon
inspection completed through the same native interface lock; this is evidence
that inspection could proceed, not a recovered successful result for the
timed-out request. The experimental helpers themselves do not enable or disable
accessibility switches; normal controller initialization can enable them.

After that inspection, the 0.15-second variant returned `-25216` on its root
request. The 2-second variant obtained 22 elements and read the expected address
field, then stopped on identifier attribute `5019` returning `-25212` for another
element. Apple documents that value as
[`AXError.noValue`](https://developer.apple.com/documentation/applicationservices/axerror/novalue),
meaning the requested value or element does not exist. These sequential
observations support a larger root budget; they are not a controlled timing
comparison or proof of which endpoint caused the earlier timeout.

A revised private scanner skips only an identifier read that returns exactly
`-25212` with no value. It still scans the remaining elements to establish a
unique match. A missing required field attribute, a non-null value paired with
that error, a different error, a duplicate address field or a deadline failure
still rejects the result. Twenty local contract scenarios and five path-format
checks passed with address and undefined-behavior sanitizers before the native
trial.

Three consecutive reads of the unchanged native address field then completed:

| Read | End-to-end RPC | Native scan | Elements visited | Exact address-field matches |
| --- | ---: | ---: | ---: | ---: |
| First | 4.18 s | 0.845 s | 22 | 1 |
| Second | 1.00 s | 0.381 s | 22 | 1 |
| Third | 0.85 s | 0.324 s | 22 | 1 |

Each read accounted for 11 absent identifiers, retained the same kernel process
identity and returned the expected URL, traits and frame. No additional keyboard,
touch, text or Return input accompanied these follow-ups. The results remain
`action_eligible: false` and `foreground_verified: false`: repeated reads of one
unchanged field do not qualify focus transitions, navigation, foreground changes
or the public tab-reuse path. The focused helper is not a published backend.

In that run, Safari reached verified foreground in 232.5 seconds. Both workers
on the first cold page missed their 15-second deadlines; that failure remains
recorded. A distinct warm navigation passed workers, canvas and WebGL checks in
18.12 seconds after a 0.73-second acknowledgment. The expected canvas was visible
at 15 seconds, and the matching saved result was visible at 30 and 60 seconds.
This does not establish repeated cold reliability or resource savings.

The focused reads also left Safari in its address/start-page flow. Their
success therefore does not demonstrate normal page focus or a completed
navigation. Subsequent default external navigations exposed a separate display
failure: one page stayed blank at 30 and 60 seconds, while the next displayed
its expected content. After one native Escape and a distinct navigation to the
first page, its content appeared at 30 seconds, but the 60-second capture showed
the preceding page beneath the new address bar. Overlapping browser chrome
remained visible. All original captures and failed outcomes were retained.
This sequence does not establish that Escape repaired focus, that the later
navigation remained correct, or whether guest compositing or display capture
caused the stale content. Warm control latency and sustained visual correctness
remain separate adoption requirements.

### Field comparison and a failed focus transition

A later run obtained an exact comparison with the published tree query: the
focused helper and controller returned the same URL, traits, frame and owning
Safari PID. The helper RPC took 9.81 seconds, including a 2.70-second native
scan; the subsequent controller query took 7.39 seconds. This first successful
comparison establishes agreement for that state, not a latency advantage.

Two earlier operations in the same run remained failed. The initial published
tree query returned native error `-25216` after 19.79 seconds. After the separate
successful comparison, one calibrated tap targeted the verified address-field
frame. A read after the 0.5-second settle interval then exhausted its two-second
root budget with the same error, visiting no elements. That sequence stopped
before selecting, entering or submitting text. Neither Command-L nor the tap
was repeated.

A distinct later read, after the failed operation had terminated, found the
same field in editing state with the same process identity. Its RPC took
3.61 seconds and its native scan took 1.33 seconds. After fresh foreground
verification, one selection and text-entry sequence entered the new URL. The
readback matched the entire requested URL and editing state before one Return
was sent. That read took 8.78 seconds end to end despite a 0.75-second native
scan. Sequential database/WAL copies retained twelve saved tabs and the same
active-tab record; their atomic consistency was not proven. The local sequence
page showed the new counter range at the scheduled 15-second capture and the
matching completion identifier at 45 and 90 seconds, with Safari's PID and
start time unchanged.

This is one manually separated settled-state follow-up. It does not qualify
automatic recovery, make the original failures successful, or validate the
published tab-reuse path with a new backend. A faster native field scan alone
does not establish faster control: foreground verification in the broader run
ranged from 3.66 to 50.34 seconds, and transport/dispatch time remained material.
Keep initialization, settling, native scan, end-to-end RPC and verified rendered
navigation as separate measurements. Retain all process and foreground guards;
never repeat native input merely because a read timed out.

The run reached verified foreground Safari in 229.4 seconds. Its first local
diagnostic callback arrived 353.9 seconds after boot and passed both workers,
canvas and WebGL checks; this does not establish first-page readiness within
300 seconds. A distinct warm navigation completed its callback in 18.17 seconds
after a 0.68-second acknowledgement. Its 15-second capture still had a blank
canvas, while the 30- and 60-second captures showed the expected canvas and
matching observation identifier. A subsequent default navigation exposed a
[QMP/native screenshot disagreement](display-sequence.md#navigation-and-capture-disagreement).
The bounded crash inventory also contained an `AccessibilityUIServer`
process-launch watchdog termination whose boot-session UUID matched the native
boot identity. Its report described exhaustion of a 30-second allowance. This
is a current-boot failure even though Safari remained alive and later queries
succeeded; no causal link to the later field timeouts has been established.
Repeated startup and warm browsing reliability remain unqualified.

### Bounded read settling and existing URL display forms

A traced follow-up on 2026-11-05 reached foreground Safari in 258.8 seconds.
Its foreground capture still showed blank page content, loading controls and
rectangular toolbar artifacts; foreground verification was not visual readiness.
Its first local diagnostic passed workers, canvas and WebGL checks, returning
at 320.1 seconds from launch. That page's capture showed its canvas and results
but still displayed collection in progress, without the saved observation ID.
A completed first-page display within 300 seconds was not established.
A distinct warm navigation acknowledged in
0.62 seconds and completed its diagnostic callback in 19.62 seconds. The
15-second capture was still collecting, with an empty canvas; the 30-second
capture showed the matching saved result, and the 60-second image was identical.

After one Command-L, the full-tree query took 26.20 seconds. A following focused
query took 4.16 seconds, including a 2.51-second native scan, and matched its URL,
traits, frame and owner. A fresh read before the single focus tap took 1.81
seconds, including a 0.67-second scan. These sequential samples do not establish
a repeatable speedup. Foreground checks still took several seconds.

The post-tap read completed with the editing trait set, but the existing HTTP
URL still omitted its scheme. The private recorder incorrectly required the
complete URL at this pre-input stage and stopped before text or Return. A
separate read-only continuation stopped on another recorder defect: SQLite
tuple rows were compared directly with JSON list rows despite identical saved
tab IDs. Both failures were retained. Neither sent new text or repeated the
focus input. These were recorder failures, not native query errors.

The corrected distinction permits the known existing URL's observed display
form before input, but requires the entire requested URL after typing. It also
normalizes database row representation without changing the compared IDs.
After fresh foreground, process and editing checks, a separate continuation
selected and entered the new URL once. Its first readback returned a completed
native root-query error `-25216`: 5.12 seconds for the RPC and 2.11 seconds in
the root request, with no elements visited. The recorder waited ten seconds
and made one separate read. That read took 1.82 seconds, including a 0.76-second
scan, and verified the exact typed URL, editing trait and ownership before
one Return. Selection, paste, verification, settling and Return together took
18.06 seconds; that excludes prior focus and foreground work and is not a
complete tab-reuse latency.

The continuation retained twelve saved tabs and the same active-tab record,
with Safari's PID and start time unchanged. The new sequence page and the next
default navigation both reached matching final counters and run identifiers
in the 45- and 90-second capture groups. The
[paired display observations](display-trace.md#mixed-focus-and-reuse-observation)
retain initial stale content and incomplete focus rendering. The original
readback remains failed even though the settled read and navigation succeeded.

This is one native exercise of bounded read settling, not sustained recovery
qualification. Only a definite completed root error permitted that extra read;
transport failures and unverified process/field state still stop without more
input. The focused helper and settling remain outside the public controller.
Repeat the corrected sequence without recorder interruptions before adopting
either or claiming an end-to-end latency improvement.

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
its installation. A separate [framework installer](native-touch-installation.md)
provides guarded replacement, full backups and post-reboot verification.
Runtime creation still does not install the framework repair,
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
