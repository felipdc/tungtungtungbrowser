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
been exercised. Reliable cold starts, responsive use of warmed browsers,
physical-device fingerprint equivalence and sustained workload limits remain
under development. The current cold-start target is roughly 300 seconds;
repeatable startup and warm navigation take priority over reducing that time
further. This is a development target, not a startup guarantee.

Current performance is provisionally accepted for API development. An image
built from published commit `25cb749` passed one cold startup and three targeted
warm session/native-focus/navigation/cleanup cycles on the same guest, using
the explicit experimental settings below. Safari reached foreground in 229.8
seconds; warm session creation ranged from 17.6 to 107.5 seconds and the three
warm navigation calls took 33.7–34.9 seconds. These are individual observations,
not latency guarantees or general startup qualification.

The browser API has exercised owned sessions, navigation across sites, DOM
conditions, original/rendered HTML, JSON response capture, CSS/XPath clicks,
native screenshots and verified owned-tab cleanup. See the
[qualification record](docs/inspector-api.md#reusable-profile-qualification-2026-11-11)
and [roadmap](docs/roadmap.md) for measured scope and remaining limits.

Four independent Docker phones have passed one cold cohort and two concurrent
product rounds on a sixteen-core, 32-GiB Linux host with configured 6-GiB
container limits. Each round returned three visible products with matching API
JSON and one login outcome, with no OOMs. Longer browsing and complete cleanup
qualification remain open. The [multi-phone guide](docs/multiple-phones.md#targeted-launch-cold-cohort-and-two-product-rounds)
records the measured scope and limitations, alongside cloning and resource setup.

The validated host platform is **Linux x86_64 with Debian 13**, Docker Compose,
and Mesa lavapipe. ARM hosts and other distributions need their own validation.
An ARM64 build booted iOS, but its initial Safari window and local-page checks
failed. Successful compilation and guest startup do not establish ARM browser
support.
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

## Get started from scratch

Use a Debian 13 x86_64 host with four CPU cores, 16 GiB RAM and at least
100 GiB free disk for the first build. Keep additional space for VM copies,
firmware downloads and failed-run evidence. Start with this size before trying
the smaller experimental configurations.

Install Git, Python with venv support, Docker Engine/CLI and Docker Compose.
Check `docker version` and `docker compose version`; the Debian daemon package
alone does not install every client component. Your user must be able to run
Docker. Before firmware creation, confirm matching kernel headers and the
`usbip_core`, `vhci_hcd` and `vhost_vsock` modules are available; see the
[host setup guide](docs/setup.md). The runtime build invokes upstream dependency
installation with `sudo`.

```sh
git clone https://github.com/felipdc/tungtungtungbrowser.git
cd tungtungtungbrowser
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e '.[inspector,pointer]'

# Keep downloaded sources, build state, firmware and captures outside the repo.
export TTT_RUNTIME_DIR="$HOME/.cache/tungtungtungbrowser/vphone-linux"
export TTT_STATE_DIR="$HOME/.local/state/tungtungtungbrowser"
bash scripts/build_runtime.sh
bash scripts/create_vm.sh --list
```

Choose a device/iOS pairing from that list. Inspect its creation flow before
running it (replace both placeholders with the listed values):

```sh
bash scripts/create_vm.sh --device '<device identifier>' --ios '<iOS version>' \
  --scale 3 --name browser-device --dry-run
bash scripts/create_vm.sh --device '<device identifier>' --ios '<iOS version>' \
  --scale 3 --name browser-device
```

Creation downloads firmware separately and follows upstream's resumable setup.
Complete first-boot migration and cleanly stop the upstream emulator. Use the
created bundle's absolute path as `TTT_VM_DIR` below. See the pinned upstream
[creation guide](https://github.com/YahiaAngelo/vphone-linux/blob/888d17ca17268aaecd277c64f2d95385b184744b/docs/CREATE-ON-LINUX.md)
and [runtime patches](patches/README.md). This repository supplies no firmware.

For the complete API, the prepared research guest also needs:

- Safari's **Web Inspector enabled** in its Advanced settings. The bridge does
  not silently change this setting.
- Support for the native helpers' platform/accessibility/HID entitlements.
  Installing an executable on an ordinary physical iPhone is insufficient.
- The matching [native touch framework repair](docs/touch-repair.md), installed
  and verified using the [native installation guide](docs/native-touch-installation.md).
  VM creation and helper installation do not apply this build-specific repair.
- The tested portrait display geometry: **393 × 852 points at scale 3** for
  calibrated taps. A different scale or rotation needs separate qualification.

Start the runtime and complete readiness before installing the API helpers.
If you only need native navigation/capture, `pip install -e .` installs the
dependency-free base controller; the Inspector and pointer extras are optional.

## Start with a prepared VM

Use a VM bundle you created with the pinned runtime. It must contain `Disk.img`,
`nvram.bin`, `ecid`, `config.plist` and the other upstream boot assets. Stop its
previous emulator cleanly before starting it here; one process may own a disk.
Check [filesystem access](docs/setup.md#filesystem-access) when the bundle was
created under a different user. The restricted container needs explicit access
to its mounted files even though its default UID is 0.

```sh
export TTT_VM_DIR=/absolute/path/to/your/ios-bundle
export TTT_RUNTIME_DIR=/absolute/path/to/built/vphone-linux

# Use the same profile and Compose files for every command in this shell.
ttt_compose() {
  docker compose --env-file runtime/api-experimental.env \
    -f runtime/compose.yaml "$@"
}
ttt_compose build
docker volume create tungtungtungbrowser-runtime-leases
# Confirm the QEMU executable loads before opening guest disks.
ttt_compose run --rm --no-deps \
  --entrypoint /runtime/qemu/build/qemu-system-aarch64 iphone --version
ttt_compose up -d
# The profile enables launcher-owned clock maintenance; do not add --sync-clock.
ttt_compose exec iphone \
  python3 scripts/measure_ready.py /vm --qmp-socket /vm/control.sock \
  --output /vm/readiness-001.json
```

The readiness command waits for native control, unlocks the device and checks
that Safari is in the foreground. Cold startup currently takes minutes. Use a
new report path for each run; the report preserves timing and failure evidence.
A successful request does not prove page rendering. Capture the display and
check the content:

```sh
ttt_compose exec iphone \
  python3 -m tungtungtungbrowser --qmp-socket /vm/control.sock \
  capture 'https://example.com/' \
  --settle-seconds 30 --output /vm/captures/example-001
```

Read [startup and readiness](docs/browser-control.md#startup-and-readiness)
for foreground verification, clock ordering, capture evidence and handling a
failed request without repeating an action with an unknown outcome.
Use the [session-transition diagnostic](docs/session-lifecycle.md) to record
one Home/Safari round trip, check process continuity and retain native captures.
For native crashes, use the [offline crash diagnostics](docs/crash-diagnostics.md)
to match saved reports to the guest boot UUID. New report filenames and
modification times can belong to an earlier boot.

Stop through Compose so iOS can flush its filesystem:

```sh
ttt_compose stop
```

The launcher records cold-start parameters in `launch.json` and shutdown
results in `exit.json` inside the VM bundle. It holds a disk lease throughout
QEMU's lifetime. Upstream launchers do not honor that lease, so keep one owner.
Check `shutdown.forced_termination` in `exit.json`: `true` means the host sent
a termination signal after the guest's shutdown grace period. QEMU can return
exit code `0` in that case; that code alone does not establish a clean iOS
shutdown. Preserve the stopped VM bundle before investigating a failed shutdown.

## Recommended experimental settings

For multiple phones on one host, use the [multi-instance guide](docs/multiple-phones.md).
It provides separate Compose projects, VM bundles, caches, CIDs and API deployment
records. Do not scale a single phone service against one writable VM bundle.

Use [runtime/api-experimental.env](runtime/api-experimental.env) as a starting
profile for API development. Environment variables already exported in your
shell override Compose's env-file values; inspect `ttt_compose config` and the
resulting `/vm/launch.json` when comparing runs.

| Setting | Profile value / reason |
| --- | --- |
| Guest | 3 GiB RAM, 2 application CPUs |
| Container | 10 GiB RAM, no additional swap, 4 CPU quota, 6 GiB shared-memory allowance |
| Renderer | CPU Lavapipe, 1 thread; Mesa Vulkan driver pinned by the Dockerfile |
| Translation cache | 1024 MiB |
| Clock maintenance | 60 seconds, owned by the launcher |
| Renderer census | Explicitly enabled at `/vm/census`; permits and records approximations that strict mode can refuse |
| Native guest RPC | 120 seconds in `BrowserProfile` |
| Inspector session / transport | 300 / 240 seconds; at most 4 bounded initial handoff attempts |
| Native input | 5-second settling interval, explicit `touch_hold_ms=0` |
| Selector clicks | Pass `timeout=120` explicitly; response expectations around the action should allow at least 180 seconds |

The memory allowances overlap: shared memory is charged to the container's
memory limit, not an extra 6 GiB allocation. These are tested starting settings,
not guaranteed minima or deadlines for the complete workflow. Action and
condition methods retain their own timeout arguments, generally 30 seconds;
pass longer deadlines explicitly for slow emulation. `BrowserProfile` leaves
the default `Browser` and `InspectorSession` constructors unchanged.

Session deadlines do not override individual action deadlines. A cold trial's
foreground check took 38 seconds; its 60-second CSS action then expired during
native element selection, before the actual click. The SDK waited for the
in-flight contact to finish and restored Inspector mode. Do not replay a timed
out action automatically: inspect its evidence first. The example below uses
120 seconds for clicks and 180 seconds for their request/response expectations;
an expectation's clock starts on entry and includes the action itself.

The successful workflow additionally used the optional patched QEMU
`14c9913d` experiment with **`VPHONE_NATIVE_FP=1`, `QEMU_TLB_UNSYNCED=0`**.
The default `build_runtime.sh` still builds the pinned baseline. The env file
does not replace that binary or apply its FP boundary patch. Follow the
[native FP evaluation](docs/native-fp.md) to prepare and qualify a separate
candidate; record the binary hash and actual environment in each trial.
Do not treat a baseline build as reproduction of that experimental result.

Once the candidate is prepared, use its path **inside the container** and add
the candidate overlay to every Compose invocation:

```sh
export TTT_QEMU_EXECUTABLE=/runtime/path/to/qualified/qemu-system-aarch64
ttt_compose() {
  docker compose --env-file runtime/api-experimental.env \
    -f runtime/compose.yaml -f runtime/compose.api-experimental.yaml "$@"
}
ttt_compose run --rm --no-deps --entrypoint "$TTT_QEMU_EXECUTABLE" iphone --version
```

This overlay sets native FP to 1 and TLB mode to 0 and requires an explicit
executable. It does not download or qualify that executable.

To assess rendering without census mode, use a separate preserved VM trial with
`TTT_RENDER_DIAGNOSTICS_DIR=''`. The base Compose file defaults to census off;
the named experimental profile opts in. Disabling census is distinct from the
renderer’s stronger `REIMS_VGPU_STRICT=1` audit setting and does not itself
establish Metal fidelity. A DOM or WebKit
snapshot can succeed while the composed device image is blank. Review native
captures independently, and retain failed attempts. Keep the device warm for
targeted fixes; reserve complete workflows for milestones.

A separate census-disabled trial of the same image reached Safari foreground
in 243.0 seconds. Its local fixture was blank at the first capture and visibly
painted at the later capture, after roughly a minute. This assesses local
document rendering only: no Inspector session, selector action, page-script
completion or external workflow was qualified without census. The experimental
profile therefore retains its explicit census setting.

## Python and native control

The controller has no third-party Python runtime dependencies:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e .
```

On the Linux runtime host, or inside the iPhone container, complete startup
readiness first, then use the native controller:

```python
from tungtungtungbrowser import Browser

browser = Browser(cid=3, timeout=120,
                  qmp_socket='/absolute/path/to/your/ios-bundle/control.sock')
browser.goto('https://example.com/')
# Wait for your application's expected state before checking captured content.
browser.screenshot('/absolute/path/to/captures/example.png')
browser.tap(120, 240)  # Native screen points.
browser.type_text('hello')
browser.press('return')
```

Inside the container, use `/vm/control.sock`. Use one controller per guest.
An accepted navigation or gesture does not verify its effect; review native
screenshots or application evidence. Failed input is not retried automatically.

The [native browser control reference](docs/browser-control.md) covers:

- Python and CLI navigation, touch, typing and screenshots.
- Startup readiness, native screen state, Home input and log capture.
- Clock inspection, one-shot synchronization and launcher-owned maintenance.
- Experimental tab reuse and transport failure handling.

An optional [calibrated native tap helper](docs/native-touch-helper.md) has a
separate build and installer. Its framework field repair has an
[offline preparation tool](docs/touch-repair.md) and a separate experimental
[native installer](docs/native-touch-installation.md), with full backups and
explicit reboot verification. VM creation does not install that repair
automatically. Live Safari validation is still required. The controller uses
native interfaces without injected page scripts or JavaScript property
replacements.

Tab reuse and owned-tab cleanup require a native address-field reader. The
provided [native focus reader](docs/native-focus.md) verifies the editing field
after keyboard layout changes and has a separate native build and installer.
The SDK does not fall back to the daemon's flat UI-tree API, which can leave
system accessibility and automation switches enabled.

An optional [native navigation key helper](docs/native-keyboard.md) dispatches
each chord's release before its settling pause and records dispatch timing.
It has a separate build and installer and is selected through `Guest`.

The optional [Inspector API](docs/inspector-api.md) provides rendered HTML,
attached/detached selector waits, verified CSS/XPath DOM focus and native
request/response observation, including response bodies. Experimental
[CSS/XPath clicks](docs/selector-clicks.md) combine native Inspector selection
with a calibrated touch helper and require the optional `pointer` dependency.
Inspection has a separate native bridge build and installer. Request
modification, streaming HTML, automatic scrolling and full lifecycle coverage remain
on the [API roadmap](docs/roadmap.md#browser-api-implementation-backlog).

## Install the browser API helpers

Build on Linux with `clang-19`, `ld64.lld-19`, `ldid` and Python 3.11+. The
helper scripts use project-authored linker declarations and do not need an
Apple SDK. Their individual guides describe tool-path overrides:
[touch](docs/native-touch-helper.md), [keyboard](docs/native-keyboard.md),
[focus](docs/native-focus.md), [Inspector bridge](docs/inspector-api.md).
Check that these tools are on `PATH` before building.

On Debian, install the LLVM tools and the dependencies for building the
[Procursus ldid signer](https://github.com/ProcursusTeam/ldid/tree/v2.1.5-procursus7)
in an external directory:

```sh
sudo apt-get install clang-19 lld-19 build-essential pkg-config libssl-dev libplist-dev
mkdir -p "$HOME/.cache/ttt-tools"
git clone --branch v2.1.5-procursus7 --depth 1 \
  https://github.com/ProcursusTeam/ldid.git "$HOME/.cache/ttt-tools/ldid"
make -C "$HOME/.cache/ttt-tools/ldid" -j2
export PATH="$HOME/.cache/ttt-tools/ldid:$PATH"
```

```sh
command -v clang-19 ld64.lld-19 ldid
export TTT_API_WORK="$HOME/.local/state/ttt-api"
mkdir -p "$TTT_API_WORK"
bash scripts/build_browser_helpers.sh "$TTT_API_WORK/build-001"

# Run on the Linux VM host where guest CID 3 is reachable over VSOCK.
# Complete runtime readiness and the guest prerequisites first.
python3 scripts/install_browser_helpers.py "$TTT_API_WORK/build-001" \
  --cid 3 --output "$TTT_API_WORK/deploy-001"
export TTT_API_DEPLOYMENT="$TTT_API_WORK/deploy-001"
```

Both output directories must be new and outside the checkout. The installer
verifies source/binary hashes for all four helpers before its first upload.
It installs touch, keyboard and focus helpers, then starts the Inspector bridge
in explicit process mode and verifies its process/boot identity. No touch or
keyboard input is sent by installation. A completed `profile.json` enables
`BrowserProfile`; a partial installation remains a failure with its records and
guest files preserved. Do not blindly rerun installation after a timeout.

Install once for a batch, then reuse the running bridge and the deployment
records for each session. Touch/keyboard executables have content-addressed
paths: their individual installers deliberately refuse to overwrite an existing
installation. For an already prepared guest, reuse its verified installation
records and use the individual installers for changed helpers. A failed bundle
is not automatically resumed or rolled back. Stop/reinstall the bridge after a
guest reboot; its previous process identity is no longer current. See the
[bridge lifecycle instructions](docs/inspector-api.md#installation).

## Browser API: a complete first example

The async API inspects WebKit's native DOM and Network domains. Navigation and
touch use native Safari/iOS input. There is no `evaluate()` API, page-script
injection, request interception or replacement of browser properties.

Start the repository's [local fixture](docs/api-fixture.md) in the runtime's
network namespace. Leave this command running in another terminal:

```sh
ttt_compose exec iphone python3 scripts/serve_probe.py \
  --output /vm/fixture-001 --request-log /vm/fixture-001-requests.jsonl
```

The server prints a random `qemu_guest_url`. Append `api-workflow.html` to it
and set `TTT_FIXTURE_URL` in the host shell running Python. For example, a
printed `http://10.0.2.2:8767/<random-path>/` becomes
`http://10.0.2.2:8767/<random-path>/api-workflow.html`. Use the actual printed
path. The loopback server must share QEMU's network namespace for this route.

```sh
export TTT_FIXTURE_URL='<qemu_guest_url>api-workflow.html'
export TTT_CAPTURE_DIR="$TTT_API_WORK/capture-001"
```

Run this Python on the host using the venv with both extras installed:

```python
import asyncio
import os
from pathlib import Path
from tungtungtungbrowser import BrowserProfile


async def main():
    profile = BrowserProfile(os.environ['TTT_API_DEPLOYMENT'])
    browser = profile.browser()  # Native device JPEGs; no QMP override.
    fixture = os.environ['TTT_FIXTURE_URL']
    output = Path(os.environ['TTT_CAPTURE_DIR'])
    output.mkdir(parents=True, exist_ok=False)
    page = profile.session(browser=browser, new_tab_url=fixture)
    async with page:
        # The session creates and owns one tab using this caller-owned fixture.
        # Its initial document response predates attachment: observe a reload.
        async with page.expect_response(
            lambda r: r.url.split('#', 1)[0] == fixture, timeout=120
        ) as document:
            await page.reload()
        await page.wait_for_selector('#state[data-ready="true"]', timeout=120)
        original = await document.value.text(timeout=120)
        rendered = await page.content()
        (output / 'original.html').write_text(original)
        (output / 'rendered.html').write_text(rendered)

        # Register before clicking so a fast request is not missed.
        async with page.expect_request('*api-fixture-data.json', timeout=180) as request, \
                page.expect_response('*api-fixture-data.json', timeout=180) as response:
            await page.click('#load-data', timeout=120)
        assert response.value.status == 200
        payload = await response.value.json(timeout=120)
        assert payload['item']['id'] == 'fixture-item'
        await page.wait_for_selector('#state[data-response="200"]', timeout=120)

        await page.click("//button[@id='record-click']", strategy='xpath', timeout=120)
        await page.wait_for_selector('#state[data-clicked="true"]', timeout=120)
        await page.screenshot(output / 'device.jpg')

        destination = fixture + '?page=2'
        await page.goto(destination)
        await page.wait_for_url('*api-workflow.html?page=2', timeout=120)
        await page.wait_for_selector('#state[data-ready="true"]', timeout=120)
        assert 'Second document ready' in await page.content()
        await page.screenshot(output / 'second-device.jpg')

    # Context exit verifies owned-tab closure and releases native inspection.
    print(page.tab_cleanup)
    assert not page.cleanup_errors


asyncio.run(main())
```

Review the native JPEGs as well as the HTML/JSON. A DOM condition does not
establish completed device painting. The fixture's own JavaScript is ordinary
served page content; the controller does not inject it.

To attach an existing tab instead, use
`profile.session(browser=browser, url='https://example.com/')`. Closing that
session detaches inspection and leaves the existing tab intact. An owned tab
shares the current Safari cookies/site data; it is not a fresh isolated browser
context. Avoid manual tab switches and keep one controller per guest.

## Selectors, actions and waits

CSS is the default. Standard WebKit CSS selectors work, including IDs, classes,
attributes and combinators. XPath must be selected explicitly:

```python
node = await page.query_selector('#load-data')
node = await page.query_selector('button:not([disabled])')
node = await page.query_selector("//button[@id='record-click']", strategy='xpath')
await page.wait_for_selector('#delayed-marker', timeout=90)
await page.wait_for_selector('.loading', state='detached', timeout=90)
await page.focus('#next-page')
```

`query_selector` returns a native node ID or `None`, not an element object.
Node IDs are short-lived; use selectors again for actions. Queries return the
first match, while `focus` and `click` require exactly one acceptable target.
Click resolves a visible target and verifies native hit selection before a
calibrated touch. It has no automatic scrolling, chained locators or input
retry. The supported strategies are `css` and `xpath`; text/role selectors,
frame locators and shadow-root traversal are not general APIs here. An invalid
XPath can currently appear as no match.

| Capability | API and practical limit |
| --- | --- |
| Rendered HTML | `await page.content()` serializes the current DOM; it is different from the original response. |
| Lookup | `query_selector(selector, strategy='css')`; returns first node ID or `None`. |
| Conditions | `wait_for_selector(..., state='attached'/'detached')`; presence is not visibility. `wait_for_url()` accepts a case-sensitive URL glob. |
| CSS/XPath action | `click(..., timeout=120)` uses native touch. `focus()` uses native DOM focus and does not establish keyboard ownership. |
| Navigation | `goto('https://example.com/')` verifies the owned/active native address field. Wait for the destination URL and a condition specific to the new document. `reload()` requests one native reload; verify a fresh-document condition afterward. |
| Network observation | `expect_request()` / `expect_response()` accept a URL glob or synchronous predicate. Register before the action. Headers/status/URLs are available on the retained records. |
| Response content | `await response.body()`, `.text()`, `.json()` after loading finishes; default limit 16 MiB. No streaming/interception/fulfillment. |
| Device image | `screenshot()` uses native JPEG by default; configuring `Browser(qmp_socket=...)` selects a host display PNG. Includes browser UI. |
| Page image | `snapshot_rect(width=393, height=650)` returns a WebKit page PNG in an explicit CSS-pixel rectangle, without browser UI; it does not prove device composition. |
| Coordinate input | `browser.tap(x, y)`, `type_text(text)`, `press(name)`, `swipe(...)`; native input requires verified focus and screen geometry. No selector-based `fill()` API yet. |
| Session cleanup | `async with page` releases inspection. Owned tabs close by default; `retain_tab=True` keeps a newly owned tab for later sessions or phone disposal. `tab_cleanup`, `cleanup_errors` and `transport.failure` retain outcomes. |

Timeouts are seconds except `wait_for_timeout(milliseconds)`. A response
expectation's deadline starts when entering its context, including the action's
duration. A timeout never replays native input. Read-only waits may poll fresh
documents. On a connection or cleanup failure, preserve the record and inspect
the live state before another action; do not assume a timed-out key did nothing.

After closing every session, stop the bridge with its deployment identity:

```sh
python3 scripts/stop_inspector_bridge.py \
  "$TTT_API_DEPLOYMENT/bridge/deploy.json" --cid 3 \
  --output "$TTT_API_WORK/bridge-stop-001.json"
ttt_compose stop
```

See the [full API reference](docs/inspector-api.md),
[selector action limits](docs/selector-clicks.md) and
[native control reference](docs/browser-control.md) for detailed contracts.

## Proxy, resources and diagnostics

[Proxy routing](runtime/proxy/README.md) gives the entire iPhone a dedicated
network namespace. TCP and DNS go through the configured proxy; direct egress
is blocked. Configuration and credentials belong in an external private file.
The optional [browser-only mode](docs/browser-proxy.md) proxies Safari's WebKit
HTTP(S) traffic while other device traffic connects directly. Register with
`--proxy-scope browser` and configure Safari before launching it; device-wide
routing remains the default.

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
The [native FP evaluation](docs/native-fp.md) documents the optional upstream
CPU experiment, its numerical correctness gate and the boundary correction
required by the tested candidate.

## Development and licensing

See the [Inspector API guide](docs/inspector-api.md) for current experimental
DOM/network operations and the [development roadmap](docs/roadmap.md) for the
remaining workflow and action APIs.

Run `python3 -m unittest discover -s tests -v`. See
[CONTRIBUTING.md](CONTRIBUTING.md) for validation and repository boundaries.

Original controller and orchestration code is MIT licensed. The QEMU patch,
vendored syscall profile, runtime dependencies and firmware have separate
licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). This repository
distributes source and patches, rather than firmware or prepared device images.
