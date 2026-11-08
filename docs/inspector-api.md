# Experimental native Inspector API

`InspectorSession` adds rendered HTML, CSS/XPath lookup, focus and experimental clicks, condition waits and
request/response observation to the native `Browser` controller. It attaches to
one existing Safari tab or creates and owns a new bootstrap tab. This is an experimental API, not a complete Playwright
or WebDriver replacement. Automatic scrolling, frame-scoped locators, visibility
waits, request modification and reliable repeated navigation remain unfinished.

The controller uses the guest's Web Inspector service and WebKit DOM/Network
protocols. It does not use `Runtime.evaluate`, inject scripts, replace browser
properties, enable Remote Automation, or install a TLS interception certificate.
Inspection is itself a browser configuration change; these checks do not
establish that an inspected VM is indistinguishable from a physical device.

## Installation

Use a prepared guest with the project's native daemon and enable **Web Inspector**
in Safari's Advanced settings. The native bridge needs the platform and native
service entitlements supported by the prepared research guest. The installer
does not change Safari preferences or enable inspection automatically.

On the Linux host, install the optional protocol dependency. Git is needed for
the pinned source revision. The base input/screenshot SDK stays dependency-free.

```sh
python3 -m pip install -e '.[inspector]'
bash scripts/build_inspector_bridge.sh /absolute/external/inspector-build
python3 scripts/install_inspector_bridge.py /absolute/external/inspector-build \
  --cid 3 --output /absolute/external/inspector-deployment
```

The build requires `clang-19`, `ld64.lld-19`, and Linux `ldid`. Override them with
`TTT_INSPECTOR_CLANG`, `TTT_INSPECTOR_LINKER`, and `TTT_INSPECTOR_LDID` when needed.
It uses declarations rather than an Apple SDK. Keep the generated executable,
manifest and deployment evidence outside this repository. The installer checks
source and executable hashes, verifies upload readback, installs a uniquely named
executable and native service, and checks that its PID owns the bridge port. It
refuses to replace a running bridge. A later deployment can use the same build;
files retained by an earlier stopped deployment are preserved. Use a new output
directory for each deployment so its evidence remains separate.

Deployment is separate from ordinary VM startup. After a guest reboot, run the
installer again and verify its health result before creating an Inspector
session. Merely reloading a saved service plist has not been qualified: in one
native restart trial it reported a process but provided no listening port.
A fresh deployment of the unchanged bridge binary passed its identity and
health checks in that guest. The installer now avoids a second observed failure
where the guest refused to rename an upload over an earlier executable.

If a requested deployment fails, `deploy.json` also retains read-only service
status, launchd's detailed state and up to 16 KiB of its own log, when available.
A missing log is recorded as a diagnostic error and does not replace the original
installation failure. These reads require matching service label and executable
identity. The installer does not automatically restart a failed service.

The direct Linux VSOCK health probe accepts a connection-setup reset as a
rejected connection before sending any control request. Linux's
[virtio-vsock connecting-state handler](https://kernel.googlesource.com/pub/scm/linux/kernel/git/torvalds/linux/+/f91a3aa6bce480fe6e08df540129f4a923222419/net/vmw_vsock/virtio_transport_common.c)
reports a peer reset as `ECONNRESET`, including for a guest port without a
listener. This permits the initial deployment and read-only readiness polling
after its one launch request. A reset after connection, malformed health reply,
timeout or other connection error still fails; it cannot authorize replacement
or repeat the launch. The native listener must bind its port and return the
expected deployment identity before installation succeeds.

In a subsequent cold-guest trial, even a fresh deployment reported a running PID
but never opened the bridge port. A diagnostic build behaved the same way.
Launchd reported `state = xpcproxy`, `execs = 0` and `initialized = 0`, and neither
job created its log. Direct execution of the unchanged bridge binary worked;
after its clean stop, an explicit start of the original service became ready in
0.77 seconds. This isolates an observed service-start path failure but does not
establish its cause or qualify a cold-start fix. The successful warm start does
not erase the original deployment failures.

A later repeat again failed to start a fresh service before exec, and one direct
run followed by an explicit service start did not restore it. A separate native
process-spawn experiment ran the verified bridge successfully without keeping a
long-running daemon RPC open. The public installer now offers that native path
as an explicit experimental option:

```sh
python3 scripts/install_inspector_bridge.py /absolute/external/inspector-build \
  --cid 3 --launch-mode process --output /absolute/external/process-deployment
```

Process mode creates no launchd job. The verified executable starts one child
with its own process group, redirects its standard streams to a new private log
and closes unrelated inherited descriptors. The administrative call returns
after spawning; installation succeeds only when the bridge health response
matches both the child PID and guest boot UUID. An existing log is never replaced,
and an uncertain spawn is recorded without another launch or an automatic kill.
The fixed administrative expression invokes the native executable; it does not
evaluate JavaScript in Safari. Service mode remains the default. Neither option
is yet qualified as a repeatable cold-start solution.

The launch script has a 30-second execution limit. The installer allows 45
seconds for each daemon HTTP exchange so the daemon can stop and reap a timed-out
script and deliver its terminal error. Previously both limits were 30 seconds;
a native trial lost the reply at that boundary. This additional reply budget
does not extend the script limit or retry a launch. An HTTP timeout still leaves
the launch outcome uncertain and is retained in the deployment record.

Close Inspector sessions before stopping a bridge. Use its verified deployment
record and a new evidence file:

```sh
python3 scripts/stop_inspector_bridge.py \
  /absolute/external/process-deployment/deploy.json \
  --cid 3 --output /absolute/external/process-stop.json
```

The stop command checks the PID and boot UUID in both the health exchange and
the stop request itself. A replacement process is rejected, including one that
appears between those exchanges. A lost reply stays an uncertain outcome and is
not retried. `stop_acknowledged` means the matching bridge accepted the request;
it does not independently prove process exit. The command does not unload a
launchd job or remove retained deployment files.

The optional protocol dependency is pymobiledevice3 at commit
`776d601cec83b894c3ad0eeee8ab4db375a46e4a`. The small adapter bypasses its Lockdown
pairing/service-start path because the native bridge already owns an open
Inspector service. No upstream source is vendored. Its GPL license remains
applicable; see [third-party notices](../THIRD_PARTY_NOTICES.md).

## Attach to an existing tab

Run the controller on the VM host. Set `TTT_PAGE_URL` to the exact URL of an
already open Safari tab. If `url` is omitted, exactly one inspectable Safari tab
must exist. Another Inspector connection must not already own that tab.

Tab listings are requested after subscribing for updates. If no matching listing
arrives within two seconds, the SDK refreshes that read within the original
session timeout. `listing_refreshes` counts these extra read requests. Creation,
navigation and close inputs are not repeated. A disconnected Inspector stream
wakes pending listing reads immediately, including during cleanup; ordinary
session closure still allows the live listing stream to verify tab cleanup.

This addresses a native observation where a tab opened and fetched its document,
but the listing read consumed a 90-second deadline. A separate cleanup read then
identified the new tab. Cleanup subsequently failed at native focused-field
readback, so refreshing listings alone does not qualify that full workflow.
A later warm fixture session exercised one refresh while creating its owned tab
in 27.8 seconds and verified that tab closed in 11.4 seconds. Its document
response waiter still failed because the test excluded the fragment retained in
WebKit's reported URL. After correcting that matcher, a separate fixture and
external-page sequence completed in 65.7 seconds with verified cleanup. These
are retained individual outcomes, not a repeated-startup qualification.

Short-lived child frames can disappear before their native resume acknowledgement
arrives. The SDK skips a resume for an already destroyed non-page target and
tolerates the native missing-target reply only after it has observed that exact
target's destruction. `retired_target_resumes` counts those cases. They do not
invalidate the main page or its response waiters. A live target's error, a
different protocol error, or a transport timeout still fails the session; no
resume command is replayed.

This case was observed during a native page reload and reproduced in protocol
tests. A later native reload captured a successful application JSON response and
kept the session alive through twelve retired auxiliary targets. Nine returned
the specific missing-target error; three disappeared before resume was sent.
That qualifies this case in one reload, without establishing sustained session
reliability.

```python
import asyncio
import os

from tungtungtungbrowser import Browser, InspectorSession


async def main():
    browser = Browser()
    async with InspectorSession(browser=browser, url=os.environ['TTT_PAGE_URL']) as page:
        # Register before the action, so an immediate native response is retained.
        async with page.expect_response(os.environ['TTT_PAGE_URL'], timeout=60) as response:
            await page.reload()

        await page.wait_for_selector('body', timeout=60)
        rendered_html = await page.content()
        original_html = await response.value.text(timeout=60)
        print({'rendered_characters': len(rendered_html),
               'original_characters': len(original_html),
               'status': response.value.status})
        # Captures stay in the caller's private workspace.
        await page.screenshot(os.environ['TTT_SCREENSHOT_PATH'])


asyncio.run(main())
```

The session owns inspection, pending commands, response metadata and subscriptions.
It **does not own the existing tab** and leaves that tab and its site data intact
on close. Closing detaches inspection, releases the native socket and cancels
pending waits. Cleanup problems are retained in `page.cleanup_errors` and
`page.transport.failure`; inspect those when assessing a workflow's success.

## Create and close an owned tab

The experimental `new_tab=True` option requests a blank Safari tab with native
Command-T, verifies a new `about:blank` tab ID and preservation of the existing
tabs, then attaches inspection before the first navigation. Supply the native
`browser` configured with address-field readback as above. Safari must already
be warmed and verified in the foreground. This mode does not need a bootstrap
server. Local protocol tests cover the ownership and cleanup behavior, but the
native end-to-end workflow is not qualified.

Configure the reader after installing the [native focus helper](native-focus.md):

```python
from tungtungtungbrowser import Browser, NativeFocusReader
from tungtungtungbrowser.guest import Guest

guest = Guest(cid=3, timeout=60)
browser = Browser(guest=guest, address_field_reader=NativeFocusReader.from_deployment(
    guest, os.environ['TTT_FOCUS_DEPLOYMENT']
))  # TTT_FOCUS_DEPLOYMENT is the external install.json path.
```

```python
async with InspectorSession(browser=browser, new_tab=True, timeout=90) as page:
    async with page.expect_response(os.environ['TTT_DESTINATION_URL'], timeout=90) as response:
        await page.goto(os.environ['TTT_DESTINATION_URL'])
    await page.wait_for_selector('body', timeout=90)
    rendered_html = await page.content()
```

An existing inspectable blank tab blocks creation before input: two blank tabs
cannot be distinguished by their native editing addresses. Multiple newly
appearing tabs or a missing input acknowledgement also prevent ownership from
being claimed. The SDK sends Command-T once and reports an uncertain result;
it does not retry the key or close an unowned tab. For a uniquely owned blank tab,
the expected native editing address is exactly empty or `about:blank`.
Navigation destinations still must be HTTP(S) URLs.

For existing-tab identity, Safari's native address field can display Unicode
characters while Inspector reports their percent-encoded UTF-8 bytes. The SDK
matches those forms in the path, query and fragment, and treats matching forms
in multiple tabs as ambiguous. It preserves the authority, URL delimiters,
query values and fragment: an encoded slash is not treated as a path separator.
Newly pasted navigation text still requires exact readback before Return.

A native workflow reproduced this mismatch after navigation to a Unicode path:
Inspector percent-encoded the accented character and the native editing field
displayed it literally. Cleanup correctly withheld its close input under the
previous exact comparison. With the UTF-8 comparison, recovery of that same
owned tab closed it in 28.2 seconds and verified preservation of the original
tab. A subsequent owned workflow also verified cleanup, in 13.2 seconds.
These observations validate the address comparison; that run still had an
earlier session-attachment timeout and later black page content in both native
and host screenshots, so complete visual and session reliability remains open.

In the first native trial, the initial inspection connection failed before
input. A later attempt created a distinct blank tab and preserved the previous
tab, but inspection initialization exceeded its deadline. Cleanup then failed
on incomplete native address-field attributes. A subsequent read identified the
address field and returned localized placeholder text as its value. That text
is deliberately not treated as a verified empty address. No destination URL was
submitted and tab closure was not established. This option remains experimental
while native empty-field identification and initialization are being qualified.
A later recovery used retained ownership evidence and the exact native
placeholder reviewed in that guest to close the tab and verify preservation of
the original tab. That private recovery does not qualify general SDK cleanup
from an empty field.

The experimental `new_tab_url` option opens a new native Safari tab and owns its
cleanup. Safari must already be warmed and verified in the foreground. Supply a
static HTTP(S) bootstrap page you control, reachable by the guest, with no URL
fragment or redirect. The [portable fixture](api-fixture.md) can serve this page.
The SDK appends a unique fragment, verifies that a new tab ID appeared while the
existing tabs remained, and attaches native inspection. Bootstrap requests occur
before attachment; register expectations before the subsequent `goto` to capture
the actual destination's traffic.

```python
async with InspectorSession(
    browser=browser,
    new_tab_url=os.environ['TTT_BOOTSTRAP_URL'],
    timeout=90,
) as page:
    async with page.expect_response(os.environ['TTT_DESTINATION_URL'], timeout=90) as response:
        await page.goto(os.environ['TTT_DESTINATION_URL'])
    await page.wait_for_selector('body', timeout=90)
    rendered_html = await page.content()
    original_html = await response.value.text(timeout=90)
    await page.screenshot(os.environ['TTT_SCREENSHOT_PATH'])

assert page.tab_cleanup['status'] in {'closed', 'already_absent'}
assert not page.cleanup_errors
```

Navigation checks a fresh tab listing and the inspected document URL, then the
native editing address field before replacing its text. Duplicate URLs, a changed
Safari PID, a different active tab or an unverified address field cause failure.
Safari can omit the HTTP(S) scheme or a bare root URL's trailing slash from its
editing field. Only those omissions are accepted: the entire host, port, path,
query and fragment must otherwise match exactly. A slash is never removed from
a non-root path or a URL with a query or fragment. Tabs that differ only by scheme
or a root slash are ambiguous and block native actions.
The SDK does not infer identity from a title or shortened host/path. After paste,
the new URL still requires exact readback, including its scheme, before Return.
Keep one controller per guest and avoid manual tab switches during an operation.

On exit, inspection release is requested first. A later listing may still name
the session's own Inspector connection; another client's connection blocks
cleanup. The SDK checks the owned tab's unique URL
and the native active address field, sends Command-W once, and waits for that tab
ID to disappear while preserving the other listed tabs. `tab_cleanup` records the
outcome. Failed or uncertain cleanup raises `InspectorError` after releasing the
connection; if the body already raised, that original exception is preserved with
a cleanup note. Concurrent close calls share one cleanup task. Cancellation joins
an already dispatched operation; it does not replay input. An uncertain creation
without proven tab ownership is reported as `unknown` and no tab is closed.

This owns one tab and its inspection resources, not a separate browser profile.
Cookies and other site data are shared with the running Safari instance. These
lifecycle contracts have local protocol tests; native qualification is ongoing.
Navigation uses the `Browser` foreground/PID check after unlock and does not make
a second, earlier query for the same state. Native readiness and address-field
readback remain required before any URL is typed.

## Available operations

| Operation | Behavior and limits |
| --- | --- |
| `content()` | Native serialization of the current document; raises if the document changes during the read. It is not the original response HTML. |
| `query_selector(selector, strategy='css')` | First native DOM node ID or `None`. `strategy='xpath'` wraps the expression to avoid the native search protocol's additional CSS/text matches and releases the temporary search. Node IDs can change on another document read, even without navigation; do not retain them for later actions. |
| `focus(selector, strategy='css')` | Resolves exactly one node, rejects absent/disabled/hidden/ignored targets, calls native `DOM.focus` once and verifies focused state. XPath is also supported. This may scroll and dispatch ordinary focus events; it does not establish native keyboard ownership or clickability. |
| `click(selector, strategy='css', timeout=30)` | Experimental visible-target native touch with strict CSS/XPath resolution, temporary Inspector highlight geometry and native element selection. Requires the `pointer` extra and calibrated native touch helper. No automatic scrolling or input retry; see [qualification and limits](selector-clicks.md). |
| `wait_for_selector(selector, strategy='css', state='attached', timeout=30)` | Polls a fresh document for attached/detached state, including a process replacement. Visibility is not inferred. |
| `wait_for_url(pattern, timeout=30)` | Waits for a committed document URL matching a case-sensitive glob. Redirect expectations remain explicit. |
| `wait_for_timeout(milliseconds)` | Cancellable delay, from 0 to 300,000 milliseconds. Other timeouts use seconds. |
| `expect_request(predicate, timeout=30)` | Async context manager registering a request waiter before an action. Accepts a URL glob or synchronous predicate over a `NetworkRequest`. |
| `expect_response(predicate, timeout=30)` | Equivalent response waiter; `waiter.value` is available after the context exits. |
| `response.body()`, `.text()`, `.json()` | Wait for `loadingFinished`, then retrieve the native body. Failure, eviction, redirect replacement and destroyed page processes are errors. Default body limit is 16 MiB. Text defaults to UTF-8; pass `encoding` explicitly for other encodings. |
| `reload()` | One native WebKit reload request. Completion does not mean the page is ready. |
| `goto(url)` | Verifies that the inspected tab has a unique URL and that the native active editing field contains that exact URL, then uses the supplied `Browser`'s address-bar path. Requires an address-field reader such as [NativeFocusReader](native-focus.md); no flat UI-tree fallback is used. Native input is not retried. Follow it with a URL/selector condition. Initial focus and foreground verification can still fail. |
| `screenshot(path=None)` | Uses the supplied `Browser`'s native or configured QMP capture. Review the image separately from DOM/network success. |

DOM serialization and selector operations serialize their document reads within
one session: native `DOM.getDocument` can invalidate earlier node IDs even when
the document has not navigated. Actions resolve a fresh node and check the target
and document generation before and after use. This does not prevent the page
itself from navigating or mutating. Focus is not retried after a lost reply or a
failed post-action check. XPath syntax validation and explicit frame scope remain
unfinished; an invalid XPath can currently look like an empty search result.

For application JSON, register a suitably narrow predicate, trigger the action,
then call `await waiter.value.json()`. Observation does not pause or modify XHR
or fetch requests. Network interception/fulfillment remains on the roadmap.
The [portable API fixture](api-fixture.md) provides a local page with distinct
original/rendered HTML, delayed DOM conditions, XHR JSON and input event records.
The original document body currently becomes available after the response
finishes; streaming HTML before DOM parsing completes is not implemented.

The default session retains at most 512 response records and 4 MiB of response
metadata. Both limits are configurable. `evicted_requests` exposes eviction.
Bodies are retrieved on demand and are not cached indefinitely by the session.
The diagnostic event ring holds only method names, target identifiers and local
timestamps, with a `dropped_events` counter. It does not log URLs, headers or
bodies. Application code must keep captured payloads and credentials private.
Each native plist frame is limited to 32 MiB.

When cross-site navigation creates a new WebKit page process, the session uses
`Target.setPauseOnStart`, enables native Network/Page observation on the new
target, then resumes it. It does not enable a JavaScript debugger. This brief
native hold affects timing and is part of the measured navigation cost. Worker
and frame targets never silently replace the main page. A command interrupted by
process destruction fails; only read-only condition waiters poll a new document.

Native `Browser` operations run in a worker thread and are serialized. Cancellation
waits for an already dispatched native exchange before releasing the session;
it cannot recall input already sent. Use the `Guest` timeout to bound those
underlying exchanges. A timeout never silently replays navigation or input.

## Transport and native observations

Bridge protocol 2 listens on guest VSOCK port 1343 and accepts only host CID 2.
`health` and `stop <pid> <boot_uuid>` are single-line administrative commands when
no session owns the bridge. The legacy unguarded `stop` remains available for
protocol compatibility; the stop script always uses the identity guard.
`open` returns one JSON identity record, then the same connection
carries native length-prefixed plist messages. EOF releases both the guest TCP
service socket and XPC handoff. There is no network listener exposed outside the
guest and no separate WebSocket stream. An external tunnel can be supplied as
an async `InspectorTransport(bridge_socket=...)` factory returning a connected
socket; the repository contains no provider-specific tunnel implementation.

The bridge queues its native loopback client before handing the listening socket
to Web Inspector. Previously it waited for the XPC acknowledgement before
connecting. Native logs showed the service exiting after its two-second
no-client window during that handoff. Queuing the client first addresses that
ordering problem without sending a page command. Failed handoffs record a fixed
XPC error category or acknowledgement timeout in the bridge log and response;
the bridge does not replay the handoff or change inspection preferences.

The queued-client build subsequently opened multiple native sessions on the
same guest boot, but a later handoff still failed with `connection_interrupted`.
Keep that later failure alongside the successful connections: this change does
not yet qualify repeatable cold or warm Inspector availability.

The bridge also constructs and queues its complete XPC check-in message before
activating the connection. This removes message construction and descriptor
duplication from the interval after activation can start the native service.
It uses `xpc_connection_activate`; the native ACK is still required. Apple
documents that an inactive connection does not
send or receive messages until activation, and that enqueueing a message alone
does not establish delivery. See [connection activation](https://developer.apple.com/documentation/xpc/xpc_connection_activate(_:))
and [message delivery](https://developer.apple.com/documentation/xpc/xpc_connection_send_message(_:_:)).
The first native trial of this ordering still failed before client check-in,
while the service was initializing application metadata. It does not extend or
disable the service's deadline, and is not sufficient to qualify reliability.

`InspectorTransport` allows at most three connection attempts, separated by
250 ms. During bridge handoff it retries only when the bridge explicitly reports
`open_failed` with `handoff_error: connection_interrupted`. That response follows
closure of the native stream and cancellation of its XPC connection, before any
Inspector plist frame or browser action is relayed. Each subsequent reply must
match the first bridge PID and guest boot UUID. Handoff timeouts, EOF, malformed
replies, identity changes, other errors and older bridges without an error
category fail immediately. Each handoff sequence retains its single transport
`timeout`.

Session initialization also handles one narrowly identified disconnect: after
registering its Inspector identifier, the native stream can end before returning
its first plist reply. The session closes that stream before opening another,
using the same bridge identity and the remaining connection-attempt budget.
Only identifier registration has been sent at this point. The original
`InspectorSession.timeout` bounds the whole initialization, including replacement
streams; it is never restarted. Recovery requires the protocol library's explicit
incomplete-read EOF cause; other wrapped I/O errors remain terminal. A send
failure, first-reply timeout, malformed reply, failure while handling that reply,
or later disconnect remains terminal.
Tab creation, navigation and page commands are never replayed by this recovery.

Use `InspectorTransport(max_open_attempts=1)` to disable this setup recovery.
`transport.handoff_attempts` retains each bridge attempt's status, error category
and duration, including failed attempts when a later connection succeeds. An
opened stream that ends before its initial plist reply also records
`initial_reply_status: "eof"`. Both recovery boundaries have protocol-fixture
coverage. The following native samples exercised the earlier interrupted-handoff
recovery. In one guest boot, three
separate existing-page sessions completed in sequence: attachment took 23.82,
6.25 and 3.41 seconds, with rendered HTML reads of 1.51, 1.26 and 1.32 seconds.
The first session retained two explicit interrupted-handoff rejections before
its third attempt succeeded; the next two opened on their first attempt.
These samples establish recovery in that run, not dependable service startup
or full browser-workflow acceptance. The earlier ordering-only attempt failed
on the same boot. No browser input or reload was sent by these read-only trials.

When WebKit reports a newly paused target, the SDK now waits for the native
`Target.resume` acknowledgement. A rejection or missing reply fails the session
without replaying the command. Page readiness is not signalled before that
acknowledgement; paused frame and worker targets are resumed without adopting
them as the inspected page. This makes resume failures observable rather than
discarding them and later reporting only a page-condition timeout.

On iOS 26.6.2, the native bridge build, verified installation, handshake, DOM
reads, CSS/XPath queries, reload, response-body retrieval and repeated attach/
detach have been exercised. A warm small-page sample took 0.34 seconds to read
HTML and 0.39/0.68 seconds for CSS/XPath waits. Those early numbers included the
initial WebSocket forwarding prototype. That prototype stalled on an 803,769-byte
native response after roughly a 512 KiB receive window. The direct stream then
read a 694,419-character external document in 1.31 seconds.

A subsequent larger-page run using the direct stream captured application JSON,
read a 694,661-character DOM and captured the rendered page. Its next navigation
failed with a native accessibility query error, so **the complete cross-page
workflow is still unqualified**. The previous transport failures and later
navigation failure remain separate observations. These are samples, not latency
guarantees or evidence of physical-device equivalence.

The final sample from that longer API investigation reported approximately
6.49 GiB current and 6.50 GiB peak container memory, 7,591 `memory.events:max`
events, and zero `oom`/`oom_kill` events. These are cumulative values for the
boot, not an isolated per-operation measurement. They establish memory-limit
pressure during this run, but do not establish that it caused the accessibility
failure. No smaller memory profile is promoted from these API results. The guest
later stopped cleanly in 6.38 seconds; the parent disks and cache were unchanged.

The offline tests cover immediate replies, a stream larger than the previous
stall window, process changes, response-body completion, redirect invalidation,
retention limits, cancellation, timeouts and socket release. Native fixture
coverage for streaming, cache/service workers, selector clicks and extended
sessions remains required before promoting the API.

### Owned-tab trial and warm rendering failure

Creation checks both that the bootstrap URL belongs to a new tab ID and that
earlier tabs remain listed. A later native trial opened the requested fixture
with a new ID while the previously listed blank tab disappeared. Creation still
fails in that case. The SDK now retains the proven new tab's ownership so error
cleanup can close it, and records the before, observed, matching and missing tab
IDs in `tab_cleanup`. It does not claim a reused existing ID or restore a missing
prior tab. Offline tests cover the lost-open-reply variant as well.

Native server-error pages expose a separate unresolved cleanup case: the tab
listing can become `data:text/html,` while Safari's address field retains the
failed HTTP URL. Automatic cleanup currently refuses that non-HTTP listing.
It does not guess the active tab from an earlier URL. The original failure and
retained ownership need to remain visible to the caller.

A later native trial created and rendered an owned fixture tab, but its first
navigation failed while reading native focus. Cleanup also exposed two SDK
issues: a listing could still name the session's own Inspector connection after
detach, and Safari could omit `http://` from an editing address. A subsequent
root-URL check also showed an omitted trailing slash. The controller now handles
those specific address forms while rejecting ambiguous sibling tabs, a different
scheme explicitly present in the field, shortened paths and changed queries.
Native recovery verified that only the owned tab disappeared.

After these fixes, a separate warm run used the existing public focus helper with
`Browser(input_settle=4)` and completed creation, two navigation acknowledgements,
committed-URL and CSS conditions, document-response and rendered-HTML reads, a
bounded delay, and automatic owned-tab cleanup. The original tab remained.

| Operation | Local fixture | External document |
| --- | ---: | ---: |
| Native navigation acknowledgement | 25.17 s | 28.00 s |
| Subsequent committed-URL wait | 2.83 s | 6.71 s |
| Subsequent CSS condition | 0.35 s | 0.36 s |
| Rendered HTML read | 0.34 s | 0.38 s |

Creation/attachment took 13.76 seconds and owned-tab cleanup 15.39 seconds.
**Both post-navigation screenshots had black content areas**, despite successful
DOM conditions. This is a failed visual-readiness outcome, not a complete browser
workflow pass. Screenshot file creation and DOM success cannot establish a usable
rendered page. Selector actions and application-JSON capture were not part of this
run. The 4-second settling delay remains an explicit trial setting; defaults have
not changed, and these timings are samples rather than performance guarantees.

An experimental focus reader running as the mobile user also completed some
reads, but a fresh navigation query failed with AX error `-25216`; it has not been
adopted. The original cold sequence timed out during Safari launch at 300.27
seconds. Later warm results do not turn that startup into a success. Background
archive work overlapped the cold sequence, so it is not a controlled startup
performance comparison.

The final container sample was approximately 6.49 GiB current and 6.50 GiB peak,
with 2,948 memory-limit events and no OOM or OOM-kill events. These cumulative
values show memory pressure, but do not identify the cause of the black captures
or establish a resource reduction. The original retained tab rendered in the
final host capture. Shutdown completed in 16.49 seconds without forced
termination; parent overlays, parent cache and six original baseline files were
unchanged. Neither the experimental helper nor this disposable guest state was
promoted.


### Native DOM focus and concurrent reads

A later iOS 26.6.2 fixture run reproduced two selector problems. Native
`DOM.performSearch` returned seven matches for bare `button` because it also
searched CSS/text; document-relative XPath `button` should match no child of the
document node. A parenthesized XPath with a fresh always-true opaque predicate
returned no result for that expression and one result for the intended absolute
button expression. The wrapper preserves native XPath evaluation without page
script injection. The native search implementation is documented in
[WebKit's InspectorNodeFinder](https://github.com/WebKit/WebKit/blob/main/Source/WebCore/inspector/InspectorNodeFinder.cpp).

A second problem occurred when another `getDocument` invalidated a previously
resolved node before `DOM.focus`. The SDK now holds a document lock from lookup
through use. Three concurrent batches of HTML, CSS, XPath and URL reads passed
in 1.53, 1.42 and 1.41 seconds. Public CSS focus took 0.81 seconds and XPath focus
1.12 seconds, with native focused-state readback. Hidden, disabled and ambiguous
controls were rejected. These are small-fixture samples, not latency guarantees.
Creation took 19.24 seconds and owned-tab cleanup 10.40 seconds; the original tab
was preserved. Guest and host captures both showed the rendered fixture after
focus. This does not resolve the earlier post-navigation rendering failures.

An experimental mobile-user native probe could map DOM focus to a visible web
accessibility element and its screen point. However, the native accessibility
hit test also accepted a deliberately covered fixture button. It therefore does
not establish pointer actionability, and no selector tap was sent from this
experiment. The probe is not part of the public SDK. A later
[native element-selection path](selector-clicks.md) now supports experimental
visible-target CSS/XPath clicks. Broad action qualification,
background-tab identity checks and coverage/occlusion checks remain open.


### Paired captures after warm navigation

A subsequent owned-session trial reached a second local fixture page and then
an external document. Committed-URL conditions, body lookup, original document
responses and rendered HTML reads succeeded. Creation took 13.95 seconds and
verified owned-tab cleanup 10.14 seconds, preserving the original tab.

| Operation | Second local fixture | External document |
| --- | ---: | ---: |
| Native navigation acknowledgement | 19.45 s | 18.53 s |
| Subsequent committed-URL wait | 1.27 s | 0.63 s |
| Subsequent body selector | 0.32 s | 0.31 s |
| Rendered HTML read | 0.35 s | 4.94 s |

Sequential guest JPEG and host display PNG captures both showed the local
fixture correctly. Both showed a black content area on the external document,
while Safari chrome remained visible. The failure is therefore not specific to
the guest capture path in this sample; its rendering cause is still unknown.
The two capture paths were sampled sequentially, not simultaneously. No visual
success or full cross-page acceptance is claimed. This trial used the
experimental mobile-user address-field reader and a 4-second input settling
delay, so it does not qualify new public defaults or isolate the reader's effect.

Two earlier private recorder errors were retained separately: an invalid network
predicate and a malformed native key request. One failed cleanup rejected a
URL mismatch while navigation was committing; a later ownership-checked recovery
closed that tab. The corrected run above used a fresh owned tab and completed
without cleanup errors.


The same boot reached foreground Safari in 255.04 seconds, within the approximate
300-second budget, but its initial capture had blank content. This qualifies
neither cold page readiness nor repeatable Inspector installation. The final
container sample was 6.36 GiB current and 6.50 GiB peak, with 1,801 memory-limit
events and no OOM or OOM-kill events. These are cumulative run values, not an
isolated operation measurement or evidence of a lower-resource configuration.
The clock recorder retained a health-request timeout and later acknowledged time
offers; its cause has not been isolated. Final accessibility settings were both
off. The original tab rendered in the final host capture, and shutdown completed
in 7.68 seconds without forced termination. Parent disks, parent cache and six
original baseline files were unchanged; no experimental guest state was promoted.

## Latest warm-control observations

A fresh disposable guest reached verified foreground Safari in 271.13 seconds.
The native inspection handoff still failed intermittently. Correlated service
logs showed shutdown after a two-second no-client window despite the bridge's
queued native connection; foreground readiness alone does not qualify control
readiness.

Once warmed, inspection of a restored existing external page produced these
samples. Both the native JPEG and host display PNG showed rendered content.

| Operation | Duration |
| --- | ---: |
| Attach native inspection | 2.54 s |
| Body selector condition | 0.36 s |
| Read rendered HTML | 1.46 s |
| XPath body lookup | 0.73 s |

This was an existing-page read, not the full create-to-second-page workflow.
An API-triggered reload subsequently exposed the retired-frame resume race
noted above. Follow-up native sessions failed at handoff, before exercising its
fix. No complete application-JSON-to-second-page acceptance is established.

The final runtime cgroup reached its 6.50-GiB limit, recorded 2,592 memory-limit
events and no OOM or OOM-kill events. These cumulative values do not establish
resource savings. Both accessibility switches were independently verified off
at the end, the owned test tab had been verified closed, and guest shutdown
completed in 7.78 seconds with exit zero and no forced termination. Parent disks,
parent cache and six original baseline files remained unchanged.

A subsequent existing-page reload captured an HTTP 200 application JSON response
and verified the expected item identifiers. Both native and host captures showed
the product image, title and price without an authentication wall. A solid media
overlay without its expected glyph remains a visual fidelity question. The
retired-target handler tolerated 12 auxiliary-target retirements, including nine
missing-target replies, while keeping the page session alive. This provides live
coverage beyond the protocol fixtures. The subsequent external navigation stopped
at a foreground-query transport timeout before keyboard input; that run does not
establish cross-page acceptance. A later read-only foreground query succeeded in
8.14 seconds. At the timeout, the cgroup had no memory-limit or OOM events.

### Capture timing and remaining workflow failures

A subsequent disposable guest reached foreground Safari in 278.32 seconds.
Its first owned session took 118.98 seconds to initialize, then missed a
30-second body-condition deadline. The fixture rendered in a later capture.
Cleanup's native focus helper terminated at its alarm; a separate recovery
verified the owned tab's address, closed it and preserved the original tab.
Another session lost its initial native Inspector connection before any browser
action. These remain failures even though later sessions worked.

Two later sessions captured HTTP 200 application JSON with the expected item
identifiers and read the rendered product document. Paired device captures
showed its image, title and price. In one session, the initial captures still
showed a skeleton; captures following an additional five-second wait showed
the content. Neither session completed the full cross-page workflow. One
private recorder incorrectly rejected a cached HTTP 304 response, despite
available rendered HTML; its device captures were blank. The next session's
native focus query returned no first responder, so navigation stopped before
destination text was submitted. Both owned tabs were subsequently verified
closed. A DOM condition alone does not establish that a capture is current.

A separate rendering diagnostic started directly on an external document,
followed its link using a native CSS click, then navigated to a local fixture.
CSS/XPath clicks, observed fixture JSON, a one-second absent-selector timeout,
a subsequent successful read and owned-tab cleanup completed. Creation took
38.74 seconds and cleanup 14.65 seconds; the entire diagnostic, including extra
captures, took 256.86 seconds. The original tab was preserved. This was a
smaller workflow, not a successful product-to-second-page run.

That diagnostic retained paired native JPEG and host PNG images before and
after focus and clicks, followed each pair with a five-second passive wait,
then requested a native WebKit
[`Page.snapshotRect`](https://github.com/WebKit/WebKit/blob/main/Source/JavaScriptCore/inspector/protocol/Page.json)
PNG and another device pair. All five WebKit snapshots returned page content.
The first device pair after focus retained the previous scroll position; the
passive pair showed the focused link before the WebKit snapshot was requested.
The first fixture JPEG also lagged the document text visible in the following
host PNG. Later pairs showed the expected content. The persistent black-page
failure was not reproduced in this smaller sequence, so neither the wait nor
the native page snapshot is a qualified fix. Captures were sequential, and no
page scripts or renderer-setting changes were used.

The run retained the experimental 2-GiB, two-CPU guest and 6.5-GiB container
limit. Its final cgroup peak was 6.5 GiB, with 17,758 memory-limit events and no
OOM kills. These cumulative counters do not establish the cause of earlier
failures. Both accessibility switches were off at final audit, no focus helpers
remained, and native shutdown completed in 16.69 seconds without forced
termination. Parent disks and renderer cache were unchanged.

### Larger resource profile trial

A disposable 3-GiB, four-CPU guest with a 10-GiB container limit reached
foreground Safari in 175.39 seconds; that capture still showed a white startup
view, not a rendered page. Its first bridge process logged that it
was listening, but the health connection timed out; a later check found the
process absent and the port refusing connections. The exit cause was not
established. A separate deployment succeeded. Session initialization then
missed its 180-second page-target readiness deadline, and cleanup's focus helper
terminated at its alarm. A separate cleanup connection also timed out before
any browser input. With a 60-second transport deadline, another connection
verified the same owned tab and closed it in 14.69 seconds, preserving the
original tab. These later results do not erase the initial failures.

A warm attempt used that 60-second transport deadline and a five-second native
input-settle interval. Session creation took 20.51 seconds and native product
navigation 25.55 seconds. The observed HTTP 200 JSON carried an application
error instead of the required product data. Native JPEG and host PNG captures
both showed black page content, although the rendered DOM contained the product
title. The cause of the application error was not established. The workflow
stopped before its second-site navigation; owned-tab cleanup completed in 21.63
seconds. The initial-EOF recovery branch was not encountered in these native
samples and retains protocol-fixture coverage only.

The final cgroup peak reached 10 GiB, with 146 memory-limit events and no OOM
kills. Initialization and warm-workflow failures preceded those limit events. Both
accessibility switches were independently verified off, no focus helpers
remained, and shutdown completed cleanly in 8.08 seconds. Parent disks and cache
were unchanged. The larger resource profile and longer waits did not qualify
the full workflow; multiple configuration changes also prevent attributing the
observed differences to one change.

### Cross-page sequence and remaining capture failures

A subsequent disposable 3-GiB, two-CPU guest with the same 10-GiB container
limit reached foreground Safari in 243.78 seconds. Its first session initialized
in 66.08 seconds, but the native focus query exceeded the administrative
script's 30-second deadline before destination text was pasted. Cleanup closed
the owned tab and preserved the original tab. Private phase logging in a later
helper build recorded seven successful focus queries and guard restorations;
it did not reproduce or explain the earlier timeout.

That instrumented attempt captured valid product JSON, rendered HTML and product
images, then navigated to a second external page and followed its link with a
native CSS click. The subsequent fixture click reached the intended text and
produced trusted pointer/touch down and up events, but selected a word instead
of firing a click. No fixture JSON request followed. The response deadline
expired, the original failure was retained, and the owned tab was closed.

A fresh attempt with the original helper completed the entire method sequence
in 315.30 seconds: session creation, product navigation and JSON, HTML, product
and second-page captures, external link click, fixture CSS/XPath clicks and JSON,
a 1.00-second absent-selector timeout, a successful subsequent read and verified
cleanup. It used no diagnostic WebKit snapshots. Product and second-page images
showed their expected content, but fixture device captures remained black before
and after the successful actions, including after five-second passive waits.
This establishes one complete method sequence, not reliable visual acceptance.

A separate fixture diagnostic measured native event-timestamp gaps of
55.42–179.21 ms for four contacts requesting 50 ms. Both real clicks succeeded,
and the page's trusted event timestamps agreed with their measured contact
durations. Those successful samples do not establish the cause of the earlier
word selection. With the black page reproduced, native `Page.snapshotRect`
returned the rendered fixture and its updated click state. Both device JPEG
and host PNG captures stayed black afterward. A page snapshot can therefore
differ from device composition in this observed failure; it did not repair it.

The final cgroup peak was 10 GiB, with 1,983 memory-limit events and no OOM
kills or CPU quota throttling. The first focus timeout preceded the limit
events; these counters do not establish the later failures' cause. All four
owned test tabs were verified closed, both accessibility switches were off,
and no focus helpers remained. Shutdown completed cleanly in 7.98 seconds;
parent disks and cache were unchanged. The resource profile remains experimental.
