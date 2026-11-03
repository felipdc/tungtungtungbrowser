# tungtungtungbrowser

Run native iOS Safari in a container-managed, CPU-rendered virtual iPhone on
Linux. Control navigation, touch input and screenshots through native guest
APIs. The controller does not inject page scripts or replace browser APIs.

The project combines a small Python controller with a pinned
[vphone-linux](https://github.com/YahiaAngelo/vphone-linux) runtime and optional
proxy routing. The same runtime layout works on a local Linux machine or a
Linux host rented from a cloud provider.

## Status and requirements

This is an experimental runtime. Native Safari navigation, external HTTPS,
page JavaScript, dedicated/shared workers, canvas, WebGL and touch input have
been exercised. Physical-device fingerprint equivalence, sustained workload
limits and fast cold startup remain under development.

The validated host platform is **Linux x86_64 with Debian 13**, Docker Compose,
and Mesa lavapipe. ARM hosts and other distributions need their own validation.
No hardware GPU or GPU device passthrough is used. The host needs
`/dev/vhost-vsock`; firmware creation additionally needs USB/IP-capable kernel
modules and matching kernel headers. Containers alone cannot supply these
host kernel features.

Allow **16 GiB host RAM and about 100 GiB free storage for initial builds and
firmware preparation**. A prepared guest has run with 2–3 GiB guest RAM, two to
four virtual CPUs and a 1 GiB translation cache, but the total container also holds
emulator, renderer, shared graphics allocations and filesystem-cache memory.
An experimental 2 GiB, two-CPU guest has completed local rendering and external
HTTPS browsing checks with a 6.5 GiB container limit on both 8 GiB and 16 GiB
hosts with four host CPU cores. Startup, worker and rendering failures have
also occurred; this is not a stable minimum host
requirement. The [resource settings](docs/setup.md#resource-settings) include
the exact profile and a measurement procedure. Linux's `file` memory counter
includes shared memory and should
not be treated as entirely reclaimable cache. Measure the whole container
for your workload before selecting a smaller host. Initial firmware preparation
and first-boot migration are separate from ordinary browser startup.

## Get the source

```sh
git clone https://github.com/felipdc/tungtungtungbrowser.git
cd tungtungtungbrowser
```

Use the [setup guide](docs/setup.md) to prepare a Linux host and VM, or follow
the commands below if you already have a compatible VM bundle. See the
[roadmap](docs/roadmap.md) for planned APIs and the
[contribution guide](CONTRIBUTING.md) for development checks.

## Start with a prepared VM

Use a VM bundle you created with the pinned runtime. It must contain `Disk.img`,
`nvram.bin`, `ecid`, `config.plist` and the other upstream boot assets. Stop its
previous emulator cleanly before starting it here; one process may own a disk.

```sh
export TTT_VM_DIR=/absolute/path/to/your/ios-bundle
export TTT_RUNTIME_DIR=/absolute/path/to/built/vphone-linux

# An explicit compatibility setting for the experimental software renderer.
# It records unsupported Metal behavior; rendering fidelity is not guaranteed.
export TTT_RENDER_DIAGNOSTICS_DIR=/vm/census

export TTT_VM_MEMORY_GIB=3
export TTT_VM_CPUS=4
export TTT_TCG_CACHE_MIB=1024

docker compose -f runtime/compose.yaml up --build -d
docker compose -f runtime/compose.yaml exec iphone \
  python3 -m tungtungtungbrowser health
# Omit --sync-clock if launcher clock maintenance is enabled.
docker compose -f runtime/compose.yaml exec iphone \
  python3 scripts/measure_ready.py /vm --qmp-socket /vm/control.sock \
  --sync-clock --output /vm/readiness.json
docker compose -f runtime/compose.yaml exec iphone \
  python3 -m tungtungtungbrowser open 'https://example.com/'
```

The health response confirms the native control daemon. It does not certify
that Safari is ready to receive navigation. The readiness command unlocks the
guest, launches Safari and verifies its foreground identity, recording timings
and a screenshot. Choose a new `.json` output path for each measurement; existing
reports and screenshots are preserved. The report records the current stage
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

Stop through Compose so iOS can flush its filesystem:

```sh
docker compose -f runtime/compose.yaml stop
```

The launcher records cold-start parameters in `launch.json` and shutdown
results in `exit.json` inside the VM bundle. It holds a disk lease throughout
QEMU's lifetime. Upstream launchers do not honor that lease, so keep one owner.
Check `shutdown.forced_termination` in `exit.json`: `true` means the host sent
a termination signal after the guest's shutdown grace period. QEMU can return
exit code `0` in that case; that code alone does not establish a clean iOS
shutdown. Preserve the stopped VM bundle before investigating a failed shutdown.

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

Use `Browser().home()` or `python3 -m tungtungtungbrowser home` to send the
native Home-button event. This makes one request and returns the acknowledgement
with `home_screen_verified: false`. Capture the display afterward to check the
result. Keyboard commands such as `press('cmd+h')` use a separate keyboard input
path and may not take the device to Home. A failed or timed-out Home request is
not repeated automatically; inspect the device before sending more input.

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

## Build and create a VM

The setup scripts resolve paths relative to this checkout and put downloaded
runtime sources under the user's cache directory. Set `TTT_RUNTIME_DIR` and
`TTT_STATE_DIR` to override those locations.

```sh
bash scripts/build_runtime.sh
bash scripts/create_vm.sh --list
bash scripts/create_vm.sh --device 'iPhone17,3' --ios 26.6.2 \
  --scale 3 --name browser-device
```

The build runs the upstream dependency installer, which uses `sudo`. Firmware
creation follows upstream device/version compatibility and resumable setup.
Use `--dry-run` to inspect a selected creation flow. Prepared devices normally
live in the upstream setup workspace, outside this checkout. Display scale is
a native guest setting: reducing it can change browser-visible geometry and
must be checked against the intended device profile.

See [setup details](docs/setup.md), [runtime patches](patches/README.md), and
the upstream [creation guide](https://github.com/YahiaAngelo/vphone-linux/blob/888d17ca17268aaecd277c64f2d95385b184744b/docs/CREATE-ON-LINUX.md).
Firmware and VM images are obtained separately under their own terms.

## Python and native control

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

For a guest whose network cannot carry NTP, use `Browser().sync_clock()` or
`python3 -m tungtungtungbrowser sync-clock` before browsing. This makes one native
request offering the host's current UTC time to iOS `timed`; it does not change
the time zone or browser APIs. Use an accurately synchronized host clock. The
guest can restore stale time on boot even when its virtual RTC was correct;
see the [upstream clock investigation](https://github.com/Lakr233/vphone-cli/blob/83197152137ddd2aad70eb121d6b88a9afbee36e/Research/Guest/host_clock_sync.md).

The result retains native `time`, `automatic`, `changed` and the pre-correction
`offset` in seconds (host minus guest). Manual guest time is preserved: when
`automatic` is `false`, an offer leaves that clock alone. The readiness command's
optional `--sync-clock` step runs once after daemon health and before unlock,
records its response and timing, and stops before input if synchronization fails
or automatic time is unavailable or disabled. Failed requests are not retried.
This is a single synchronization, so run it again after a host suspend or VM
pause if needed. It does not create a background clock service.

For longer sessions where the guest cannot reach NTP, the VM launcher can own
periodic native clock offers. Set `TTT_CLOCK_INTERVAL_SECONDS=60` before starting
Compose, or pass `--clock-interval-seconds 60` to `scripts/run_vm.py`. The proxy
Compose overlay enables this interval by default because it blocks UDP NTP.
The base Compose file defaults to `0` (disabled). Intervals from 10 to 3,600
seconds are accepted; `0` explicitly disables maintenance in either setup.

The launcher waits for daemon health, offers host time, then waits the interval
after each successful response. Its health checks may retry during startup;
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

`goto()` explicitly opens the URL in Safari through the native operating-system
API. `goto(reuse_tab=True)` is an experimental option that types into the active
Safari tab's address field and verifies the complete entered URL before
submitting. It requires native accessibility evidence that the field is editing;
if needed, it makes one focus attempt using the verified field's frame. Missing
or unconfirmed fields stop the operation before text entry. It selects existing
text before entering the URL and never repeats a failed input request. It falls
back to explicit Safari opening if another app is in the foreground. Local page
checks preserved the active tab and saved-tab count; automatic refocusing and
sustained page-load validation remain incomplete. See the
[native control audit](docs/native-control-audit.md#reusing-a-tab). Both paths
report a navigation request, not successful page rendering. Screenshots preserve
existing files unless `overwrite=True` is supplied. One `Browser` object
serializes its operations; use one controller per guest.

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
[native control audit](docs/native-control-audit.md) for the pinned source path.

## Proxy, resources and diagnostics

[Proxy routing](runtime/proxy/README.md) gives the entire iPhone a dedicated
network namespace. TCP and DNS go through the configured proxy; direct egress
is blocked. Configuration and credentials belong in an external private file.

Use the reversible native service profile to reduce background work, then
restart the guest:

```sh
docker compose -f runtime/compose.yaml exec iphone \
  python3 -m tungtungtungbrowser services trimmed
```

`services none` restores jobs changed by that profile. Readiness and resource
measurement tools are described in [diagnostics](diagnostics/README.md).
Cold boot and in-memory pause/resume must be reported separately. The pinned
virtual display does not support durable QEMU saved-state migration.

## Development and licensing

See the [development roadmap](docs/roadmap.md) for planned DOM, wait and
network APIs. These are future capabilities.

Run `python3 -m unittest discover -s tests -v`. See
[CONTRIBUTING.md](CONTRIBUTING.md) for validation and repository boundaries.

Original controller and orchestration code is MIT licensed. The QEMU patch,
vendored syscall profile, runtime dependencies and firmware have separate
licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). This repository
distributes source and patches, rather than firmware or prepared device images.
