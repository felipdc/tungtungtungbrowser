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
