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
firmware preparation**. A prepared guest has run with 2–3 GiB guest RAM, four
virtual CPUs and a 1 GiB translation cache, but the total container also holds
emulator, renderer, shared graphics allocations and filesystem-cache memory.
The 2 GiB guest profile has reached about 7 GiB of total container memory in
single-session browsing tests; it is an experimental setting, not a 2 GiB host
requirement. Linux's `file` memory counter includes shared memory and should
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
docker compose -f runtime/compose.yaml exec iphone \
  python3 scripts/measure_ready.py /vm --qmp-socket /vm/control.sock \
  --output /vm/readiness.json
docker compose -f runtime/compose.yaml exec iphone \
  python3 -m tungtungtungbrowser open 'https://example.com/'
```

The health response confirms the native control daemon. It does not certify
that Safari is ready to receive navigation. The readiness command unlocks the
guest, launches Safari and verifies its foreground identity, recording timings
and a screenshot. Choose a new `.json` output path for each measurement; existing
reports and screenshots are preserved. The report records the current stage
during startup. If readiness fails and QMP is configured, it also attempts a
separate `.failure.png` display capture with a five-second QMP deadline. A failure
capture does not establish readiness, and its own error does not replace the
original failure. Cold startup currently takes minutes. Capture and review
page content separately:

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

On the Linux runtime host, or inside its iPhone container:

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

`goto()` explicitly opens the URL in Safari through the native operating-system
API. `goto(reuse_tab=True)` is an experimental option that types into the active
Safari tab's address field and verifies the complete entered URL before
submitting. It falls back to explicit Safari opening if another app is in the
foreground. Tab reuse has not passed sustained page-load validation. Both paths
report a navigation request, not successful page rendering. Screenshots preserve
existing files unless `overwrite=True` is supplied. One `Browser` object
serializes its operations; use one controller per guest.

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
