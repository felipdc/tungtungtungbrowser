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
The two-phone trial reached 19.38 GiB combined sampled container memory, and one
phone reached its 10-GiB limit. The 3-GiB guest setting is not the total memory
cost of a phone. Current performance remains provisional.

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

For measured resource experiments, record overrides when registering each phone:

```sh
phone init gamma --vm /srv/phones/gamma --runtime /srv/vphone-linux --cid 43 \
  --guest-memory-gib 2 --guest-cpus 2 --container-memory-mib 6144 \
  --container-cpus 4 --tcg-cache-mib 1024 --render-threads 1
```

These values are stored in the instance record and override the default profile
and inherited shell variables. `--shm-mib` also controls shared-memory capacity;
used shared memory remains charged to the container. This example requests a
6-GiB container limit, not a validated density profile. Inspect `phone show gamma`
and `phone compose gamma config` before starting. Existing registrations retain
their settings; use a fresh copied bundle and registration for comparisons.

Add `--image YOUR_IMAGE` at registration to select a source-built image. For the
separately prepared [native-FP candidate](native-fp.md), add
`--qemu /runtime/path/to/candidate` or `/vm/path/to/candidate`. This opts into the
experimental native-FP overlay; it does not build or patch QEMU. For routed
egress, add `--proxy /absolute/private/proxy.json`. Each project gets its own
egress container/network namespace, even when both use the same proxy provider.
No host TCP ports are published by these Compose files.

The base Compose file also accepts `TTT_VM_CID` (default 3). All phone projects
share the external Docker volume `tungtungtungbrowser-runtime-leases`. The instance
command creates it idempotently before `up` or `run`; direct Compose users must
first run `docker volume create tungtungtungbrowser-runtime-leases`. A project's
`down -v` leaves this external volume intact. The launcher
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

## Measure each phone

Run the recorder on the Docker host for each phone and its proxy container:

```sh
python3 scripts/sample_resources.py CONTAINER_NAME --seconds 600 --interval 1 \
  --host-memory --cpu-pressure --output /srv/phones/resources-001.jsonl
python3 scripts/summarize_resources.py /srv/phones/resources-001.jsonl
```

`memory_bytes` measures the whole container cgroup, including renderer processes
and shared memory. Each raw sample also records `swap_memory.logical_bytes`,
`swap_memory.compressed_bytes` and swap events. These distinguish logical swap
usage from the RAM occupied by compressed pages. Compressed memory is already
charged to the cgroup; do not add it to `memory_bytes`. Missing kernel interfaces
produce `null`, not zero. The complete `memory_stat` record retains available
compression activity counters, including `zswpwb`.

The recorder also checks `memory.zswap.max` and `memory.zswap.writeback` for changes
during measurement. It does not configure compression or establish that disk
writeback is disabled: that also depends on the host's zswap state. Consult the
[kernel zswap documentation](https://www.kernel.org/doc/html/v6.12/admin-guide/mm/zswap.html)
before a compression experiment. The default phone profile still disables
additional swap; a smaller memory limit requires its own browser validation.

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
selection and mixed-controller rejection.

A two-phone trial on 2026-11-12 used one Debian 13 x86_64 host with eight CPU
cores and 32 GiB RAM, Linux 6.12, Mesa 26.1.6, independent raw bundles and ordinary
Docker networks. Both used the experimental resource profile, native-FP candidate
and renderer census. The image was built from `4a47305`; host orchestration used
`3dccac7`, whose package and launcher sources matched the running containers.

| Observation | Phone A | Phone B |
| --- | ---: | ---: |
| Cold launch to verified Safari foreground | 315.7 s | 268.6 s |
| Concurrent owned-session creation | 43.1 s | 20.2 s |
| Native external-navigation request | 52.3 s | 53.7 s |

Both browsers exercised CSS and XPath clicks, per-phone JSON responses, DOM
conditions, independent local storage and external HTTPS navigation concurrently.
Native JPEG and host PNG captures visibly showed each phone's own fixture and
the external page. Namespace, disk-inode and CID checks passed; a duplicate-CID
launch was rejected before QEMU started, without disrupting either guest.

The external-page test initially assumed an `h1` that the returned mobile HTML
did not contain. Those timeout records were retained. Read-only follow-up on the
same tab identities verified the exact URLs, actual HTML and visible content;
navigation was not repeated. Prefer a controlled fixture with explicit DOM
conditions for repeatable tests instead of assumptions about third-party markup.

Ownership-checked cleanup closed one test tab per guest, with one observed
Command-W sequence each. Two database copies at the settled checkpoint retained
every original tab, and independent audits found accessibility settings restored
and no owned focus helper left running.

Stopping A took 17.0 seconds and left B's native daemon and Inspector bridge
identities unchanged. A's CID lease was released while B's stayed held. Without
restarting B, a further session opened in 17.3 seconds, retained B's local-storage
marker, performed both selector clicks and navigated to a second fixture document
in 30.8 seconds. Its first content assertion raced the navigation because the
same DOM predicate matched the old document. A read-only follow-up waited for the
destination URL and verified the new document and paired captures. This failure
was retained too; the navigation request was not repeated.

The surviving session's owned tab was then closed once, with its original tabs
preserved. Both guests shut down cleanly without forced termination; the source
VM disks and cache remained unchanged. Neither console recorded
`RasterOrderGroups`, `exec-fault` or `Restarting GPU` during this trial.

One-second cgroup sampling recorded a maximum combined memory usage of 19.38 GiB
while both guests were running. Per-container peaks over the complete batch were
10.00 GiB and 9.90 GiB. There were no OOM events, but A recorded 501 `memory.max`
events at its limit. Allow headroom and measure your own workloads; 10 GiB is not
a comfortable per-phone memory guarantee.

This qualifies two concurrent browsers for the described targeted checks. It
does not qualify a maximum density, sustained load, concurrent proxy egress,
mutually untrusted tenants or renderer-census-disabled operation. Keep guests
warm during a batch and validate each change with a targeted check; repeat a
full browser workflow at milestones.

### Latest four-phone product result

A later run on sixteen physical cores and 32 GiB RAM returned **four visible
Shopee product pages with matching intercepted product JSON** in one concurrent
round. Each phone retained the configured 6 GiB container limit, 3 GiB guest
RAM, three guest CPUs, one rendering worker and a 1024 MiB translation cache.
The compressed-memory settings described below were unchanged. Native and host
screenshots confirmed each product; all four responses had the expected item
and shop IDs, HTTP status 200 and no application error. This exceeds the accepted
threshold of three products and at most one login for that measured round.

The replacement cohort ran together for 42.28 minutes. Sampled RAM peaked at
24.00 GiB for the phones and 24.15 GiB including proxies; minimum host available
memory was 5.04 GiB. There were no host or phone OOM events and no disk swap I/O.
The largest sampled per-phone accounting overshoot above the configured 6 GiB
limit was 52 KiB; the kernel peak counter recorded up to 648 KiB above it.
This interval includes startup, recovery and idle time. It is not a measurement
of continuous successful browsing.

Cold startup remains unqualified. Three original phones passed readiness and
their first native fixture. The fourth stopped on an uncertain foreground-read
transport timeout and was retired. Its fresh replacement received a confirmed
native observation timeout, then exhausted its original readiness deadline
while Safari remained in the background. A separately reviewed native tap on
Safari opened the already-persisted requested page. The replacement subsequently
passed its warm fixture and the four-phone product round; its original cold
failure remains a failure. No failed input was automatically replayed.

Separate default-close checks passed on all four working phones: two visibly
rendered local documents, one observed Command-W and every earlier tab preserved
through sixty seconds. Final native audits also passed. These checks do not
explain the earlier intermittent tab loss. All guests shut down natively;
source disks and caches were unchanged and host swap settings were restored.
One accepted concurrent product round
does not establish sustained reliability or a general Shopee success rate. Keep
the compressed-memory profile experimental while cold startup and repeated
concurrent browsing remain unresolved.

### Application-launch comparison and address discovery

A subsequent four-phone comparison kept the same resource settings and opened
Safari with one native application-launch request before creating an owned
blank tab. Three phones completed readiness in 202–261 seconds. The fourth
stopped at the launch call's 120-second transport timeout; a later read-only
capture showed Safari, which does not repair that original failure.

All three first blank-tab navigations then failed address discovery with native
AX error `-25216`, before any URL input. A later read-only probe found the
address control. Separate reviewed probes showed that Command-L revealed the
bar without verified editing focus, while one tap established the URL first
responder. One phone subsequently rendered both local fixture documents in
native and host captures and retained its tabs through sixty seconds. Its
first warm follow-up also contained a query/fragment harness mistake; that
failed assertion remains separate from the corrected navigation. No measured
Shopee product round was performed in this comparison.

Final audits preserved every original tab and each intentionally retained
blank tab, restored accessibility settings and found no focus helpers.
Two new opt-in candidates now cover [bounded application launch](browsing-reliability.md#experimental-bounded-application-launch)
and a [read-only discovery wait](native-focus.md#optional-address-discovery-wait).
All 702 host tests passed; the live comparison used the preceding build.
At that point, fresh cold validation of both candidates was still required.

The four containers coexisted for 25.70 minutes with no OOMs or disk swap I/O.
Sampled RAM peaked at 24.00 GiB for phones and 24.18 GiB including proxies;
host available memory stayed above 5.27 GiB. Limits remained configured at
6 GiB per phone; the largest accounting overshoots were 56 KiB in samples and
1,224 KiB in kernel peak counters. This interval includes failures and idle
time. All guests shut down natively in 8.63–17.89 seconds; source disks and
caches were unchanged and host swap settings were restored.

Final anonymous executable mappings retained 974–994 MiB of proportional
resident memory per phone. Graphics mapping sizes grew by 110–594 MiB on the
three phones with startup snapshots, even where their resident memory fell.
These observations do not demonstrate reduced allocation retention or qualify
the 4 GiB stretch target.

### Bounded-launch trial and targeted LaunchServices follow-up

The next four-phone trial enabled the bounded launch and a 30-second native
address-discovery wait, with all resource settings unchanged. Every launch
returned the exact 90-second native stop response. One phone subsequently
verified Safari at 441.10 seconds within its original deadline; the other three
exhausted their original startup budgets while remaining on the Home Screen.

The passing phone rendered both local documents in native and host captures,
reattached and preserved its retained tab through sixty seconds. Its first
address scan was unavailable and the next completed scan found no address
field; the existing guarded focus path then completed navigation. A separate
default-close check sent exactly one close, removed only its owned tab and
preserved all five baseline tabs through sixty seconds. These results do not
isolate a causal benefit from the optional discovery wait.

A separately reviewed warm diagnostic on one failed-start phone made a
targeted native LaunchServices request. It was accepted in 3.88 seconds,
followed by verified Safari foreground state and visible browser chrome. The
page was still loading and no product JSON was measured. The original cold
failure remains a failure. The new opt-in
[`--launch-services` candidate](browsing-reliability.md#experimental-targeted-launchservices-launch)
avoids broad app scans before requesting launch. All 709 host tests passed;
fresh four-phone cold validation of this candidate remains outstanding.

The four containers coexisted for 20.63 minutes with no OOMs or disk swap I/O.
Sampled RAM peaked at 24.00 GiB for phones and 24.18 GiB including proxies;
minimum host available memory was 5.38 GiB. Configured limits remained 6 GiB
per phone. Maximum accounting overshoots were 24 KiB in samples and 496 KiB
in kernel peak counters. This interval includes failed startups, diagnostics,
single-phone local browsing and idle time, not sustained concurrent browsing.
All final native audits passed and all guests shut down cleanly in
17.29–18.05 seconds. Source disks and caches were unchanged; host swap settings
were restored.

Final anonymous executable PSS remained 919–979 MiB per QEMU. On the phone
with the warm launch diagnostic, graphics mappings grew by 320 MiB while
graphics PSS fell by about 198 MiB and compressed memory rose. The passing
phone's graphics mapping size was nearly unchanged despite lower PSS. These
observations do not demonstrate freed graphics allocations or qualify 4 GiB.

### Compressed-memory trial: four-phone startup remains unqualified

A subsequent experiment retained 3 GiB guest RAM and used a 6 GiB cgroup RAM
limit, 4 GiB of logical swap capacity and a 1.5 GiB zswap limit per phone.
Host zswap used zstd, and per-phone disk writeback was disabled before QEMU
started. Compressed memory remained part of the cgroup charge.

One phone reached foreground Safari in 246 seconds and passed CSS/XPath clicks,
native navigation, application JSON capture and settled visible rendering.
A post-close forensic download was truncated. Two later bounded database reads
confirmed the owned tab was absent and original tabs were preserved, without
another close input. The original failed audit was retained; this was not an
uninterrupted full-workflow pass.

With that phone warm, three additional phones booted concurrently on an
eight-core, 32 GiB host. Sampled RAM peaked at **24.00 GiB for the four phones**
and **24.07 GiB including their proxies**; minimum host available memory was
5.67 GiB. No phone recorded an OOM, and no disk swap I/O occurred. However, all
three new phones failed their original 600-second Safari readiness checks.
Each phone used about 1.9 CPU cores on average, including the warm phone, while
memory stall time stayed below 0.34%. CPU contention is therefore the next
hypothesis to test on **16 CPU cores with the same 32 GiB RAM**.

All four guests subsequently shut down natively; parent disks and cache stayed
unchanged. This experiment demonstrates memory containment, not four working
browsers. The compressed profile remains unqualified until startup, native
focus, rendering, API capture and cleanup pass under sustained concurrent use.

### Sixteen-core compressed-memory follow-up

A subsequent trial on a sixteen-core host with the same 32 GiB RAM and
compressed-memory limits brought all four phones to verified foreground Safari
in 278.5, 279.7, 297.3 and 291.3 seconds. Each retained 3 GiB guest RAM, two
guest CPUs, a four-core host CPU quota and a 1 GiB translation cache. Native
resolution, one lavapipe worker and the interlock-capable Mesa driver were
unchanged. Independent writable child disks shared a stopped, immutable parent.

The first recorded hour peaked at **24.00 GiB for the phone cgroups** and
**24.23 GiB including proxies**, with at least **5.32 GiB host available memory**.
No OOM events or disk swap I/O occurred. Full memory stall time remained below
0.32% per phone. The configured limit was exactly 6 GiB; a few kernel samples
exceeded it by at most 36 KiB, so this is not a claim that every raw observation
was mathematically below that boundary. Across the complete 162-minute record,
the same combined peaks held and minimum available host memory was 5.24 GiB.
No host or phone OOM events and no disk swap I/O occurred. Full memory stalls
were below 0.14% per phone. The largest sampled per-phone overshoot was 904 KiB;
the kernel peak counter recorded up to 1.77 MiB above the configured limit.

The original cold setup sequence still failed: one forensic read observed a
changing database file size, and three native touch-helper checks timed out
after verified installation. Reconciliation retained those failures, verified
the installed bytes and reran input-free checks before continuing warm. All
four subsequently passed concurrent local native navigation and cleanup.
The first external batch returned valid application product JSON and visible
product content on three phones; the fourth reached a login requirement. Login
outcomes are accepted separately from valid product data.

A later external batch exposed a real preservation failure: one close removed
its owned tab and a pre-existing test tab. Two database copies confirmed the
loss, while the native observer recorded only one address chord and one close
chord. This does not establish a key-repeat cause. Other login and product-to-
login outcomes were retained and their owned tabs were closed separately after
verifying identity.

The new native blank-tab path independently verifies both WebKit's empty Start
Page document and the native address placeholder. A four-phone warm follow-up
completed creation, two destination-verified navigations, readable native and
host captures, and one observed close per phone, preserving every pre-existing tab through the settled database audit.
This targeted success does not explain the earlier bootstrap-tab loss or
qualify an uninterrupted cold setup and external workflow with the new path.

All four guests later shut down natively, without forced termination, after
roughly 162 minutes of uptime. Parent disks and cache remained unchanged, and
the host's original swap/compression configuration was restored. Compressed
pages reduce resident pressure; this trial does not establish a reduction in
emulator or graphics allocation retention. Keep the density profile experimental
until the remaining startup and cleanup failures are resolved. The 4 GiB
stretch target remains unqualified.

### Smaller translation-cache experiment

A later four-phone comparison kept the sixteen-core, 32-GiB host and the same
6-GiB RAM, compressed-memory and native display settings. Two phones used a
1024-MiB translation cache and two used the explicit 768-MiB option. The smaller
cache reduced QEMU's anonymous executable PSS at each observed phase:

| Observation | 1024-MiB cache, mean of two phones | 768-MiB cache, mean of two phones |
| --- | ---: | ---: |
| Foreground Safari ready | 992.8 MiB | 747.7 MiB |
| First external case cleaned up | 980.6 MiB | 739.6 MiB |
| Final settled state | 987.0 MiB | 746.1 MiB |

QEMU reported translation-buffer capacities of 1,073,618,944 and 805,183,488
bytes respectively. This demonstrates approximately 241 MiB less executable
residency per phone after browsing. It does not establish a reduction in graphics
allocation retention or the whole-container peak: all four retained the same
6-GiB RAM limit. The smaller caches flushed more often. Controls started about
seven minutes earlier, and their recovery histories differed, so this comparison
does not isolate a startup or throughput effect.

The four phones ran together for 45.6 minutes. Sampled combined RAM peaked at
24.00 GiB for phones and 24.15 GiB including proxies; minimum host available
memory was 5.29 GiB. There were no host or phone OOM events and no disk swap I/O.
Full memory stalls were below 0.30% per phone. The largest individual sampled
overshoot above the configured limit was 472 KiB; the kernel peak counter
recorded up to 564 KiB above it.

This batch did not qualify cold browsing or the external product target. The
original cold sequence retained helper-check and focus-read failures. Separate
warm recovery reached all four external pages and intercepted their API
responses, but every response required login. Allowing one login outcome does
not turn that result into a three-of-four product pass. A subsequent warm native
navigation test closed an inner test tab while preserving a second test tab on
all four phones, then closed that second tab and preserved the original session.
The test used one Inspector connection at a time; an earlier harness attempt
with overlapping connections timed out before sending its inner-tab input.

Native page bodies and host captures were readable. Some immediate native
captures retained the previously observed overlapping translation hint in the
Safari toolbar. All four VMs shut down cleanly, with unchanged parent disks and
cache and restored host swap settings. Keep the smaller-cache density profile
experimental until a fresh cold batch also passes the product, focus, rendering
and cleanup requirements.

A fresh batch with all four caches set to 768 MiB also failed cold qualification.
Three phones reached foreground Safari in 297.1–357.2 seconds, then encountered
two native address-field failures and one session-creation transport timeout.
The fourth phone's guest daemon never became reachable within the original
600-second readiness deadline. No product navigation ran. During 14 minutes
of concurrent residency, no OOM or disk swap I/O occurred. Three phones shut
down natively; the unreachable phone required the existing forced-stop fallback
after its 90-second grace period. Parent disks and cache stayed unchanged, and
host swap settings were restored. These observations reject that batch; they
do not isolate the smaller cache as the cause of its failures.
