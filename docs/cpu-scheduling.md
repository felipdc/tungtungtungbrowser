# Guest CPU count and native lifecycle behavior

Increasing the guest CPU count can shorten foreground startup while making
page execution less reliable. Qualify the whole browser sequence before
adopting a CPU setting: foreground readiness, local page execution, native
touch, Home/Safari continuity, rendered captures and external browsing.

## Record host scheduling pressure

On the Linux Docker host, record the entire container and host headroom:

```sh
python3 scripts/sample_resources.py runtime-iphone-1 \
  --seconds 600 --host-memory --cpu-pressure \
  --output "$TTT_EVIDENCE_DIR/startup.jsonl"
python3 scripts/summarize_resources.py \
  "$TTT_EVIDENCE_DIR/startup.jsonl" \
  --output "$TTT_EVIDENCE_DIR/startup-summary.json"
```

Use a new output path for each recording. A separate short recording with
`--interval 1` can cover a lifecycle transition that happens after startup.
Keep its measurement window and result separate from the startup window.

CPU pressure measures runnable work stalled by the Linux host scheduler. It
does not observe scheduling inside iOS, measure guest instruction throughput,
or identify the cause of a native timeout. CPU utilization, quota throttling
and pressure are separate measurements. Host CPU `full` is undefined and is
omitted. Container `some` and `full` overlap; do not add them. Missing files
remain explicitly unavailable. The offline summary rejects counter resets,
missing requested samples and changes in availability. See the
[Linux pressure interface](https://docs.kernel.org/accounting/psi.html).

## Controlled native comparison

On 2026-11-04, two trials used fresh children of the same stopped coherent
disk state and identical copied renderer-cache manifests. They used the
[shared storage graph](storage-isolation.md), the corrected experimental
QEMU `14c9913d` native floating-point path, and TLB mode 0. The host had four
CPU cores; the container limit was four cores and 6656 MiB with no swap.
Guest memory stayed at 2 GiB, the translation cache at 1024 MiB, the display
at 1179×2556 with scale 3, and software rendering at one thread. No hardware
GPU was used. The native control sequence and clock configuration were unchanged.

| Observation | Three application CPUs + SEP | Two application CPUs + SEP |
| --- | ---: | ---: |
| Foreground Safari readiness | 168.24 s | 250.19 s |
| Local canvas / WebGL | Passed | Passed |
| Dedicated worker | 15.15 s timeout | 6.88 s response |
| Shared worker | 15.03 s timeout | 11.15 s response |
| Calibrated touch | Not attempted after failure | Passed |
| Home/Safari continuity | Not attempted after failure | Failed; Safari replaced |
| 600-second sampled cgroup memory peak | 5.94 GiB | 5.63 GiB |
| Average host CPU cores used | 2.93 | 2.24 |
| Container CPU pressure, `some` | 5.91% | 1.50% |
| Container CPU pressure, `full` | 0.87% | 0.49% |

Both startup windows had zero memory-limit events and OOM kills. The
three-CPU run recorded one quota-throttled period totaling 407 microseconds;
the two-CPU run recorded none. The failed page workload, different completed
stages, retained host filesystem cache and one run per configuration prevent
these measurements from establishing a reliable performance distribution or
a successful-browsing resource reduction. A metadata-only filesystem scan
also overlapped the first run's page loading after foreground readiness.

The first trial's HTTP journal recorded complete local responses in under
5 milliseconds. A later worker-related script request arrived after the
timeout result was collected. This locates delayed progress beyond the local
HTTP handler; it does not isolate the guest network or execution component.

In the two-CPU trial, the capture taken 15 seconds after Home still showed
Safari. A process inventory took 36.7 seconds and returned the original PID;
the subsequent launch returned a replacement. A matching-boot report
identified a background `scene-update` watchdog with a 10-second allowance.
The sampled main thread was waiting in `FBSSceneSnapshotRequestHandle` through
a dispatch semaphore. A widget renderer also had a matching-boot scene-update
watchdog. These are sampled stacks, not duration profiles or proof of a cause.

The separate 90-second transition recording averaged 2.07 host CPU cores,
with container CPU pressure of 1.46% `some` and 0.34% `full`. It recorded no
quota throttling, memory-limit events or OOM kills. A later read-only native
probe reported Low Power Mode off and thermal state zero; that observation
does not establish power-state history during the failed transition.

Both guests exited cleanly in about 7.4 seconds. Parent disks, copied cache
and the original baseline remained unchanged. Neither trial proceeded to
external-page acceptance. Three guest application CPUs remain unadopted;
runtime defaults are unchanged. The next measurements should investigate
native scene-snapshot completion and guest scheduling while accounting for
inspection overhead and preserving the original failed operation.

## Separate cold readiness from warm responsiveness

The current development priority accepts cold startup around 300 seconds while
requiring reliable startup and responsive use of an already-running browser.
Measure those separately. A later successful warm page does not replace a failed
cold page result, and an accepted native request does not prove rendered output.

A subsequent trial changed only TLB mode from 0 to 2 relative to the two-CPU
control above. It retained the same immutable parent disks, copied renderer
cache, native controller, guest limits and software-rendered display. Safari
foreground readiness took 282.16 seconds, within the approximate startup budget.
The first local page rendered the expected canvas and passed WebGL checks, but
both workers exceeded their 15-second deadline. That cold sequence remained a
failure and sent no touch or Home input.

After the original 600-second recording finished, a separately labelled warm
sequence used the same guest session and configuration. Its navigation was a
new request with separate evidence; no individual action was retried.

| Warm observation | Result |
| --- | ---: |
| Native navigation acknowledgment | 0.91 s |
| Local diagnostic callback, from navigation request | 22.91 s |
| Dedicated / shared worker response | 6.50 / 6.25 s |
| Calibrated touch request to recorded page event | 5.43 s |
| Native foreground inspection | 37.91 s |
| Process inventory after Home | 61.88 s |
| Safari continuity after Home | Failed; original process absent |

Canvas, WebGL and touch-field checks passed in the warm sequence. The capture
15 seconds after Home still showed Safari with its recorded touch. The later
failure capture showed Home with rendering artifacts. The process inventory
returned no Safari process, so the recorder stopped without requesting a
return launch. External browsing was not attempted in this trial.

A matching-boot crash report identified a 10-second background scene-update
watchdog for the original Safari PID. Its sampled main thread was waiting for
a synchronous XPC reply in `BKSHIDKeyboardService` while UIKit prepared a
scene snapshot. A widget renderer also had a matching-boot scene-update
watchdog. These observations narrow the native paths to investigate; they do
not prove a keyboard-device defect or identify the component responsible for
the wait. Clock maintenance also recorded a transport timeout during the slow
Home/process-inspection interval; its failed result was preserved.

The cold 600-second recording sampled a 6.44 GiB whole-cgroup memory peak and
averaged 2.31 host CPU cores. The separate 300-second warm recording sampled
6.43 GiB and averaged 2.14 cores. Both had zero memory-limit events, OOM kills
and CPU-quota throttling. The warm window includes the failed transition and
the remaining observation time; it is not a successful browsing benchmark.
Container CPU pressure during that window was 1.66% `some` and 0.25% `full`.

Warm-up improved local page execution in this session but did not establish
reliable lifecycle control. Prioritize native lifecycle completion and the
cost of foreground/process inspection before seeking further cold-start
reductions. This single trial does not qualify TLB mode 2, smaller memory
limits or a change to the default runtime pin.
