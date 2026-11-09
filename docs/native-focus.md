# Native address-field readback

`NativeFocusReader` reads Safari's focused address field and can discover a
unique native address control when focus is absent. It lets tab-reuse navigation
verify the typed URL even when the software keyboard changes the field's frame.
It sends no touch or key events and evaluates no JavaScript in Safari.
Configure `Browser(address_field_reader=...)` for tab-reuse navigation and
owned-tab cleanup. Those operations now require a reader; the provided native
helper is the supported implementation.

The current implementation uses the guest's native AXRuntime first-responder
attribute, `3002`, verified against `AXElement.firstResponder` on iOS 26.6.2.
The native query returns the element's process ID, identifier, value, traits,
label and frame. The reader rejects missing attributes, a different process,
query errors and invalid geometry. This is an experimental interface tied to
the tested native runtime, not a general compatibility guarantee for iOS.
The native `kAXErrorNoValue` result with no element is treated as absent focus;
it is distinct from a failed query.

The helper runs as the guest's mobile user, UID/GID 501, before loading the
accessibility frameworks. When launched by the administrative daemon as root,
it first drops supplementary groups and changes its user and group. It refuses
other user contexts. This keeps the focused-field query and its preference
readback in Safari's user context. The change applies only to the short-lived
helper process.

## Install and use

Build on Linux with `clang-19`, `ld64.lld-19` and `ldid`. The build uses minimal
Darwin declarations; it does not require an Apple SDK. Override tool names with
`TTT_FOCUS_CLANG`, `TTT_FOCUS_LINKER` and `TTT_FOCUS_LDID` if needed.

```sh
bash scripts/build_focused_element.sh /absolute/external/focus-build
python3 scripts/install_focused_element.py /absolute/external/focus-build \
  --cid 3 --output /absolute/external/focus-deployment
```

The installer verifies the source manifest, Mach-O identity, executable hash,
upload readback and a native capability check. Each deployment has a unique
executable path; existing deployments are preserved. Keep generated binaries
and installation records outside the repository. Installation requires the
prepared guest's administrative daemon and supported platform entitlements.

```python
from tungtungtungbrowser import Browser, NativeFocusReader
from tungtungtungbrowser.guest import Guest

guest = Guest(cid=3, timeout=70)
reader = NativeFocusReader.from_deployment(
    guest, "/absolute/external/focus-deployment/install.json"
)
reader.check()  # Loads native symbols; sends no input and makes no AX query.
browser = Browser(guest=guest, address_field_reader=reader)
browser.goto("https://your-page.example/", reuse_tab=True)
```

Use this same `browser` with `InspectorSession(browser=browser)` for page
conditions, HTML and network observation. Configure any separately required
native touch helper on `guest` as described in the [native touch helper guide](native-touch-helper.md).
The focus helper has no listening port. Each query starts a short-lived guard
process that owns temporary preference changes and exits after restoration.
A retained deployment can be checked again after a guest reboot. Rebuild and
install a new helper to get the guard; an older deployment does not acquire it
by updating the Python package alone.

## Navigation behavior and limits

New helper builds advertise `address_resolution: "guarded-v1"` in their native
capability check. `from_deployment` enables the combined resolver only when the
installation record contains that capability. A direct reader can call `check()`
to discover it. Older helpers retain their existing first-responder behavior;
updating Python alone does not add a native helper mode.

For a capable reader, `Browser` sends Command-L once and requires its native
acknowledgment. The resolver first queries the native first responder. Only
absence or a completed AX `-25216` error with no element permits native address
control discovery. Discovery requires a complete scan of at most 512 elements
and exactly one enabled URL control owned by the expected Safari process.
Partial scans, ambiguity, missing attributes and invalid geometry are errors.

Each guarded resolution permits at most three eligible address scans, with
400 ms between scans and a ten-second accepted-query budget. A completed
resolution that proves absence or an unavailable root with verified restoration
can receive one fresh guarded resolution after 100 ms. A completed AX `-25216`
partial scan can also permit that fresh query when it returned no element,
found no matching or foreign elements, and has valid counts and restoration
evidence. The partial result is discarded. Only a fresh validated responder or
complete unique address scan can authorize further action; partial scans are
never retried within the same guard.

A completed first-responder AX `-25215` with no element can also receive one
fresh guarded resolution after verified restoration. Its trace must report
only that failed query, with matching durations and an empty scan. The native
resolver still stops at this error within its guard; the Python reader discards
the result before starting a new invocation. Other first-responder failures
remain terminal and retain their native AX error instead of being reported as
missing responder-source evidence.

A completed responder whose label, value, identifier and traits all return
AX `-25216` with null values can also receive one fresh guarded resolution.
This requires verified ownership and restoration, an otherwise successful
first-responder query, an empty scan and a valid frame response. Every attribute
from the failed response is discarded, including the frame.

The same bounded recovery applies when label and identifier both return
`-25212`, or both return `-25205`, with null values, while value, traits and
frame succeed. Apple defines `-25205` as
[`attributeUnsupported`](https://developer.apple.com/documentation/applicationservices/axerror/attributeunsupported).
The retry does not assume that either attribute will become available: it
discards this responder and requires a fresh complete result. This requires
a nonempty string value, valid positive integer traits and valid frame geometry,
as well as the ownership, restoration and responder-trace checks above. The
partial value and frame never authorize input; the entire response is discarded
before the fresh query. Different errors on the two names, other mixed
attribute failures, malformed values and
invalid geometry remain terminal. Ordinary first-responder reads do not gain
either retry.

A completed native budget error `-30` can use the remaining fresh resolution
when the discarded result contains no element and only complete, empty scans
with no foreign elements. This requires a valid trace, matching process ID,
verified switch restoration, a first-responder lookup under ten seconds and
a total query duration from ten to under twenty seconds. Every scan and its
duration must agree with the final result. The expired result remains a failed
attempt; it supplies no field, geometry or input authorization. A fresh result
must satisfy the ordinary ten-second accepted-query budget.

Both invocations must verify the original accessibility settings and process
identity. Missing replies, failed processes, ambiguous or foreign partial
results, expired field data and unrestored state remain terminal. The existing
two-invocation limit and 22-second guard command wait are unchanged. Each
invocation now has separate caller phase alarms, described below; these do not
extend a caller's HTTP timeout or an enclosing session deadline.

A targeted warm test explicitly substituted a previously recorded, restored
budget failure for the first successfully completed native reply, preserving
both replies. The reader discarded that result and obtained a fresh complete
native result before navigation. URL, response, rendered HTML, native pixels
and owned-tab cleanup passed; the existing tabs remained through sixty seconds.
The test used a private batched-attribute diagnostic helper and the unchanged
public keyboard helper. It validates the Python recovery path, not a naturally
reproduced timeout or cold reliability. The original cold attempt remains a
failure, and fresh cold qualification is separate.

A subsequent cold check with durable private phase records hit the process
alarm during restoration. Automation enable took 13.7 seconds; native reads
finished and restoration began 19.55 seconds after process entry. The caller
was terminated, while the independent guard completed restoration at 21.13
seconds. A separate audit confirmed restored settings and no remaining helpers.
That build used one 20-second alarm for setup, query and restoration together.
The failed process supplies no usable result and does not enter the completed-error
recovery above.

The current helper gives setup 20 seconds, rearms a 20-second query alarm only
after the guard acknowledges enablement, and arms a ten-second restoration alarm
before receiving the final frame and reaping the guard. This explicitly raises
the caller's total nominal allowance to at most 50 seconds. The independent guard
has no process alarm and can outlive the caller to restore settings. A hung
setup, query, restoration receive or child reap still terminates the caller.
The accepted native resolution budget remains ten seconds, with unchanged
250 ms messaging timeouts and bounded scans.

The capability check advertises `query_timing: "phased-v1"`. A reader that verifies
this capability uses a 60-second administrative `script.eval` execution deadline
for queries; capability checks and older helpers retain 30 seconds. Use
`from_deployment` with the new installation record or call `check()` on a direct
reader. The reader never changes `Guest`'s HTTP timeout. Configure it explicitly
(for example, `Guest(timeout=70)`) to allow the complete execution window and
response delivery; a shorter caller deadline remains in effect. A timed-out
request or failed native process is never replayed.

Linux lifecycle tests use the production guard and phase-alarm code with scaled
alarms and file-backed preferences. Slow setup/query/restoration work terminates
under the old shared alarm and succeeds with separate alarms. Deliberately hung
setup, query, restoration and reap all terminate the caller while the independent
guard restores settings. The tests also cover an inherited blocked/ignored alarm
and the distinction between execution and HTTP timeouts.

A controlled warm native comparison added the same fixed delays to both builds:
eight seconds in setup, eight before query work and five before restoration.
The shared-alarm control terminated at 20.38 seconds during restoration; the
phased candidate completed in 21.91 seconds with a verified owned editing field.
Independent audits found both settings restored and no remaining helpers after
each case. The deliberately delayed builds are private validation artifacts;
the production helper contains no injected delays or attribute batching.

The uninstrumented candidate then opened a warm session in 19.62 seconds and
navigated in 23.23 seconds. URL, HTTP response, rendered HTML and native JPEGs
immediately and fifteen seconds later passed. Original owned-tab cleanup and
sixty-second preservation of the existing tabs also passed. Cold reliability remains open:
the first focus attempt on the fresh guest returned restored, completed queries
but no owned URL field, so no text or Return was sent. That run also had a fixture
transfer pause before helper installation and is not an uninterrupted cold timing
comparison. The full external-site workflow was not repeated for this fix.

A returned first responder must be Safari's URL field. If it is already editing,
the browser proceeds to URL verification. A discovered control is not proof of
keyboard focus, even if it carries an editing trait. The browser verifies its
bounds against the native screenshot and rechecks the foreground Safari PID,
then sends at most one tap and requires a fresh editing first-responder read.
Subsequent typed-URL verification and owned-tab close still use exact expected
addresses. Shortcuts, taps, paste, Return and close commands are not replayed.
A navigation acknowledgment requires a separate page condition.

`reader.resolve_address_field(pid, max_attempts=1)` disables the second complete
resolution for a direct caller. Its default limit is two; this is separate from
`max_read_attempts`, which governs ordinary first-responder reads. The latest
resolution and its bounded attempt history are exposed as `last_resolution` and
`last_resolution_attempts`. `find_address_field(pid)` performs one independent
native discovery; its result alone never establishes editing focus.

Like the existing native accessibility queries, this helper may temporarily
enable the system accessibility and automation switches. A separate guard saves
the mobile user's original values, enables the required switches, and restores
only switches it enabled. The querying process never writes those preferences.
The Python reader requires matching before/after states, and a normal helper
return additionally requires the guard's restoration acknowledgment and clean
exit. A terminated query is still an error; no automatic input retry follows.

The guard uses a pipe to detect completion or query-process death. It creates a
separate process session before changing settings, so terminating the caller's
process group does not terminate restoration. Missing completion, pipe EOF,
invalid completion and a 22-second guard deadline all lead to restoration.
No framework code runs in a signal handler or in a child produced by `fork()`.
A nonblocking per-user lock covers the original-state read through restoration;
an overlapping guarded query fails without saving another query's temporary
settings as its own baseline. The empty lock file remains in the mobile user's
Library directory; the OS releases the lock when the guard closes or exits.

This guard cannot restore settings if it is itself forcibly killed, the guest
stops, or the native preference service cannot complete restoration. A failed
or missing guard acknowledgment does not qualify restoration. Do not run older
unguarded helpers concurrently with the new one. The native messaging timeout,
query alarm and guard deadline do not guarantee a precise host wall-clock
duration under emulation.

The administrative launcher was observed to pass a blocked signal mask that
included `SIGALRM`. The earlier helper's `alarm()` therefore did not terminate
a deliberately stalled query. The helper now clears its inherited signal mask
and restores default alarm and child-exit dispositions before starting either
process. This changes only helper processes. A Linux regression test starts the
helper with an explicitly blocked and ignored alarm to exercise that boundary.

A later cold-guest trial's first focused-field query exceeded the administrative
30-second deadline. An independent mobile-user read afterward found both
switches enabled. There was no independent sample immediately before that query;
the trial restored the isolated baseline's off/off state and verified it with a
separate read. That failure motivated the separate guard. Retain the original
failure; successful normal returns do not qualify restoration after termination.
Do not assume that retrying a query will repair settings left by a killed helper.

Linux process tests compile the production guard with file-backed preference
callbacks. They cover all four original switch combinations, successful reaping,
query termination by SIGALRM/SIGKILL, an inherited blocked/ignored alarm,
caller process-group termination, the guard
deadline, overlapping queries, EOF, invalid completion and failed restoration.
These tests establish the process lifecycle, not iOS preference behavior; native
qualification must independently read settings after the fault and exercise
normal address-field reads and owned-tab cleanup.

An iOS 26.6.2 fault trial used private test variants with the same guard source
as the production build. One terminated the query with `SIGKILL` after the guard
enabled the switches; another stalled the query with a two-second alarm. Both
terminated on the expected signal. A separate mobile-user reader found the
original off/off state after each fault, in the same guest boot. Independent
process checks found all four query/guard PIDs absent. The final-build alarm
exchange took 3.21 host seconds and the forced-kill exchange 1.39 seconds; these include
administrative execution and are not normal focus-query latency measurements.

One narrow read recovery is enabled by default: a completed helper returning
AX error `-25216`, no element, the requested PID and matching before/after
switch states may be read once more after 100 ms. This repeats only the query;
Command-L, taps, paste and Return are not replayed. A transport timeout, failed
process, missing state, wrong PID, un-restored switch or any other error stops
immediately. Valid absent focus remains an absent result and prevents input.

Set `max_read_attempts=1` in `NativeFocusReader` or `from_deployment` to disable
read recovery; the supported limit is one to three attempts. The bounded
`reader.last_read_attempts` list records the most recent query's outcomes and
durations, including a failed first read when the next succeeds.

A guarded native workflow returned `-25216` followed by valid absent focus after
Command-L. Navigation stopped before sending text; subsequent owned-tab cleanup
read the field and closed the tab successfully. An experimental three-read
budget that also re-read absent focus did not resolve the failure: another run
still ended without a focused field. That absence-retry change was not retained
in the SDK. Keep absent focus explicit rather than adding unsuccessful queries
to the default path. A separate successful fixture/external-page workflow used
first-attempt field reads throughout; it does not establish recovery from absent
focus. All four owned tabs from this trial were closed and verified absent
in fresh Inspector listings, preserving the original tab.

That boot produced one complete fixture-to-external-page sequence in 102.1
seconds, two navigation failures on absent focus, and one rejected focus action
before a dynamic link appeared. This is not sustained workflow reliability.
The guest's cold readiness attempt had already failed at the screen-unlock
response deadline after 300.3 seconds, before these helper tests began.
Peak container memory reached 6.50 GiB and recorded 11,449 memory-limit events,
with no OOM kills. The longer workload and extra diagnostics prevent attributing
that pressure to a single change. No resource reduction is established.

The mobile-user build passed native installation and address-field readback in
an owned-session trial. It verified both the bootstrap address and the new
destination before Return, with matching off/off accessibility states. An
earlier owned attempt returned `-25216` before paste; its later cleanup read
succeeded and the owned tab was verified closed while preserving the original
tab. That later workflow was interrupted after navigation, so its final
owned-tab cleanup remains unverified. A subsequent cleanup exercised the bounded
second-read path: the first completed read returned `-25216` in 4.24 seconds and
the second returned the verified address in 2.28 seconds. Both reported matching
off/off states, and only one Command-L and one close command were sent. A later
independent listing confirmed the owned tab absent and the original preserved;
a separate state read again found both switches off. This does not qualify
recovery from a killed or timed-out helper.

The query is read-only with respect to page content. It is not an implementation
of selector clicks, scrolling, visibility checks or arbitrary DOM geometry.
Native focused-field lookup and foreground verification remain possible
failure points. This avoids the daemon's flat-tree fallback; it does not
establish repeated cold-boot or external-site acceptance.

The flat `ui.tree` daemon operation is no longer an implicit SDK fallback. A
native diagnostic call left both system accessibility switches enabled after
they had been observed off, and the pinned daemon source does not restore them
when that operation returns. The test restored the prior state and verified it
separately. This finding does not establish the cause of an external site's
behavior. The observations below describe earlier implementations and retained
trials; they do not override the current requirement for a scoped reader.

## Native observations

In one warm session, the public `Browser`, `NativeFocusReader` and
`InspectorSession` completed two consecutive external-page navigations,
committed-URL waits, CSS and XPath lookups, HTML and response-body reads,
screenshots and cleanup. Native navigation acknowledgements took 22.4 and 21.2
seconds. The subsequent committed-URL waits took 6.8 and 4.4 seconds, and HTML
serialization took 0.33 and 0.64 seconds. These are samples from one session,
not latency guarantees or a broad reliability qualification.

A separate rapid Escape/Command-L probe, without the normal input settling
delay, failed its immediate focus query and sent no text or Return. A later
read found the editing field without repeating input. The failed transition is
retained separately; the two-page result does not qualify zero-delay control.

The field reader independently verified an editing address field before and
after paste, when its native frame moved from y=770 to y=488. The complete
application-JSON-to-second-page-and-selector-click workflow remains unfinished.

At the end of the run, the runtime cgroup reached a 6.50-GiB peak against its
6.5-GiB limit and recorded 797 memory-limit events, with no OOM kill. These are
cumulative values for the whole boot and investigation, not memory attributed
to the focus helper or the two navigations. No memory reduction is established.
Guest shutdown completed in 7.28 seconds without forced termination; the
parent overlays, parent cache and six original baseline files were unchanged.

A subsequent cold boot reached foreground Safari in 262.3 seconds, but its
first two address-bar attempts failed before text or Return: one native helper
request exceeded its 30-second administrative deadline, and another returned
AX error `-25215`. Later in that same boot, after separate local-page work, the
public focus reader and native address-bar path navigated to an external page
in 28.3 seconds, followed by an 8.6-second committed-URL wait and a rendered
screenshot. The later warm success does not qualify the failed earlier control
attempts. Read-only samples found both accessibility switches off after the
first failure; those samples do not establish their state throughout the failed
request or explain its cause.

## Targeted resolution validation (2026-11-09, trial 136)

The first focus operation on this guest boot ran in a separate session after
the initial cold Inspector connection failed. One acknowledged Command-L was
followed by a 17.17-second guarded invocation ending in complete absence with
restored off/off settings. The second guarded invocation found a unique enabled
URL control in 1.40 seconds. One tap and fresh editing-URL verification allowed
navigation to finish. The requested fixture URL, DOM condition, rendered HTML
and native screenshot passed; original cleanup closed only the owned tab in
11.87 seconds. The pre-tap image still showed Safari suggestions without its
drawn toolbar; the successful page image was captured before Page.snapshot.

The integrated public `Browser` path was then tested on the same warm device
with a newly built helper advertising the capability. Session creation took
17.73 seconds, fixture navigation 21.54 seconds, and original cleanup 10.76
seconds. Native pixels and the exact requested URL were verified. All 521
public-candidate tests passed, including compatibility, malformed capability
evidence, bounded native scans, restoration and unrepeated-input checks.

The first cold connection still exhausted four completed native interruptions
in 68.42 seconds before creating a tab. The later focus and warm integration
passes do not qualify cold startup or sustained end-to-end reliability. This
batch used targeted checks; the full external-site workflow was not repeated.

A subsequent targeted product navigation reached the expected URL, DOM
condition and rendered HTML in the same warm guest, but its native screenshot
was black. Original cleanup then rejected a missing guard restoration reply:
the four-second receive window expired and the guard exited with an
undeliverable final frame. No close key was sent. Independent checks later
found settings off/off and no surviving helper. A separate fresh, restored
editing-URL read authorized the first close key and closed only the owned tab.
This remains a failed visual and original-cleanup case. The address-resolution
change does not increase that restoration-reply timeout or qualify rendering.


A separate restoration-reply fix increases only the final pipe receive window
from four to ten seconds. At that stage the main query alarm remained 20 seconds
and the guard command wait remained 22 seconds; the latter is not a hard process
deadline because the guard must restore settings. A full valid restoration
frame and clean guard exit are still required. No browser input is retried.

On the same warm guest, a controlled six-second delay before restoration made
the four-second receiver miss the final frame and fail. The ten-second receiver
accepted the complete frame after 7.35 seconds and verified clean guard exit.
Independent reads found both switches off after each case. Local tests also
cover a restoration delay beyond the new window, which must still fail. All
522 candidate tests passed. These fault-injection builds were used only for
validation; the production helper contains no injected delay.

A subsequent production-build product case created a session in 19.02 seconds,
navigated in 22.37 seconds and completed original owned-tab cleanup in 39.04
seconds. The exact URL, DOM condition and HTML passed, but native pixels were
still black. This validates normal cleanup alongside the controlled reply
timing test; it does not qualify product rendering or cold session startup.

## Restored partial-query recovery (2026-11-09, trial 138)

The first native focus operation on a fresh guest returned AX `-25216` after
visiting one of 24 elements. Its process completed and restored settings, but
the public reader stopped before any tap or text. Separate verified recovery
closed only the owned tab. The revised reader permits one fresh guarded query
for this specific failure without repeating the shortcut or accepting partial
evidence.

A controlled native comparison injected an element-owner error after retrieving
the real root list. The original reader stopped at that partial result. The
revised reader discarded it, then read all 51 elements and found one enabled
Safari-owned address control. Both cases restored settings and left no helper
process. A different injected error encountered foreign page elements and was
ineligible; that diagnostic failure is retained. Thirty targeted reader,
browser and guard tests passed, including partial-result exhaustion and rejection
of ambiguous, foreign, malformed, expired or unrestored evidence.

Normal warm integration then passed exact fixture URL, DOM condition, rendered
HTML, native pixels and original owned-tab cleanup. Creation took 18.77 seconds,
navigation 21.00 seconds and cleanup 10.49 seconds. That case used the unchanged
production native helper and succeeded on its first resolution read; the
controlled comparison separately exercised recovery. Cold Inspector startup
still failed, and the revised reader has not yet passed a first-focus test on
a fresh boot. No full external-site workflow was repeated.


## Completed first-responder error (2026-11-09, trial 139)

The first focus operation on the next boot returned AX `-25215` with no element.
The process completed, its guard restored off/off settings, and its trace
reported only the failed first-responder query. The previous reader rejected
that legitimate error trace as missing responder-source evidence. Navigation
stopped after one Command-L, before tap, text or Return. Separate recovery's
immediate listing showed the owned tab gone and the original present; the
later preservation failure is recorded below.

The reader now preserves such native errors and permits one fresh query for
the observed restored `-25215` case, within the existing two-invocation limit.
In a controlled native comparison, a diagnostic helper performed the real
first-responder query, released any returned element and returned this error.
The previous reader stopped. The revised reader discarded the failed result
and used the unchanged production helper for a second query, which read all
16 native elements and found one enabled Safari address control. Both cases
restored settings and left no helper process. The diagnostic helper remains
outside production source and builds.

Thirty-four targeted reader, browser and restoration tests passed, including
exhaustion, opt-out, malformed evidence and a single browser shortcut. Normal
integration created a session in 18.90 seconds, navigated in 22.87 seconds and
closed its original owned tab in 12.97 seconds. Exact URL, response, DOM, HTML
and native focus checks passed. The native screenshot was black, so this case
does not qualify visual rendering. Its first read succeeded; the controlled
comparison separately exercised the new recovery branch.

The first cold Inspector connection still failed after four completed native
interruptions, before opening a tab or sending input. Cold startup, the revised
reader's first-focus behavior on a fresh boot, and native rendering remain
unresolved. No full external-site workflow was repeated.

The final database audit also failed tab preservation: one original tab was
absent despite being present in the immediate post-recovery listing. It was
already absent before normal candidate integration. The cause is unresolved;
immediate owned-page disappearance does not qualify sustained cleanup. The
isolated parent image is unchanged. The batch reached its 10-GiB memory limit
with 1,509 limit events and no OOM kill; this does not establish the cause of
the tab or rendering failures.

## First focus and settled cleanup (2026-11-09, trial 140)

The next boot's first focus still failed. A restored partial scan permitted
the second guarded query, which completed three scans but found no URL control.
Navigation stopped after one Command-L, before tap, text or Return. The native
capture also lacked an address bar. Cold Inspector startup had already failed
at its shared 120-second deadline; increasing a private attempt limit did not
resolve it.

Separate recovery verified the exact owned editing address and sent one close
key. Inspector reported disappearance before the database reflected removal.
Later read-only samples preserved every original tab through 114 seconds after
that close. No input was repeated. An immediate persisted-row assertion was
too strict; the original failed assertion remains in the evidence.

A distinct warm fixture control created in 18.77 seconds and navigated in
21.22 seconds. Both native captures, taken before Page.snapshot, visibly
rendered the fixture. Cleanup preserved all original tabs through 60 seconds
of observation. Final independent audits confirmed preservation, restored
settings and no surviving focus helper. These warm results do not qualify the
failed first focus or cold startup. No full external-site workflow was repeated.

## First-focus pass and native rendering failure (2026-11-09, trial 141)

The next guest reached foreground readiness in 237.14 seconds. Its original
test harness failed before opening a connection because it supplied a
360-second session timeout, above the supported 300-second maximum. The
corrected, separate case used 240-second transport and 300-second session
budgets. Its first actual handoff opened on attempt two; a private larger
attempt cap was not exercised. Extra idle time after the harness error prevents
attributing this result to a startup fix. Session creation took 117.50 seconds,
most of it after the successful handoff.

The unchanged public resolver passed the first native focus operation in one
guarded invocation. After an absent first responder and an unavailable scan,
a complete scan found no URL control, then the third permitted scan found one
unique control. Accepted native query time was 8.05 seconds within the existing
ten-second budget. The helper exited zero and restored both settings. Fresh
screen and process checks, one tap and a fresh exact editing-URL read preceded
typing. Native fixture captures immediately and 15 seconds later passed before
Page.snapshot. Original tabs were preserved through 60 seconds after cleanup.

That targeted gate permitted one full workflow milestone on the same warm
device, using an exact copy of the published SDK with its public four-attempt
maximum. Session creation took 16.12 seconds; the two external navigations took
21.63 and 41.26 seconds. The first page supplied validated product JSON, DOM,
HTML and a native product image. A later native capture showed its title and
price after an intervening Page.snapshot, so independent settled rendering is
not established by that later capture.

The second page passed URL, HTTP, original HTML and rendered HTML checks, but
both the guest and host screenshots had black page bodies. Page.snapshot showed
the product while subsequent visible-screen captures remained black. The
milestone failed before selector-action and timeout checks. A separate passive
observer sent no input or Page.snapshot and still found black pixels through
160 seconds after case failure. Separate recovery verified the exact owned
editing URL, sent its first close key and preserved all original tabs through
60 seconds of read-only observation. The original workflow remains failed.

A narrow fixture control on the same warm device then created in 14.96 seconds
and navigated in 22.59 seconds. Its initial and both post-navigation native
captures were also black before Page.snapshot, which rendered the fixture.
This failure therefore persisted beyond the external page. Its first close
removed the owned Inspector page, but the database changed between the two
post-close copies: one retained the owned row and the next omitted it. Both
preserved every original tab. The harness's identical-copy assertion failed;
that failure and both copies are retained, with subsequent observation sending
no additional close key.

All five later observations had matching database copies containing exactly the
original tabs, from 89 through 149 seconds after that close. A future harness
can classify an in-progress deletion separately from tab loss, while still
requiring every original in each valid copy and full removal at the final
deadline. This does not turn the original failed audit into a pass. The current
rendering failure and repeatable cold startup remain unresolved.

Final independent audits found restored settings, no focus helper and exactly
the original tabs. The guest stopped cleanly in 7.68 seconds with unchanged
parent images and cache. The 10-GiB container recorded 4,482 memory-limit events,
no OOM kill and no CPU throttling. Earlier native product pixels rendered after
limit events had already begun; the counts do not establish the rendering
failure's cause. No runtime change was adopted from this batch.

## Completed responder attribute failure (2026-11-09, trial 144)

A targeted session stopped before paste or Return when its first native
responder query returned the expected process and a frame, but label, value,
identifier and traits each returned `-25216` with null values. The helper
completed successfully and restored both switches. A later independent read
found the editing address field; the original navigation remains failed.

The reader now discards that exact failure and permits one fresh resolution
within its existing two-query limit. A warm-device diagnostic replaced those
four attribute replies after an actual successful guarded query. The second,
unmodified native query returned the complete editing field in 1.16 seconds.
Navigation sent one Command-L, pasted only after the fresh result, and reached
the expected fixture URL and condition in 23.86 seconds. Native and display
captures showed the fixture before any page snapshot; Safari toolbar artifacts
remained. Owned-tab cleanup completed, with original tab identities preserved
through 60 seconds of observation.

This validates the bounded response-recovery path with a deliberate host-side
fault and real native reads. It does not establish cold-start reliability or
repeatability of the original transient error. The helper binary, query alarms,
input sequencing and maximum number of queries are unchanged.

## Missing responder names (2026-11-09, trial 148)

A fresh session stopped before text input when label and identifier returned
`-25212` with null values, while value, traits and frame succeeded. The process
completed and restored both switches. Private phase diagnostics measured
10.92 seconds in guard setup, including 9.68 seconds enabling automation; the
first-responder query took 0.19 seconds. This sample did not reproduce the
earlier alarm and does not establish where that earlier invocation stalled.

A separate warm recovery returned complete attributes and safely closed the
owned tab. The reader now permits its existing second resolution for the exact
missing-name result described above. A controlled warm test replaced only those
two attributes after a successful native query. It discarded the entire reply,
obtained a fresh complete native field, and navigated with one Command-L.
Native screenshots showed the fixture immediately and 15 seconds later. One
close removed only the owned tab, with all original tabs preserved through
60 seconds and exactly eight observed cleanup key events.

This exercises the recovery branch with an explicit response fault and real
native queries. The original cold failure remains recorded. The two-query
limit, helper binary, alarms and input sequence are unchanged; cold qualification
of this recovery remains a separate check.

## Unsupported responder names (2026-11-09, trial 156)

A fresh session using an experimental batched-attribute helper returned a
first responder whose label and identifier both reported `-25205` with null
values. Value, traits and frame succeeded. The process exited normally and
restored both switches, but the reader rejected the partial field before text
input. This shows that batching alone does not resolve cold focus failures.

The reader now allows its remaining fresh resolution for this exact pattern,
with the same ownership, restoration, trace, value, traits and geometry checks
used for missing names. Different errors on the two names remain terminal.
The entire partial response is discarded; no shortcut, tap or text is replayed.

Forty-four targeted tests passed. In a controlled warm test, only the two name
attributes in the first successful native reply were replaced with the observed
failure; both replies were saved. The reader discarded the failed reply and
obtained a fresh complete native field before text input, with one Command-L.
Navigation took 25.42 seconds. Immediate and delayed native captures showed the
expected fixture body, and owned cleanup preserved the original tabs through
60 seconds. An independent audit after the original failure found restored
settings and no focus helpers.

This validates bounded response recovery with real native reads and an explicit
reply fault. It does not establish natural recovery or cold-start reliability.
The native batch helper remains experimental; the production native helper,
phase alarms, accepted query budget and two-query limit are unchanged. The full
product workflow was not run because the cold focus gate failed.
