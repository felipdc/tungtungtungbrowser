# Experimental native Inspector API

`InspectorSession` adds rendered HTML, CSS/XPath lookup, condition waits and
request/response observation to the native `Browser` controller. It attaches to
one existing Safari tab or creates and owns a new bootstrap tab. This is an experimental API, not a complete Playwright
or WebDriver replacement. Selector clicks, frame-scoped locators, visibility
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

In a subsequent cold-guest trial, even a fresh deployment reported a running PID
but never opened the bridge port. A diagnostic build behaved the same way.
Launchd reported `state = xpcproxy`, `execs = 0` and `initialized = 0`, and neither
job created its log. Direct execution of the unchanged bridge binary worked;
after its clean stop, an explicit start of the original service became ready in
0.77 seconds. This isolates an observed service-start path failure but does not
establish its cause or qualify a cold-start fix. The successful warm start does
not erase the original deployment failures.

The optional protocol dependency is pymobiledevice3 at commit
`776d601cec83b894c3ad0eeee8ab4db375a46e4a`. The small adapter bypasses its Lockdown
pairing/service-start path because the native bridge already owns an open
Inspector service. No upstream source is vendored. Its GPL license remains
applicable; see [third-party notices](../THIRD_PARTY_NOTICES.md).

## Attach to an existing tab

Run the controller on the VM host. Set `TTT_PAGE_URL` to the exact URL of an
already open Safari tab. If `url` is omitted, exactly one inspectable Safari tab
must exist. Another Inspector connection must not already own that tab.

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
| `query_selector(selector, strategy='css')` | First native DOM node ID or `None`. `strategy='xpath'` uses WebKit's search protocol and releases the temporary search. Node IDs are temporary; do not retain them across document changes. |
| `wait_for_selector(selector, strategy='css', state='attached', timeout=30)` | Polls a fresh document for attached/detached state, including a process replacement. Visibility is not inferred. |
| `wait_for_url(pattern, timeout=30)` | Waits for a committed document URL matching a case-sensitive glob. Redirect expectations remain explicit. |
| `wait_for_timeout(milliseconds)` | Cancellable delay, from 0 to 300,000 milliseconds. Other timeouts use seconds. |
| `expect_request(predicate, timeout=30)` | Async context manager registering a request waiter before an action. Accepts a URL glob or synchronous predicate over a `NetworkRequest`. |
| `expect_response(predicate, timeout=30)` | Equivalent response waiter; `waiter.value` is available after the context exits. |
| `response.body()`, `.text()`, `.json()` | Wait for `loadingFinished`, then retrieve the native body. Failure, eviction, redirect replacement and destroyed page processes are errors. Default body limit is 16 MiB. Text defaults to UTF-8; pass `encoding` explicitly for other encodings. |
| `reload()` | One native WebKit reload request. Completion does not mean the page is ready. |
| `goto(url)` | Verifies that the inspected tab has a unique URL and that the native active editing field contains that exact URL, then uses the supplied `Browser`'s address-bar path. Native input is not retried. Follow it with a URL/selector condition. The optional [native focus reader](native-focus.md) avoids a whole-tree query. Initial focus and foreground verification can still fail. |
| `screenshot(path=None)` | Uses the supplied `Browser`'s native or configured QMP capture. Review the image separately from DOM/network success. |

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
`health` and `stop` are single-line administrative commands when no session owns
the bridge. `open` returns one JSON identity record, then the same connection
carries native length-prefixed plist messages. EOF releases both the guest TCP
service socket and XPC handoff. There is no network listener exposed outside the
guest and no separate WebSocket stream. An external tunnel can be supplied as
an async `InspectorTransport(bridge_socket=...)` factory returning a connected
socket; the repository contains no provider-specific tunnel implementation.

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
