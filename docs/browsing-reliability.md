# Browsing reliability measurements

The development priority is repeatable startup and responsive use of warmed
Safari sessions. Approximately 300 seconds is the current cold-start budget;
it is not a guarantee. Measure foreground readiness, first complete page,
warm request acknowledgement, page execution and visible rendering separately.
An accepted URL request does not establish that the requested page loaded.

Readiness reports retain the successful guest health response in
`vphoned_health_response` before the recorder sends native input. It includes
the returned guest-instance identity and IP address when supplied by the
daemon. This preserves the existing observation without another request.
An address is not proof that Safari's network process is ready or that a
page can load; retain the subsequent page outcome separately.

If the native launch request returns HTTP 400 with the exact error
`launch refused: com.apple.mobilesafari`, the recorder retains that error and
checks Safari's foreground state within the original launch deadline. Acceptance
requires a fresh, verified Safari identity with a valid process ID, followed by
the usual screenshot. The recorder does not repeat the launch or unlock.
Other launch errors and uncertain transport failures remain terminal.
`safari_launch_error` and the failed `once_only_requests` entry preserve the
rejection even when the foreground postcondition succeeds. This reconciliation
has targeted host-side coverage; a new cold native run of the changed recorder
is still pending. Successful foreground verification does not establish page
readiness or replace visual review.

### Experimental wait for an assigned guest address

`scripts/measure_ready.py --wait-for-guest-ip` delays native input until the
health response contains a usable assigned IPv4 or IPv6 address. It accepts
private/NAT addresses and rejects empty, malformed, unspecified, loopback,
link-local, multicast and broadcast addresses. This is an optional startup
condition; it does not test DNS, the proxy, Internet access or Safari itself.

The pinned daemon selects its health address from the guest's network-interface
inventory and can report `status: ok` with an empty `ip`. See its
[address selection and health implementation](https://github.com/Lakr233/vphone-cli/blob/2.14.2/VPhoneDaemon/Daemon/GuestAPI.swift).
A native initial-URL trial recorded that empty address before input. Its first
page eventually ran, but both workers exceeded their deadlines. That observation
motivates testing the additional condition; it does not establish that the
missing address caused the worker failures or the earlier disconnected page.

For a separate experiment using a newly started VM and a new evidence path:

```sh
python3 /app/scripts/measure_ready.py /vm \
  --qmp-socket /vm/control.sock \
  --deadline-seconds 300 \
  --wait-for-guest-ip \
  --initial-url https://example.org/ \
  --output /vm/readiness-guest-ip.json
```

The wait reuses the already recorded health response when it contains an
address. Otherwise it polls only the read-only health endpoint, with a five-second
request limit and up to five seconds between observations. All waiting uses the
original launch deadline. `guest_ip_observations` retains each response or error;
`guest_ip_ready_response` and `guest_ip_ready_seconds` identify the accepted
sample. The initial `vphoned_health_response` is preserved. A changed daemon
instance, when supplied by the initial response, stops startup before input.
Expiry also stops startup without unlocking, launching or opening a URL.

The option has host-side tests for ordering, absent/invalid addresses, transport
failures, daemon restarts and late replies. Its [first native
trial](#native-validation-of-the-assigned-address-wait) exercised the empty-to-assigned
address transition, but still failed cold-page checks. The default startup
behavior is unchanged, and successful address observation does not set either
page-content verification flag. Compare complete cold-page outcomes before
considering this condition for the default startup path.

## Retain each stage's outcome

The local diagnostic page reports worker, canvas and WebGL results to its
server. Attribute the callback to the exact navigation and check its saved
identifier in the captured image. Keep a failed first page when later pages
succeed. External pages require their own visible-content acceptance; local
diagnostic success does not establish consistent Internet browsing.

Use the configured screenshot backend for the browsing sequence. Run paired
native/QMP screenshot comparisons as separate diagnostics. In a CPU-only
trial, one native screenshot took 43.21 seconds and prevented the next
scheduled capture. That interval cannot establish that the page itself took
43.21 seconds to render. Preserve missed capture slots and record later
observations separately; do not substitute a later image for the missed one.

Optional live logs also have their own outcome. A failed log exchange is a
failed observation, not proof that Safari crashed. Wait for the original
observation to finish before continuing the sequence, retain its failure,
and independently check whether the browser is available for further work.
Do not retry native input to compensate for an observation failure. For
browsing measurements, omit verbose live logging unless it is the variable
being investigated. Record the diagnostic work included in resource windows.

## Recent CPU-only observations

Two sequential guest launches on one Linux host used the same stopped disk
parent, copied shader cache and experimental QEMU binary. The guest had two
application CPUs and 2 GiB RAM; the container had a four-core quota, 6656 MiB
limit and no swap. Rendering used one Lavapipe thread at 1179 × 2556, with
native FP enabled, TLB mode 0 and a 1024 MiB translation cache. The optional
display-refresh experiment remained disabled. There was no hardware GPU.

| Observation | First launch | Second launch |
| --- | --- | --- |
| Foreground Safari from launch | 259.3 s | 235.2 s |
| First local navigation acknowledgement | 13.50 s | 2.23 s |
| First complete local callback | No callback within its 180 s wait | 321.9 s from launch; 66.25 s after navigation |
| First local rendered outcome | Disconnected-network error | Matching saved identifier and canvas visible |
| Subsequent warm callbacks | 21.59 / 12.86 / 20.57 s | Not requested |
| 420 s startup average CPU cores | 2.33 | 2.25 |
| 420 s startup sampled memory peak | 6.40 GiB | 6.13 GiB |

The first launch's three warm navigations passed both workers, canvas and
WebGL checks in the same Safari process. All six 30- and 60-second captures
showed their matching identifiers. URL acknowledgements took 0.78, 0.82 and
1.07 seconds. Saved tabs increased from ten to thirteen, based on sequential
database/WAL copies rather than an atomic snapshot. The 300-second warm window
averaged 2.05 CPU cores and peaked at 6.42 GiB. External browsing remained
inconsistent, and the native screenshot stall stopped that sequence.

During its failed cold navigation, no request reached the local diagnostic
server. Three live system-log windows recorded Safari-requested termination
of a WebKit Networking process and separate graphics-process termination.
Those events establish process lifecycle changes, not their cause. They do
not by themselves identify a renderer defect, device fingerprint leak or
proxy failure.

The second launch's local page passed, but its test controller stopped before
the warm sequence because an optional system-log response was incomplete:
262,035 body bytes arrived and 65,852 more were declared. The first log window
and a separate Safari log window completed. Later native control requests and
clean shutdown succeeded. The original failure remains a transport/collection
failure; it is not a completed three-window log capture or a warm-browsing pass.

Neither launch recorded a memory-limit event or OOM kill in its startup
window. The host cache was not reset, proxy session selection changed between
launches, and diagnostic coverage differed. These results do not establish a
CPU or memory improvement, qualify a smaller host, or justify changing the
default runtime pin. See the [runtime candidate evaluation](native-fp.md) and
[warm browsing observations](browser-control.md#warm-navigation-observations).

## Follow-up with QMP captures

A subsequent run kept the same runtime, guest parent, renderer cache and
resource settings, rebooted the Linux host, and omitted the optional live-log
collectors. Foreground Safari took 273.2 seconds. The first local callback
arrived at 342.1 seconds from launch, but both workers exceeded their original
15-second deadlines. That cold-page failure was retained.

Three later default opens passed the worker, canvas and WebGL checks in the
same Safari process. Acknowledgements took 0.74, 0.65 and 0.81 seconds;
callbacks took 17.94, 10.59 and 12.56 seconds. All six 30- and 60-second images
showed the matching saved identifier and canvas. Saved tabs again grew from
ten to thirteen. The later database copy still named the second warm page as
active, while the images showed the third; these sequential database reads
were not used as proof of the displayed page.

Both ordinary external HTTPS pages showed their requested content at 30 and
60 seconds. Their six QMP captures took 0.038–0.140 seconds each, without a
missed slot. The configured proxy exit matched its preboot observation at the
checked stages. These are sampled rendered outcomes, not exact load times or
a guarantee of future routing and browsing reliability.

The 300-second warm window averaged 2.03 CPU cores and peaked at 6.37 GiB;
the 240-second external-page window averaged 2.10 cores and peaked at 6.29 GiB.
Neither window recorded a memory-limit event or OOM kill. Changing diagnostic
coverage and rebooting the host prevent attributing differences to one cause.
This successful warm sequence still does not qualify the failed cold page,
reduce the supported memory limit, or justify adopting the runtime candidate.

## Repeat after native tab cleanup

A later cold launch used the [saved state produced by native tab
cleanup](native-touch-helper.md#validation), the same complete renderer-cache
seed and the same runtime/resource settings as the QMP follow-up above. The
host was rebooted. Four saved tab records remained before navigation, including
one in the active group, and the native display showed the Start Page. Required
signed observation helpers were verified before boot; this corrected the
deployment omission that had stopped the intervening warm comparison.

Foreground Safari took 236.8 seconds. The first local page passed both worker
deadlines, canvas and WebGL checks; its complete callback arrived at 367.8
seconds from launch, or 101.9 seconds after navigation. Foreground readiness
within the approximate startup budget still differs from a usable first page.
One successful cold page does not establish repeated startup reliability.

The same Safari process completed three warm local navigations:

| Navigation | Request acknowledgement | Complete callback | 30-second image | 60-second image |
| --- | ---: | ---: | --- | --- |
| First | 0.62 s | 17.92 s | Matching completed page | Matching completed page |
| Second | 1.30 s | 15.77 s | Matching completed page | Matching completed page |
| Third | 8.65 s | 30.45 s | Still collecting observations | Matching completed page |

All three callbacks passed workers, canvas and WebGL. The third incomplete
30-second sample remains incomplete; the later image does not replace it.
There was no sampled regression to an earlier page identifier. Saved tab
records grew from four to seven, again using non-atomic database/WAL copies.
The before-copy's active pointer did not identify the already visible cold
page, so database state was not used to establish display acceptance.

Both ordinary external pages again displayed the requested content at 30 and
60 seconds. The 300-second warm window averaged 1.91 CPU cores and peaked at
6.41 GiB, versus 2.03 cores and 6.37 GiB in the earlier workload. These single
observations do not demonstrate a memory reduction or faster warm browsing
from tab cleanup. The 240-second ordinary external-page window averaged
2.08 cores and peaked at 6.35 GiB. Neither of those windows recorded a
memory-limit event or OOM kill.

A separate subsequent browsing window reached the 6.5 GiB limit and recorded
4,689 memory-limit events, with no OOM kill. Container memory-pressure `some`
stall time was approximately 0.054% of that window; reaching the limit does not
by itself explain a page outcome. A collected `atc` segmentation-fault report
matched the current native boot UUID. Safari process continuity and successful
pages therefore do not establish an error-free device. Another newly collected
panic report identified a different boot and was kept separately.

The guest stopped through native shutdown without forced termination. Disk
parents, the original renderer cache and deployed source hashes were verified
unchanged. The corrected helper deployment enabled the intended observations,
but warm latency variation and memory pressure remain unresolved. Keep the
runtime candidate experimental and retain the current resource limits.

## Host allocator trial stopped during startup

A separate trial set `MALLOC_ARENA_MAX=2` only in the QEMU process environment.
The runtime image reported glibc 2.41. The emulator binary, stopped guest parent,
complete renderer-cache seed, CPU/RAM limits and display matched the previous
tab-cleanup repeat. The host was rebooted. A preboot gateway refusal required a
new proxy session before the guest started; both the original failure and the
replacement's successful gateway and route checks were retained.

The single Safari-launch request started 177.7 seconds after guest launch and
timed out waiting for response headers at 297.8 seconds. Its outcome remained
uncertain; no launch was repeated. A later diagnostic image showed Safari's
Start Page. That image does not establish when Safari became foreground or
turn the timed-out operation into a successful readiness check. No cold-page,
warm or external navigation was requested after the failed gate.

The complete 420-second startup window averaged 2.27 CPU cores and peaked at
6.15 GiB, with no memory-limit event or OOM kill. The previous 6.41-GiB startup
window included a completed local diagnostic page; these are different
workloads. The lower peak does not demonstrate a usable memory reduction.
Two newly listed panic-related reports remained unattributed: one captured body
was malformed JSON and the other body was unavailable. Native shutdown completed
without forced termination in 15.9 seconds, and parent disks, cache and deployed
source hashes were verified. The allocator setting remains unadopted.

## Experimental startup with an initial URL

`scripts/measure_ready.py --initial-url URL` selects the existing native Safari
URL-opening path instead of `apps.launch`. The pinned launch implementation
enumerates installed/running applications and checks foreground state; its
response can be much slower than a warm URL-open acknowledgement. That source
observation motivates the alternative, but does not prove the cause of a
particular timeout or establish a faster startup.

For a separate diagnostic run inside the runtime container:

```sh
python3 /app/scripts/measure_ready.py /vm \
  --qmp-socket /vm/control.sock \
  --output /vm/readiness-initial-url.json \
  --initial-url https://example.org/
```

Use a new output name and the launcher's PID namespace, just as for ordinary
readiness measurement. The URL must be absolute HTTP(S), without embedded
credentials or whitespace. Keep private query parameters and captured content
outside the checkout. If a diagnostic page is the initial URL, observe that
original navigation's callback; do not open it a second time to measure the
first request.

This path waits for the existing managed-clock correction, unlocks once and
attempts one URL open in Safari. It then separately checks native foreground
state and captures the display. A failed or timed-out open does not trigger
another open or a fallback application launch. The original launch clock,
per-request timing and transport uncertainty remain in the report. The
`initial_url_ack_seconds` field records the reply time from guest launch; it
is not a page-load timestamp.

Completion still leaves `navigation_verified` and `initial_url_content_verified`
false, with visual review pending. Acknowledgement, Safari foreground identity
and a valid image do not establish that the URL's content loaded. Host tests
cover request preservation, invalid URLs, existing evidence, unlock failures
and foreground timeouts. The first native observation below passed foreground
verification and later warm browsing, but failed its initial page. Repeated
qualification remains necessary. The default startup method is unchanged.

### Experimental bounded foreground reads

Add `--bounded-foreground` to observe `apps.foreground` inside a native daemon
session. This is a guest-native read; it does not evaluate JavaScript in Safari.
The session receives at most 45 seconds, with ten seconds reserved for its reply
within the original startup deadline. The existing foreground identity and
rendering checks still apply.

The pinned daemon's [session evaluator](https://github.com/Lakr233/vphone-cli/blob/83197152137ddd2aad70eb121d6b88a9afbee36e/VPhoneDaemon/Native/vphoned_pty.c)
kills and reaps its child before returning the explicit native deadline error.
Only that complete, exact error permits a new foreground observation. Transport
loss, an invalid reply or a different session error stops the measurement.
Every observation and any confirmed stop remain in the readiness report.
Unlock, URL open and application launch are never repeated by this option.

This option bounds the native observation; it does not remove the foreground
API's application scans or establish faster startup. A warm native probe
returned a verified Safari process in four seconds. Host tests cover confirmed
native cancellation, uncertain transport, invalid identity and the original
deadline. Fresh cold validation remains outstanding, so the option is disabled
by default.

### First native initial-URL observation

The first run used the same stopped tab-cleanup parent, copied renderer cache,
experimental emulator and resource settings as the earlier repeat. The host
was rebooted, and the allocator environment used its original defaults.
Gateway and configured proxy-route checks passed before the guest started.

Native unlock took 32.32 seconds. The single URL-open request started at
183.94 seconds from guest launch and returned at 210.14 seconds, taking
26.20 seconds. Separate foreground verification completed at 223.24 seconds;
the display capture completed at 223.42 seconds. No `apps.launch` call or
second initial URL open was sent.

The first page still failed: no matching diagnostic callback arrived within
180 seconds of the URL acknowledgement, and the retained image showed
Safari's disconnected-network error. The server journal recorded no request
before that deadline; its first request belonged to the later warm sequence.
Foreground readiness within the startup budget did not establish a usable
first page. Host-side proxy preflight also
did not establish Safari network readiness. The original page was not retried.

The separate warm sequence completed three new local navigations in the same
Safari process:

| Navigation | Request acknowledgement | Complete callback | 30- and 60-second images |
| --- | ---: | ---: | --- |
| First | 0.80 s | 16.88 s | Matching completed page |
| Second | 0.79 s | 13.34 s | Matching completed page |
| Third | 1.56 s | 16.74 s | Matching completed page |

All three callbacks passed dedicated/shared workers, canvas and WebGL. All six
images displayed their matching saved identifiers and canvas, without a sampled
return to an earlier page. Saved tab records grew from four to seven, based on
sequential database/WAL copies. Both ordinary external HTTPS pages subsequently
displayed the requested content at 30 and 60 seconds.

The complete 420-second startup window averaged 2.26 CPU cores and peaked at
6.38 GiB. The 300-second warm window averaged 2.00 cores and peaked at 6.36 GiB.
Neither recorded a memory-limit event or OOM kill. Those warm readings are
close to earlier runs, and the initial-page workloads differed. They do not
establish a resource reduction or faster warm browsing caused by the startup
method. A stale allocator assertion in the read-only memory recorder was
corrected before that recorder ran; both source versions were preserved, and
the running emulator configuration was unchanged.

The 240-second ordinary external-page window averaged 2.13 cores and peaked
at 6.32 GiB. A later 240-second browsing window averaged 2.09 cores and sampled
a 6.498-GiB peak; the container's lifetime peak reached its 6.5-GiB limit, with
seven memory-limit events and no OOM kill. These observations still do not
qualify a smaller memory limit.

A collected InputUI process-launch watchdog matched the current native boot
UUID and reported a 30-second allowance. The returned inventory also contained
an accessibility-service report and a panic report from different boots.
Successful Safari process continuity therefore does not establish an error-free
guest, and the InputUI report alone does not explain the failed first page.
Native shutdown completed without forced termination in 7.8 seconds. Parent
disks, renderer cache and all 53 deployed source files were verified unchanged
against their retained records, including the explicit recorder revision.

The health-response recording described above was added after this native run
started. This run therefore cannot establish what IP address the original
health response contained. Preserve that snapshot in future comparisons and
investigate guest/browser network readiness separately from successful host
proxy routing. Keep the initial-URL option experimental.

## Warm comparison with TLB mode 2

A subsequent run changed `QEMU_TLB_UNSYNCED` from `0` to `2` in the same
experimental emulator, with native FP enabled and the required host-FPU
boundary correction. The default barrier limit was retained. The stopped
guest parent, complete renderer-cache seed, allocator settings, CPU/RAM limits
and display were unchanged. The host was rebooted. A fresh proxy session was
selected before boot, so external-page differences cannot be attributed to
the CPU setting alone. The exact binary also passed the original numerical
fixture in both modes; that fixture is not a multicore synchronization test.

Foreground Safari completed at 218.68 seconds. The original health response
had an empty IP address at 154.24 seconds, before the one unlock and URL-open
request. The diagnostic callback arrived at 323.25 seconds from launch, or
159.25 seconds after the navigation began. Both workers exceeded their original
15-second deadlines. Canvas and WebGL passed, but the subsequent cold capture
still displayed "Collecting observations". The cold page remains a failure.
The optional assigned-address wait above was not enabled in this run.

All three later warm navigations passed workers, canvas and WebGL in the same
Safari process. All six 30- and 60-second images matched their saved callback
identifiers and canvas. The previous mode 0 run and this mode 2 run used the
same warm workload:

| Navigation | Mode 0 acknowledgement | Mode 2 acknowledgement | Mode 0 callback | Mode 2 callback |
| --- | ---: | ---: | ---: | ---: |
| First | 0.80 s | 0.86 s | 16.88 s | 15.58 s |
| Second | 0.79 s | 0.70 s | 13.34 s | 11.95 s |
| Third | 1.56 s | 0.75 s | 16.74 s | 22.59 s |

The 300-second warm window averaged 2.03 CPU cores and peaked at 6.36 GiB,
versus 2.00 cores and 6.36 GiB with mode 0. Neither warm window had a
memory-limit event or OOM kill. These single samples show no convincing
warm-page speed or resource improvement. Request acknowledgement is still much
faster than completion of page work.

External acceptance failed: the first ordinary HTTPS page displayed its
requested content at 30 and 60 seconds, while the second was blank at both
checkpoints. Native requests were acknowledged and Safari process continuity
passed, which did not establish successful page rendering. The configured
proxy route passed before and after the sequence with the same observed exit
within this run. That does not isolate the cause of the blank page. The
240-second external window averaged 1.83 cores and peaked at 6.28 GiB, but
its incomplete workload cannot establish an improvement over successful browsing.

The external review was not submitted within its 600-second orchestration
deadline while separate source work continued. The controller stopped the
sequence, and downstream acceptance was not attempted. Later review retained
the blank-page failure rather than resuming the expired gate. Future live runs
must finish their review gates before separate implementation work.

Native shutdown completed in 8.5 seconds without forced termination. The
returned new/changed crash reports belonged to other boots; none of those
collected reports matched the current native boot. This limited inventory
does not prove a crash-free guest. Parent disks, renderer cache, original
baseline files and all 54 deployed source files were verified unchanged.
The cold and external failures, variable warm latency and unchanged warm
memory usage leave mode 2 experimental. Runtime pins and resource limits
remain unchanged.

## Native validation of the assigned-address wait

The first native run of `--wait-for-guest-ip` used a 300-second readiness
deadline, mode 0 of the same experimental emulator, native FP with the boundary
correction, the original allocator and the same stopped tab-cleanup parent and
complete renderer-cache seed. Guest and container resource settings were
unchanged. It reused the preceding trial's proxy session, which differed from
the earlier mode 0 control. The host was rebooted. No running configuration
was changed during measurement.

The initial healthy response still had an empty address at 171.4 seconds.
One read-only poll reported an assigned address at 176.4 seconds with the same
daemon instance. The wait took 5.01 seconds and sent no input before that
observation. Unlock then took 15.90 seconds; the single URL-open request took
22.49 seconds. Foreground Safari completed at 226.28 seconds, within the
configured budget. The original empty-address sample and request timings
were retained.

The first page still failed. Its callback arrived at 330.64 seconds from
launch, or 138.29 seconds after navigation began. The dedicated worker replied
in 6.65 seconds, but the shared worker exceeded its 15-second deadline. Canvas
and WebGL passed. The subsequent capture displayed the canvas while the page
header, observation text and saved identifier were missing. This run verifies
that the address condition works; it does not demonstrate reliable cold-page
readiness or isolate the cause of the rendering and worker failures.

Three later navigations completed in the same Safari process:

| Navigation | Acknowledgement | Complete callback | 30-second image | 60-second image |
| --- | ---: | ---: | --- | --- |
| First | 0.79 s | 15.46 s | Matching completed page | Matching completed page |
| Second | 0.63 s | 14.78 s | Matching completed page | Matching completed page |
| Third | 3.24 s | 28.96 s | Still collecting observations | Matching completed page |

All three callbacks passed workers, canvas and WebGL. The third incomplete
image remains incomplete despite the earlier callback. The post-sequence
`apps.foreground` observation took 55.16 seconds and returned successfully;
that read is separate from navigation acknowledgement and page execution.
The ordinary default URL-open path did not make an extra foreground query for
each navigation. Saved tab records grew from four to seven, using sequential
database/WAL copies rather than an atomic display-state snapshot.

Both ordinary external HTTPS pages displayed their requested content at 30
and 60 seconds. Both manual review gates were completed before their deadlines.
Proxy-route checks passed before and after the external sequence, retaining
the same observed exit within this run. These successful samples do not
qualify the failed cold page or guarantee future external-page behavior.

The complete startup window averaged 2.36 CPU cores and peaked at 6.37 GiB.
The 300-second warm window averaged 1.97 cores and peaked at 6.36 GiB; the
240-second ordinary external-page window averaged 2.02 cores and peaked at
6.29 GiB. A separate later browsing window averaged 2.08 cores and peaked
at 6.48 GiB. None of these windows recorded a memory-limit event or OOM kill.
These measurements do not establish a meaningful resource reduction or a
smaller supported host.

Native shutdown took 15.9 seconds without forced termination. Parent disks,
renderer cache, original baseline files and all 55 deployed source files were
verified unchanged. None of the collected new/changed crash reports matched
the current native boot; that limited inventory does not prove a crash-free
guest. Keep the address wait optional and investigate slow native foreground
observations, incomplete rendering and cold worker behavior separately.
