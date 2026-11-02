# Linux setup

Use a Linux x86_64 host with Docker Compose, Python 3.11 or newer, a C toolchain,
matching kernel headers, `/dev/vhost-vsock`, and Mesa's lavapipe Vulkan driver.
Debian 13 is the validated baseline. Cloud provider selection is independent
of the browser runtime; the required kernel features must be available locally.

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

After creation, cleanly stop the upstream VM before starting the Compose
runtime. Confirm no emulator still owns `Disk.img`. Keep VM bundles, proxy
credentials, deployment configuration and captured page content in external
directories. Do not put them into a source archive or container image.

The current Dockerfiles build for x86_64. An ARM port must update the runtime
build and architecture-specific downloads and pass native guest verification;
a different host CPU alone does not establish acceleration for Apple-specific
CPU instructions.
