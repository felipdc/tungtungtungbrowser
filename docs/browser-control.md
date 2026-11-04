# Native browser control

Use the [quickstart](../README.md#start-with-a-prepared-vm) to start a prepared
VM. This reference describes the current Python and CLI behavior, native
readiness checks, clock management and failure evidence. Planned DOM and
network APIs remain in the [roadmap](roadmap.md).

## Startup and readiness

The health response confirms the native control daemon. It does not certify
that Safari is ready to receive navigation. The readiness command unlocks the
guest, launches Safari and verifies its foreground identity, recording timings
and a screenshot. Choose a new `.json` output path for each measurement; existing
reports and screenshots are preserved. Initial report creation and subsequent
checkpoints publish complete JSON atomically. Initial publication also refuses
an existing filename, including one claimed concurrently by another observer.
A serialization failure leaves the preceding report intact. This prevents a
polling reader from observing an empty initial report; it does not change native
startup or retry failed browser actions. The report records the current stage
during startup and retains the native `safari_launch_response`. A launch can
return PID `0` or `frontmost_verified: false`; the report preserves that response.
When the launch reply explicitly reports `frontmost_verified: true`, readiness
uses that native identity verification and its timestamp. Otherwise it queries
foreground identity separately. `safari_foreground_source` records which native
method supplied the observation. This avoids another application scan after an
already verified launch. A launch acknowledgement without verified foreground
identity does not establish readiness, and a screenshot still needs visual
review. If readiness fails and QMP is configured, it also attempts a
separate `.failure.png` display capture with a five-second QMP deadline. A failure
capture does not establish readiness, and its own error does not replace the
original failure. Unlock input and the Safari launch request are each sent once.
An unlock error, including a connection reset or a reply that still reports a
locked screen, stops the measurement before Safari launch. Inspect the retained
report and display before starting another readiness command; a failed transport
does not establish whether the guest received the action. Cold startup currently
takes minutes. Capture and review page content separately:

```sh
docker compose -f runtime/compose.yaml exec iphone \
  python3 -m tungtungtungbrowser --qmp-socket /vm/control.sock \
  capture 'https://example.com/' \
  --settle-seconds 30 --output /vm/captures/example-001
```

Each capture has an exclusive output directory, a screenshot, metadata
and a manifest. Add `--accessibility` or `--ocr` when you need native text
diagnostics. These queries can be slow; they run after the screenshot, and
their failures are recorded without discarding it. A missing or invalid
screenshot fails the command and leaves a failure manifest. Navigation remains
unverified until the captured content has been checked.

## Screen-state inspection

For a read-only startup diagnostic, use
`python3 -m tungtungtungbrowser --timeout 30 screen-state` inside the container,
or `Browser().screen_state()` in Python. This reads three native SpringBoard
notifications without waking the display, pressing buttons, or collecting a
full device snapshot. Before SpringBoard reports startup, `locked` and
`screen_off` are `null` because the initial notification values are not yet
reliable. The raw integer values are retained in `states`.

The three reads are sequential, so they can straddle a screen transition or
SpringBoard restart. The timeout applies to each request, and an error stops
the operation without retries or partial success. An unlocked notification can
precede the rendered Home screen; `visual_readiness_verified` stays `false`.
Review a screenshot and verify the application separately before sending input.

## Native Home input

Use `Browser().home()` or `python3 -m tungtungtungbrowser home` to send the
native Home-button event. This makes one request and returns the acknowledgement
with `home_screen_verified: false`. Capture the display afterward to check the
result. Keyboard commands such as `press('cmd+h')` use a separate keyboard input
path and may not take the device to Home. A failed or timed-out Home request is
not repeated automatically; inspect the device before sending more input.

## Native logs

For native startup and app-launch diagnostics, capture a short interval of
the guest's unified log:

```sh
docker compose -f runtime/compose.yaml exec iphone \
  python3 -m tungtungtungbrowser logs --seconds 10 \
  --process MobileSafari --level all --max-lines 500 \
  --output /vm/diagnostics/safari-001.json
```

This listens to new native log events; it does not read historical logs or
require a guest `log` executable. Omit `--process` for all processes; the default
level is `error`. Process matching is case-insensitive and accepts a substring.
Duration is bounded to 0.1–60 seconds and retained entries to 1–5000. Filtering
happens before the entry limit, and `truncated` records whether more entries
matched. A zero-entry capture does not establish app health. Allow the global
`--timeout` to cover the capture interval plus guest processing time.

Each capture creates a new file with mode `0600`, saves its timing and status,
and preserves a failure report if the native request fails. Existing files are
never overwritten, and requests are not automatically retried. The CLI prints
the file path and counts; log contents stay in the file. Native logs may include
application data, so keep these artifacts outside the source repository.
Python callers can use `Guest().logs(seconds=10, process='MobileSafari')`
from `tungtungtungbrowser.guest` to receive the native result directly.

## Python API and touch input

An optional [calibrated native tap helper](native-touch-helper.md) can be
built and installed separately for the tested portrait display. Select it with
`Browser(touch_helper_sha256=...)` or `--touch-helper-sha256`. It uses native
IOKit events and does not inject scripts into Safari. The separate framework
field repair and live validation requirements are documented with the helper.

The Python controller has no third-party runtime dependencies:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e .
```

On the Linux runtime host, or inside its iPhone container, after completing
the startup readiness command:

```python
from tungtungtungbrowser import Browser

browser = Browser(cid=3, timeout=120)
browser.goto('https://example.com/')
# Wait for your application's expected state before using the captured content.
browser.screenshot('/absolute/path/to/captures/example.jpg')
browser.tap(120, 240)
browser.type_text('hello')
browser.press('return')
```

`tap(x, y)` takes native screen points. It sends one balanced native touch
sequence with a 50 ms requested contact delay, calculating both positions
before contact starts. Its result retains the native sequence's measured
`duration_ms` and released-finger state; scheduling can make the duration longer
than requested. The SDK does not repeat a gesture or fall back to another input
method after an error. An acknowledged gesture still needs an observed UI or
page result. See the [native input audit](native-control-audit.md#tap-timing)
for the timing problem this avoids and the remaining touch-fidelity work.

## Launching Safari

`launch()` unlocks the running guest and requests native Safari launch once.
The CLI equivalent is `python3 -m tungtungtungbrowser launch`. Use it before
the first navigation after a guest boot, or use `measure_ready.py` when you
also need startup timings and a screenshot. It starts the app inside an
already-running VM. URL opening alone can be acknowledged while the device
remains on Home; an observed request loaded only after Safari was explicitly
launched. Do not use an `open()` acknowledgement as an app-launch check.

The launch result retains native `pid`, `frontmost_verified` and any warning.
An unverified response is returned for inspection, including PID `0`; it is
not silently retried. `visual_readiness_verified` and `navigation_verified`
remain `false` even when native foreground identity is confirmed. Inspect the
display and verify page content separately. Unlock failure prevents launch,
and a failed or malformed launch response stops without further guest actions.

## Reading and synchronizing time

For a guest whose network cannot carry NTP, use `Browser().sync_clock()` or
`python3 -m tungtungtungbrowser sync-clock` before browsing. This makes one native
request offering the host's current UTC time to iOS `timed`; it does not change
the time zone or browser APIs. Use an accurately synchronized host clock. The
guest can restore stale time on boot even when its virtual RTC was correct;
see the [upstream clock investigation](https://github.com/Lakr233/vphone-cli/blob/83197152137ddd2aad70eb121d6b88a9afbee36e/Research/Guest/host_clock_sync.md).

The synchronization result retains native `time`, `automatic`, `changed` and the pre-correction
`offset` in seconds (host minus guest). Manual guest time is preserved: when
`automatic` is `false`, an offer leaves that clock alone. The readiness command's
optional `--sync-clock` step runs once after daemon health and before unlock,
records its response and timing, and stops before input if synchronization fails
or automatic time is unavailable or disabled. Failed requests are not retried.
This is a single synchronization, so run it again after a host suspend or VM
pause if needed. It does not create a background clock service.

## Read-only clock inspection

To inspect the current clock without correcting it, use `Browser().clock_state()`
or `python3 -m tungtungtungbrowser clock-state`. The result contains native
Unix `time` in seconds and `automatic` (`true`, `false`, or `null` when the mode
is unavailable). This sends one read request, with no host time or mode change.
It never initiates synchronization after an error. A returned timestamp alone
does not establish clock accuracy: bracket the call with host timestamps and
retain the request duration when comparing clocks. Inspect the launcher's
clock record separately; reading the clock does not restart failed maintenance.

## Managed clock offers

For longer sessions where the guest cannot reach NTP, the VM launcher can own
periodic native clock offers. Set `TTT_CLOCK_INTERVAL_SECONDS=60` before starting
Compose, or pass `--clock-interval-seconds 60` to `scripts/run_vm.py`. The proxy
Compose overlay enables this interval by default because it blocks UDP NTP.
The base Compose file defaults to `0` (disabled). Intervals from 10 to 3,600
seconds are accepted; `0` explicitly disables maintenance in either setup.

The launcher waits for daemon health, offers host time, then waits the interval
after each successful response. Health requests use a five-second socket
timeout; clock offers use 30 seconds to accommodate the native operation's
multiple waits. Both settings are recorded in `launch.json`. Health checks may
retry during startup;
failed clock offers are never retried. Manual guest time remains untouched and
ends maintenance. A clock failure is recorded without restarting the guest.
Use an accurately synchronized host clock and review `clock_maintenance` in
`launch.json`: it identifies a unique `clock-*.json` state file in the VM bundle.
That file retains the last response, offer count and any failure with mode
`0600`. The final state is also recorded in `exit.json`. When stopping the VM,
the launcher waits for an in-flight clock exchange before requesting native
shutdown, and sends no further clock offers.

The readiness command detects launcher maintenance and waits for its successful
automatic-time response before sending health, unlock or app-launch requests.
It reads the state file belonging to the current VM launch, records that state
and retains the original boot deadline. A failed, stopped or manual-time worker,
stale launch state, or invalid response stops readiness before native input.
This orders the initial correction, which can move guest time by many minutes,
before startup actions. The worker continues its periodic offers afterward.

Omit readiness's `--sync-clock` when using launcher maintenance; combining them
is rejected before guest requests. Do not start another periodic clock process
for that guest. For direct SDK control, finish the readiness command first: an
enabled setting alone does not establish synchronization, and `Browser.launch()`
does not read the launcher's state file. Include maintenance in the resource
measurements for that setup.

## Separate clock process

For a guest whose launcher does not manage the clock, a separate foreground
process remains available:

```sh
docker compose -f runtime/compose.yaml exec iphone \
  python3 -m tungtungtungbrowser --timeout 30 sync-clock --interval-seconds 60
```

This sends an initial offer, then waits the specified interval after each
successful response. It prints one flushed JSON line per response, preserving
the native offset and change result. Intervals from 10 to 3,600 seconds are
accepted. Requests never overlap within this process; a transport error or invalid native response
ends the process with an error instead of retrying. Manual time remains
untouched. A successful offer is a point-in-time result, not a guarantee of
accuracy between offers.

Ctrl-C or SIGTERM stops the loop. If a request is in progress, the process waits
for its response or configured request timeout, prints any successful response,
and exits without another offer. Run one clock process per guest and stop it
before stopping or replacing that guest. This command does not install a service
or start with the container automatically. Keep it out of baseline resource
measurements unless periodic synchronization is part of the measured workload.

## Navigation and tab reuse

`goto()` explicitly opens the URL in Safari through the native operating-system
API. `goto(reuse_tab=True)` is an experimental option that types into the active
Safari tab's address field and verifies the complete entered URL before
submitting. It requires native accessibility evidence that the field is editing;
if needed, it makes one focus attempt using the verified field's frame. Missing
or unconfirmed fields stop the operation before text entry. A completed native
tree response marked `truncated: true` with no usable URL field gets at most
one additional read after the input settling interval. The controller still
requires the URL identifier and matching Safari process; it does not infer
field identity from its text. Each read uses the configured request timeout.
Ambiguous fields, a different owner, and transport errors stop immediately.
The initial foreground reply must have valid verification and bundle fields.
Verified Safari also requires a valid process ID before Command-L or any other
address-bar input. Malformed replies stop with `GuestError`; they do not silently
fall back to opening a URL in another tab.
It selects existing
text before entering the URL and never repeats a failed input request. It falls
back to explicit Safari opening if another app is in the foreground. A local page
check exercised automatic refocusing; local and external HTTPS checks preserved
the active tab and saved-tab count. Native inspection can take tens of seconds,
and sustained page-load
validation remains incomplete. The CLI equivalent is
`python3 -m tungtungtungbrowser goto 'https://example.com/' --reuse-tab`. See the
[native control audit](native-control-audit.md#reusing-a-tab). Both paths
report a navigation request, not successful page rendering. Screenshots preserve
existing files unless `overwrite=True` is supplied. One `Browser` object
serializes its operations; use one controller per guest.

### Warm navigation observations

A native foreground trial with the corrected experimental QEMU update, native
FP enabled and TLB mode 0 completed three consecutive tab-reuse navigations.
Their local diagnostic callbacks arrived after 67.2, 22.9 and 25.1 seconds;
native URL submission alone took 57.2, 19.4 and 19.6 seconds. Safari retained
the same PID and process-start time, and its saved-tab count stayed at ten.
Three subsequent default URL opens produced callbacks in 16.3, 15.4 and 18.2
seconds, with saved tabs increasing from ten to thirteen. Each callback passed
the canvas, worker and WebGL checks. Native touch also passed. The database and
WAL were read separately, so those counts are observations rather than an atomic
snapshot guarantee. These are different sequential workloads in one guest, not an
isolated comparison of the two navigation methods.

The captures did not qualify fast, consistent rendering. For one default open,
the 30-second capture showed the saved observation identifier, the 60-second
capture showed the earlier collecting label again, and the 120-second capture
showed the saved identifier. No intervening navigation or input was sent, and
the served diagnostic source never resets that label after saving. Other
captures showed incomplete content, overlapping clock digits or address-bar
artifacts. All three default opens eventually displayed their matching saved
identifiers. This points to a display-state problem that still needs tracing;
it does not identify a specific renderer defect or prove the DOM regressed.

Cold readiness in this trial took 281.8 seconds. The 600-second startup window
averaged 2.28 CPU cores and sampled a 6.42 GiB whole-container peak. The following
900-second warm window, including local navigation, touch and external-page
observations, averaged 1.77 cores and reached the 6.5 GiB container limit,
recording 372 memory-limit events and zero OOM kills. Neither a smaller
supported host nor the experimental runtime is qualified by these results.
Prioritize repeated successful cold starts near the current 300-second budget
and correct warm rendering before further boot-time tuning.

A subsequent cold session with the same experimental runtime and disk parent
reached foreground Safari in 230.2 seconds, but its first complete local
diagnostic callback arrived 355.4 seconds after boot. Its initial foreground
capture was blank. Three later default URL opens in the same Safari process
produced successful worker, canvas and WebGL callbacks in 22.9, 12.8 and 16.5
seconds; native URL acknowledgements took 1.31, 0.59 and 0.61 seconds. These
are callback and request timings, not first-paint measurements. Fixed captures
at 15, 30, 60 and 120 seconds showed each matching saved identifier by 30
seconds, without the earlier backward status change in those samples. Native
touch passed, and a subsequent paired native/QMP capture showed matching settled
page content. This bounded success does not resolve the earlier intermittent
display issue or certify complete visual fidelity.

Saved tabs again grew from ten to thirteen. The following 720-second warm
measurement included those navigations, native touch, the paired captures and
two external HTTPS opens. It averaged 1.67 CPU cores and sampled a 6.38 GiB
whole-container peak, with no memory-limit events or OOM kills. It used the same
6.5 GiB container limit and no swap; the 600-second startup window sampled
6.39 GiB and averaged 2.22 cores. These differing workloads and observation
lengths do not establish a resource reduction. Keep tab ownership, consistent
rendering and repeated successful cold starts as separate adoption checks.

The same session's subsequent 420-second external-page window averaged 1.46
cores and reached the 6.5 GiB memory limit, recording 1,078 memory-limit events
and no OOM kills. It included fixed display captures and two brief host memory
inspection reads. This reinforces the need to measure sustained browsing beyond
the local diagnostic window before qualifying a smaller memory limit.

## Transport failures

Transport failures raise `GuestTransportError`, a subclass of `GuestError`.
Its `phase` identifies `connect`, `send_request`, `receive_headers`, or
`read_body`; `errno` retains the operating-system error number when available.
`request_may_have_been_sent` is false for a failed VSOCK connection and true
for later failures, where the native action's outcome is unknown. This flag
describes the failed exchange only: an operation such as `goto()` may already
have completed earlier steps. No transport failure automatically repeats the
request. Inspect the guest before repeating an action with an unknown outcome.
Responses shorter than their declared HTTP body length fail during `read_body`,
even if the received bytes form valid JSON. Error messages report byte counts
without including the response body.

## Display capture through QMP

For faster screenshots, capture the virtual display through its local QMP
socket. This avoids guest JPEG encoding, which can stall at Retina resolution:

```python
browser = Browser(qmp_socket='/absolute/path/to/your/ios-bundle/control.sock')
shot = browser.screenshot('/absolute/path/to/captures/example.png')
print(shot.metadata())
```

Inside the container, use `/vm/control.sock`. The output file can be anywhere
the caller can write; the host and container do not need matching output
paths. The CLI equivalent is `tungtungtungbrowser --qmp-socket /vm/control.sock
screenshot /vm/captures/example.png`. `--display-timeout` sets a separate
capture deadline, defaulting to 15 seconds.

Without `qmp_socket`, `screenshot()` returns a native `Screenshot` with `.jpeg`
bytes. With it, the method returns a `DisplayScreenshot` with `.png` bytes.
Both support `save()` and `metadata()`. Display capture includes virtual
display overlays and reports pixel dimensions; its `coordinate_scale` is
`None` because QEMU cannot establish the guest's screen-point scale. Use
native device geometry when converting pixels to touch coordinates. A valid
image alone does not establish browser readiness or successful page loading.
Capture failures are reported without silently switching backends.

The QMP backend requests raw RGB frames and encodes PNG in the controller after
releasing the monitor connection. This reduces time spent encoding under the
emulator's global lock. It handles the pinned QEMU's row padding without changing
pixel geometry. Raw frames are bounded to 32 MiB; encoded files may be larger
than QEMU's own PNG output. This changes capture overhead, not rendering fidelity
or cold-start readiness.

QMP is a VM administration socket: keep the VM directory private and use one
controller at a time. The SDK opens a short-lived connection for each capture
and does not expose QMP over the network.

The CLI has corresponding `goto`, `screenshot`, `tap`, `swipe`, `type` and `key`
commands. `open` retains the lower-level native URL-opening behavior.

The controller uses VSOCK and a narrow native method allowlist. URL navigation
accepts HTTP(S) and explicitly targets Safari. Read the
[native control audit](native-control-audit.md) for the pinned source path.
