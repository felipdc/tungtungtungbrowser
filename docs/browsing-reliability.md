# Browsing reliability measurements

The development priority is repeatable startup and responsive use of warmed
Safari sessions. Approximately 300 seconds is the current cold-start budget;
it is not a guarantee. Measure foreground readiness, first complete page,
warm request acknowledgement, page execution and visible rendering separately.
An accepted URL request does not establish that the requested page loaded.

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
