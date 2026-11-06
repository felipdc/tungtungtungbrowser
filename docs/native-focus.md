# Native address-field readback

`NativeFocusReader` reads Safari's focused address field without
enumerating every visible accessibility element. It lets tab-reuse navigation
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

guest = Guest(cid=3, timeout=60)
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

Command-L can reveal Safari's URL field without putting it into editing mode.
When the reader identifies the URL field but its editing trait is absent, the
SDK can use that verified field's frame for one focus tap and then read it again.
Missing or unverified focus stops the operation. Subsequent editing and URL
readback checks use the first responder, independently of its screen position.
A failed focus query does not trigger another reader implementation or repeat
native input.
Missing focus, wrong ownership or a different URL prevents Return from being
sent. A navigation acknowledgement still requires a separate page condition.

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
