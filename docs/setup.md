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
the source file and verifies the changed code page's signature. The separate
[native installation guide](native-touch-installation.md) covers firmware
prerequisites, full backups, reversal and post-reboot verification. VM creation
does not install this optional, build-specific repair automatically.

After creation, cleanly stop the upstream VM before starting the Compose
runtime. Confirm no emulator still owns `Disk.img`. Keep VM bundles, proxy
credentials, deployment configuration and captured page content in external
directories. Do not put them into a source archive or container image.

Earlier runtime and proxy images built natively for ARM64 and passed controller
import and proxy binary execution checks. The Mesa driver update below is
qualified on x86_64 only; the complete ARM runtime still needs native guest
verification. The source build, firmware creation and kernel-selection helpers remain validated only
on x86_64. A different host CPU alone does not establish acceleration for
Apple-specific CPU instructions.

The optional [ARM64 memory-access check](../native/README.md#arm64-linux-memory-access-check)
can test some native instruction requirements before a runtime build. Passing
it does not establish ARM runtime support or guest compatibility.

## Check the built executable inside the runtime container

A QEMU binary can run on its build host and still fail to load in the runtime
container. Before booting a newly built binary, use the same image, mounts and
Compose overlays as the intended runtime to check its dynamic loader:

```sh
docker compose -f runtime/compose.yaml run --rm --no-deps \
  --entrypoint /runtime/qemu/build/qemu-system-aarch64 iphone --version
```

Include any additional Compose files used by your deployment. If an external
overlay selects a different executable through `QEMU`, use that executable's
container path as the entry point instead. A custom wrapper must support
`--version`, or the check must target its underlying QEMU binary directly.
Require exit status zero and the expected QEMU version. This invocation does
not start a guest or open its disks; it does not validate firmware, rendering,
the proxy or Safari readiness. The filesystem check below is a separate check.

Keep the output and binary hash with the build record. If the loader reports a
missing library, resolve the build/container dependency mismatch before starting
the guest. For example, one experimental build auto-enabled FUSE after its
development package became available on the host. Its configure arguments
matched the baseline, but its executable required `libfuse3.so.4`, absent from
the runtime image, and exited with status 127 before QEMU initialization. The
baseline had FUSE disabled. Explicitly disabling that optional feature in the
candidate's configure invocation restored the intended dependency set.

For controlled rebuilds, preserve the baseline executable and source diff in
an external directory. Retain the configure invocation, generated
`config-host.h`, `meson-info/intro-buildoptions.json` and shared-library
dependencies from the build directory. Compare the generated features and
dependencies as well as the arguments: an `auto` feature can resolve differently
after host packages change. Reconfigure through the pinned QEMU configure
wrapper with the original arguments and explicit feature changes; a direct
Meson reconfiguration in this fork also changed the effective warning policy
in one observed build. Check the final build settings again before measuring.

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

## Lavapipe capabilities and renderer logs

The runtime image pins Mesa Vulkan drivers to `26.1.6-1~bpo13+1` from Debian's
signed `trixie-backports` repository. The previous Mesa 25.0.7 CPU driver lacked
`fragmentShaderPixelInterlock`: native raster-order pipeline creation failed
with `RasterOrderGroups` / `Feature(FragmentShaderPixelInterlock)` errors.
The pinned driver exposes pixel and sample interlock while retaining CPU
rendering. Driver capability checks alone do not establish usable native pages.

Rebuild the image after updating the Dockerfile. If your Compose overlays bind
mount `libvulkan_lvp.so`, that mount overrides the image's driver. Check the
actual mounted library and its dependencies before booting; updating the image
alone leaves an older mounted driver active. Preserve the old image and library
when comparing a new driver on a separate guest copy.

The image sets `REIMS_VGPU_LOG=warn`, so renderer failures appear in the VM's
`console.log`. Set this variable explicitly when launching outside the image.
The pinned renderer installs no logger when it is unset. Logging does not
enable census mode or change how failures are handled; `REIMS_VGPU_CENSUS_DIR`
remains empty by default. Keep native screenshots, page/API results and renderer
logs together when diagnosing a blank page: a GPU restart message alone does
not identify its cause.

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
| `TTT_CACHE_DIR` | `$TTT_VM_DIR/cache` | Host directory mounted at `/root/.cache`; use a separate copy for resource comparisons |

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

Add `--cpu-pressure` when investigating CPU contention. This records cumulative
CPU pressure for the container and host alongside CPU use and quota throttling;
the offline summary reports stall percentages over the observed interval.
Missing pressure files remain unavailable, and counter resets or changing
availability invalidate the summary. Older recordings remain supported.

CPU pressure describes runnable work waiting for the Linux host scheduler. It
does not measure scheduling inside iOS or establish why a native watchdog
expired. Host CPU `full` is undefined and omitted; container `some` and `full`
overlap and must not be added. A low CPU-use average or zero quota throttling
alone does not establish absence of scheduler contention. See the
[Linux pressure interface](https://docs.kernel.org/accounting/psi.html).

For comparisons that change emulator settings, use the
[translation-cache and cache-isolation procedure](translation-cache.md).
Changing environment variables for individual graphics libraries does not
necessarily redirect every renderer cache.
