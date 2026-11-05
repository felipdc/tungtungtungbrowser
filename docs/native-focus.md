# Native address-field readback

The optional `NativeFocusReader` reads Safari's focused address field without
enumerating every visible accessibility element. It lets tab-reuse navigation
verify the typed URL even when the software keyboard changes the field's frame.
It sends no touch or key events and evaluates no JavaScript in Safari.

The current implementation uses the guest's native AXRuntime first-responder
attribute, `3002`, verified against `AXElement.firstResponder` on iOS 26.6.2.
The native query returns the element's process ID, identifier, value, traits,
label and frame. The reader rejects missing attributes, a different process,
query errors and invalid geometry. This is an experimental interface tied to
the tested native runtime, not a general compatibility guarantee for iOS.
The native `kAXErrorNoValue` result with no element is treated as absent focus;
it is distinct from a failed query.

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
The focus helper itself has no resident process or listening port. A retained
deployment can be checked again after a guest reboot.

## Navigation behavior and limits

Command-L can reveal Safari's URL field without putting it into editing mode.
When the first-responder query completes without a focused URL field, the
initial lookup still uses the existing native accessibility tree to locate the
address field and make one verified focus attempt. Subsequent editing and URL
readback checks use the first responder, independently of its screen position.
A failed focus query does not trigger a fallback query or repeat native input.
Missing focus, wrong ownership or a different URL prevents Return from being
sent. A navigation acknowledgement still requires a separate page condition.

Like the existing native accessibility queries, this helper may temporarily
enable the system accessibility and automation switches. It records their
original values, restores only switches it enabled, and reports both states.
The Python reader requires matching before/after states. A terminated helper
is an error and cannot prove restoration; no automatic input retry follows.
The native messaging timeout and process alarm do not guarantee a precise
host wall-clock duration under emulation.

The query is read-only with respect to page content. It is not an implementation
of selector clicks, scrolling, visibility checks or arbitrary DOM geometry.
The initial tree lookup and native foreground verification remain possible
failure points. This reduces reliance on whole-tree queries; it does not
establish repeated cold-boot or external-site acceptance.

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
