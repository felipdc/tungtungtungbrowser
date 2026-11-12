# Multiple phones on one host

Each phone runs as its own Docker Compose project, with an independent writable
VM bundle and cache, a unique guest VSOCK CID, and its own QMP socket, launch
records, clock worker and optional proxy namespace. The runtime binaries and
Docker image can be shared read-only. Use named instances rather than scaling
one Compose service: replicas would otherwise share disks and a CID.

Start with **8 CPU cores, 32 GiB host RAM and storage for two complete VM copies**.
The experimental profile allows each phone four CPU cores and 10 GiB container
memory, including its shared memory. This is a starting configuration, not a
measured maximum phone density. Allow additional storage for retained failures.
The previous single-phone batch peaked at 9.50 GiB; the 3-GiB guest setting is
not the total memory cost of a phone. Current performance remains provisional.

## Prepare independent bundles

Complete the [README setup](../README.md#get-started-from-scratch), including
guest Web Inspector, native touch prerequisites and runtime build. Either create
two separately prepared VM bundles or copy a **cleanly stopped raw bundle**:

```sh
python3 scripts/clone_vm.py /srv/phones/template /srv/phones/alpha
python3 scripts/clone_vm.py /srv/phones/template /srv/phones/beta
```

The copier holds the launcher's bundle/disk leases, creates a new destination,
uses sparse copies (and reflinks where available), and copies the shader cache.
It does not copy old launch records or sockets, create hard links to writable
disks, or regenerate the device identity. Copies initially contain the template's
cookies, native files and device identity; subsequent writes are independent.
Use separately created bundles when the initial device identities must differ.
Raw upstream launchers do not honor this project's leases, so stop them before
copying or launching their bundle. The copier refuses qcow2 chains, forced
shutdown records, shared cache symlinks and an existing destination. Partial
copies remain recorded as failed and cannot be launched as completed copies.

## Register and start

Run these commands on the Linux Docker host. Instance records belong outside
the checkout and should use one registry directory per host:

```sh
export TTT_PHONE_REGISTRY=/srv/phones/registry
phone() {
  python3 scripts/phone_instance.py --registry "$TTT_PHONE_REGISTRY" "$@"
}
phone init alpha --vm /srv/phones/alpha --runtime /srv/vphone-linux --cid 41
phone init beta --vm /srv/phones/beta --runtime /srv/vphone-linux --cid 42

phone compose alpha build
phone compose alpha up -d --no-build
phone compose beta up -d --no-build
phone compose alpha ps
phone compose beta ps
```

`init` records configuration without starting a guest. Duplicate CIDs, nested VM
directories, shared disk inodes and overlapping caches are rejected in the
registry. Compose project names include the phone name and a hash of the registry
path, so separate registries cannot reconfigure each other's projects. The command
clears inherited `TTT_*` and `COMPOSE_*` settings before supplying the selected
instance's paths and CID, so a shell configured for another phone cannot silently
redirect it. Resource settings come from `runtime/api-experimental.env`.

Add `--image YOUR_IMAGE` at registration to select a source-built image. For the
separately prepared [native-FP candidate](native-fp.md), add
`--qemu /runtime/path/to/candidate` or `/vm/path/to/candidate`. This opts into the
experimental native-FP overlay; it does not build or patch QEMU. For routed
egress, add `--proxy /absolute/private/proxy.json`. Each project gets its own
egress container/network namespace, even when both use the same proxy provider.
No host TCP ports are published by these Compose files.

The base Compose file also accepts `TTT_VM_CID` (default 3). All phone projects
share the fixed Docker volume `tungtungtungbrowser-runtime-leases`. The launcher
holds a CID lease before starting QEMU or clock control, plus locks on the VM
bundle and writable disk inodes. Another cooperating launcher with the same CID
fails before guest input. Locks survive into QEMU and are released on process
exit; never delete lease files to bypass a live owner. Direct host launches can
join the same lease directory with `run_vm.py --lease-dir PATH` when that Docker
volume is bind-mounted to a known host location.

On the validated Linux 6.12 host, VSOCK addressing is shared across Docker
network namespaces. Always allocate a unique CID per running guest. Newer
kernels also have explicit namespace modes; see the
[kernel VSOCK namespace documentation](https://www.kernel.org/doc/html/v7.1/admin-guide/sysctl/net.html#proc-sys-net-vsock-vsock-sockets).
Distinct CIDs prevent routing mistakes; this setup is not an access-control
boundary between mutually untrusted controllers on the same host.

## Readiness and native control

Readiness derives its CID from that instance's `/vm/launch.json`. Run one
observer per phone; these commands may run concurrently in separate terminals:

```sh
phone compose alpha exec -T iphone python3 scripts/measure_ready.py /vm \
  --wait-for-guest-ip --qmp-socket /vm/control.sock --output /vm/readiness-001.json
phone compose beta exec -T iphone python3 scripts/measure_ready.py /vm \
  --wait-for-guest-ip --qmp-socket /vm/control.sock --output /vm/readiness-001.json
```

The identical container paths refer to different host directories. Native
control from the host must specify the correct CID:

```sh
python3 -m tungtungtungbrowser --cid 41 screenshot /srv/phones/alpha/native-001.jpg
python3 -m tungtungtungbrowser --cid 42 screenshot /srv/phones/beta/native-001.jpg
```

For QMP capture, use the corresponding host socket
`/srv/phones/alpha/control.sock` or `/srv/phones/beta/control.sock`. Avoid mixing
a native CID with another phone's QMP path. Browser foreground readiness and a
successful screenshot call do not prove that the page has visibly painted.

## Install helpers and use the API

Build the [helper bundle](../README.md#install-the-browser-api-helpers) once,
then install separately into each ready guest, keeping separate records:

```sh
bash scripts/build_browser_helpers.sh /srv/phones/helper-build-001
python3 scripts/install_browser_helpers.py /srv/phones/helper-build-001 \
  --cid 41 --output /srv/phones/alpha-api-001
python3 scripts/install_browser_helpers.py /srv/phones/helper-build-001 \
  --cid 42 --output /srv/phones/beta-api-001
```

Reuse each completed deployment during a warm batch. Existing installations
have the same reuse/reboot rules as the single-phone setup; cloning a guest
with those files does not authorize rerunning a one-shot installer over them.

```python
import asyncio
from pathlib import Path
from tungtungtungbrowser import BrowserProfile


async def work(deployment, fixture_url, capture):
    profile = BrowserProfile(deployment)
    browser = profile.browser()
    async with profile.session(browser=browser, new_tab_url=fixture_url) as page:
        await page.wait_for_selector('#state[data-ready="true"]', timeout=120)
        await page.click('#record-click', timeout=120)
        await page.wait_for_selector('#state[data-clicked="true"]', timeout=120)
        await page.screenshot(Path(capture))
    assert not page.cleanup_errors


async def main():
    # Serve a fixture in each phone's network namespace, as in the README.
    # Use the two actual printed URLs, including their random path components.
    await asyncio.gather(
        work('/srv/phones/alpha-api-001', 'http://10.0.2.2:8767/ALPHA/api-workflow.html',
             '/srv/phones/alpha/click-001.jpg'),
        work('/srv/phones/beta-api-001', 'http://10.0.2.2:8767/BETA/api-workflow.html',
             '/srv/phones/beta/click-001.jpg'),
    )


asyncio.run(main())
```

Each deployment profile binds native control and Inspector transport to one CID.
Mixing a profile, browser or Inspector transport with another declared CID fails
locally before input. `InspectorSession(browser=Browser(cid=41))` also inherits
CID 41 instead of silently inspecting CID 3. Custom transport factories must
accurately declare the CID of the guest they reach. Keep one controlling Browser
per guest; concurrency across guests does not authorize overlapping actions on
one guest.

## Stop one phone

Close its API sessions and stop its bridge using that guest's deployment record
and CID. Then stop just its Compose project:

```sh
python3 scripts/stop_inspector_bridge.py /srv/phones/alpha-api-001/bridge/deploy.json \
  --cid 41 --output /srv/phones/alpha-api-001/bridge-stop.json
phone compose alpha stop
phone compose beta ps
```

The launcher asks the selected CID to shut down natively and records the result
in that bundle's `exit.json`. Check `exit_code == 0` and
`shutdown.forced_termination == false`. Stop beta separately after its own
sessions/bridge close. Do not use broad container/process kill commands or
remove the shared lease volume while another instance is running.

## Validation scope

Protocol and process tests cover duplicate CID rejection, disk aliases, inherited
leases, separate copy writes, partial-copy failure, per-instance environment
selection and mixed-controller rejection. Concurrent native browser qualification
is recorded separately once the two-instance trial completes.
