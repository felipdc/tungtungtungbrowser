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
Check [filesystem access](docs/setup.md#filesystem-access) when the bundle was
created under a different user. The restricted container needs explicit access
to its mounted files even though its default UID is 0.

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
# Omit --sync-clock if launcher clock maintenance is enabled.
docker compose -f runtime/compose.yaml exec iphone \
  python3 scripts/measure_ready.py /vm --qmp-socket /vm/control.sock \
  --sync-clock --output /vm/readiness.json
```

The readiness command waits for native control, unlocks the device and checks
that Safari is in the foreground. Cold startup currently takes minutes. Use a
new report path for each run; the report preserves timing and failure evidence.
A successful request does not prove page rendering. Capture the display and
check the content:

```sh
docker compose -f runtime/compose.yaml exec iphone \
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
docker compose -f runtime/compose.yaml stop
```

The launcher records cold-start parameters in `launch.json` and shutdown
results in `exit.json` inside the VM bundle. It holds a disk lease throughout
QEMU's lifetime. Upstream launchers do not honor that lease, so keep one owner.
Check `shutdown.forced_termination` in `exit.json`: `true` means the host sent
a termination signal after the guest's shutdown grace period. QEMU can return
exit code `0` in that case; that code alone does not establish a clean iOS
shutdown. Preserve the stopped VM bundle before investigating a failed shutdown.

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

HTML retrieval, DOM snapshots, selector waits and network observation or
interception are [planned APIs](docs/roadmap.md#browser-api-expansion--pursue-after-the-current-runtime-work).
They are not current SDK features.

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
The [native FP evaluation](docs/native-fp.md) documents the optional upstream
CPU experiment, its numerical correctness gate and the boundary correction
required by the tested candidate.

## Development and licensing

See the [development roadmap](docs/roadmap.md) for planned DOM, wait and
network APIs. These are future capabilities.

Run `python3 -m unittest discover -s tests -v`. See
[CONTRIBUTING.md](CONTRIBUTING.md) for validation and repository boundaries.

Original controller and orchestration code is MIT licensed. The QEMU patch,
vendored syscall profile, runtime dependencies and firmware have separate
licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). This repository
distributes source and patches, rather than firmware or prepared device images.
