# Linux setup

Use a Linux x86_64 host with Docker Compose, Python 3.11 or newer, a C toolchain,
matching kernel headers, `/dev/vhost-vsock`, and Mesa's lavapipe Vulkan driver.
Debian 13 is the validated baseline. Cloud provider selection is independent
of the browser runtime; the required kernel features must be available locally.

Check both `docker version` and `docker compose version` before starting.
Debian 13 packages the Docker daemon, command-line client and Compose
separately; installing `docker.io` with `--no-install-recommends` does not
install `docker-cli`. Install the client and Compose as well when using those
distribution packages.

`build_runtime.sh` pins the revision in `runtime.lock.json`, installs upstream
dependencies and applies the native JIT permission patch before compiling.
It places sources in `$XDG_CACHE_HOME/tungtungtungbrowser/vphone-linux` and build
records in `$XDG_STATE_HOME/tungtungtungbrowser`, using standard home-directory
fallbacks. `TTT_RUNTIME_DIR` and `TTT_STATE_DIR` override those paths.

Before firmware creation, check that `modinfo usbip_core`, `modinfo vhci_hcd`
and `modinfo vhost_vsock` succeed. Some minimal kernels omit USB/IP. Install an
appropriate kernel and matching headers through your distribution, then boot
it before creating a device. `select_standard_kernel.sh` is a Debian/GRUB
helper for selecting an already installed standard kernel; inspect it before
using it. It does not reboot the machine.

`create_vm.sh` applies the USB transport fixes, compiles the host-side restore
shim, checks kernel modules and forwards its arguments to the upstream creation
CLI. The upstream process downloads its own firmware and boot assets. Use
`--list` for supported pairings and `--dry-run` to inspect a creation flow.
The first boot includes data migration and is not a normal startup benchmark.

An experimental [native touch repair](touch-repair.md) can be prepared from an
exact matching extracted cache using a separate offline tool. It preserves
the source file and verifies the changed code page's signature. VM creation
does not install this optional, build-specific repair automatically.

After creation, cleanly stop the upstream VM before starting the Compose
runtime. Confirm no emulator still owns `Disk.img`. Keep VM bundles, proxy
credentials, deployment configuration and captured page content in external
directories. Do not put them into a source archive or container image.

The runtime and proxy Dockerfiles also build natively for ARM64. The ARM
images have passed controller import and proxy binary execution checks, but
the complete ARM runtime still needs native guest verification. The source
build, firmware creation and kernel-selection helpers remain validated only
on x86_64. A different host CPU alone does not establish acceleration for
Apple-specific CPU instructions.

The optional [ARM64 memory-access check](../native/README.md#arm64-linux-memory-access-check)
can test some native instruction requirements before a runtime build. Passing
it does not establish ARM runtime support or guest compatibility.

## Filesystem access

The default container runs as UID 0 with all Linux capabilities dropped. It
still needs permission to traverse the mounted VM directory, read its metadata,
write the disk and NVRAM, and create logs and renderer cache files. UID 0 in this
configuration cannot bypass a private directory owned by another user. Mounting
a bundle created under a different account can therefore fail before boot.

With the guest stopped, build the image and check access using the same Compose
files and environment as your runtime:

```sh
docker compose -f runtime/compose.yaml build iphone
docker compose -f runtime/compose.yaml run --rm --no-deps iphone \
  /vm --source /runtime --check-access
```

The check opens existing disk/state files without writing or truncating them,
creates and removes a temporary file in the bundle and any existing cache
directories, and checks the runtime launcher. It reports the container UID/GID
and failing path. Normal startup performs this check too. It does not start
QEMU, validate firmware, verify runtime libraries, or establish that another
emulator has released the disk. A successful check covers filesystem access
only; retain the separate native readiness and page-rendering checks.

Correct access on a dedicated, stopped runtime bundle. For the default UID 0
container under rootful Docker **without user-namespace remapping**, ownership
can be assigned on the host with:

```sh
sudo chown -R 0:0 -- "$TTT_VM_DIR"
```

This changes ownership of the bundle and its existing caches and reports. Keep
the original preparation bundle separately if its owner still needs to manage
it without elevated privileges. Rootless Docker and user-namespace remapping
use different host IDs; use the mapped identity for that daemon. The runtime
checkout also needs readable files and traversable directories. Rerun the
container check after correcting access; the launcher does not change ownership
or permissions automatically.

## Resource settings

Compose reads these variables from your shell or an external `--env-file`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `TTT_VM_MEMORY_GIB` | `3` | RAM visible to iOS |
| `TTT_VM_CPUS` | `4` | Virtual CPUs visible to iOS |
| `TTT_TCG_CACHE_MIB` | `1024` | QEMU translated-code cache limit |
| `TTT_RENDER_THREADS` | `1` | Software-renderer worker threads |
| `TTT_CONTAINER_MEMORY` | `10g` | Whole-container RAM limit; additional swap is disabled |
| `TTT_CONTAINER_CPUS` | `4` | Whole-container CPU quota |
| `TTT_SHM_SIZE` | `6g` | Shared-memory mount capacity, charged to container memory as used |
| `TTT_CLOCK_INTERVAL_SECONDS` | `0`; proxy overlay: `60` | Native time offers owned by the launcher; `0` disables, otherwise 10–3,600 seconds |

Guest RAM is only part of total memory use. Include the emulator, software
renderer, shared buffers and filesystem cache when sizing the host. The
container CPU quota also covers rendering and the controller; increasing
virtual CPUs does not increase that quota. Keep capacity for the host OS and
optional proxy container. Smaller values require workload validation and may
increase startup time or cause guest process termination.

### Experimental smaller profile

A prepared guest has completed native Safari startup, local canvas/WebGL and
worker checks, and an external HTTPS browsing observation with these settings
on both 8 GiB and 16 GiB Linux hosts with four CPU cores:

```sh
export TTT_VM_MEMORY_GIB=2
export TTT_VM_CPUS=2
export TTT_TCG_CACHE_MIB=1024
export TTT_RENDER_THREADS=1
export TTT_CONTAINER_MEMORY=6656m
export TTT_CONTAINER_CPUS=4
```

The observations used the upstream reversible `trimmed` service profile and
the readiness command's `--sync-clock` option. The native display remained
1179 × 2556 at scale 3. The environment variables above do not apply a service
profile or change display geometry. Inspect the current profile with
`python3 -m tungtungtungbrowser services status` inside the running guest
container; the [resource instructions](../README.md#proxy-resources-and-diagnostics)
describe applying and restoring it. Record that state with your measurements.

Apply settings after a clean guest shutdown, using the same Compose files and
VM bundle as your existing configuration. Verify `shutdown.forced_termination`
is `false` in the bundle's `exit.json` before recreating the container. Keep the
native display geometry unchanged when comparing memory measurements.

This profile reduces the container limit from the default 10 GiB to 6.5 GiB;
it does not establish a matching reduction in actual memory consumption. The
host still needs capacity for its OS and any proxy. Two virtual guest CPUs
also do not make this a two-core host profile: the container retains a four-core
CPU quota for emulation, rendering and control.

These settings remain experimental. Separate observations have included a
native launch response timeout, worker timeouts, and loss of visible page
content. A successful run does not erase those failures. A later cold run
completed native foreground verification but its local workers missed their
15-second deadline. Treat native readiness and usable page execution as
separate observations.

A later seven-minute browsing recording reached the 6.5 GiB hard limit and
recorded `memory.events` `max` events, with no OOM kill or CPU throttling. The
page remained visible in a separate later capture, while limit events continued
to accumulate. Successful rendering and the absence of OOM kills therefore do
not establish adequate memory headroom for a long-lived session at this limit.

One 420-second browsing recording on the 8 GiB host retained about 1.10 GiB
of available host memory at its lowest sampled point, with no host or container
OOM kills, CPU throttling or swap use. This is evidence for that prepared guest
and workload, not a stable minimum capacity guarantee. Build and firmware
preparation requirements remain separate. The default Compose limits have
not changed.

For your workload, record the following before adopting a smaller configuration:

1. Run `measure_ready.py` with a new report path and review its screenshot.
   Native foreground identity and page readiness are separate checks.
2. Navigate to the local diagnostics and review worker results and the rendered
   canvas, then exercise your external pages through the intended proxy.
3. Use `sample_resources.py --host-memory` for both startup and browsing. Start
   the startup recording as soon as the container runs. Review whole-container
   memory, host available memory, pressure, swap, OOM events and CPU throttling.
4. Keep failed results and repeat with a clean guest start. Record whether the
   host was rebooted too: a guest restart can retain the host's filesystem cache.

See the [diagnostic commands and limitations](../diagnostics/README.md) for
recording and interpreting these measurements.
