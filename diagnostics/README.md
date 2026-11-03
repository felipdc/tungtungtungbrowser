# Browser observation page

This is a diagnostic page loaded normally through Safari's native open-URL
operation. Its JavaScript reads browser properties and draws on its own canvas;
it does not inject scripts into other pages, redefine properties or replace APIs.
The browser controller continues to reject page evaluation and script URLs.

Run the server **inside the iPhone container**, sharing QEMU's network namespace:

```sh
python3 scripts/serve_probe.py --output /vm/browser-observations
```

It listens only on loopback and prints a random-path URL for QEMU's guest
gateway, `10.0.2.2`. Open that URL through the native browser controller.
Each submission creates a separate directory containing browser observations,
the rendered canvas PNG and the HTTP request headers received by the server.
Keep the service out of baseline resource measurements and stop it when finished.

To distinguish local HTTP delivery from later browser work, enable a bounded
request journal when starting the server:

```sh
python3 scripts/serve_probe.py --output /vm/browser-observations \
  --request-log /vm/diagnostics/probe-requests-001.jsonl \
  --max-request-records 1000
```

The journal creates a new file with mode `0600` and preserves existing files.
It records handled GET/POST responses with a route label, method, status,
planned response-body size, UTC request time, host monotonic start/end times,
and whether writing the response completed. Route labels contain only the
known asset name, `collect`, `input`, or `unrecognized`; URLs, random route
tokens, query strings, headers, peer addresses and submitted bodies are omitted.
The separate observation directories still contain the diagnostic data and
headers described above, so keep all artifacts outside the source repository.

Records are appended as response handlers finish; concurrent requests can be
recorded in a different order than they arrived. The default limit is
1,000 response records, configurable from 1 to 100,000; one `limit_reached`
record is added if another response arrives. Further requests are served
without growing the journal. Logging is disabled when `--request-log` is absent.
The monotonic clock can be compared with the emulator's launch clock on the
same host and boot. A completed socket write proves only server-side delivery
progress: inspect the observation callback and screenshot to verify browser
execution and rendering. This is local test-server instrumentation, not a
browser network-interception API or an external-site navigation measurement.

The page compares main-frame, dedicated-worker and shared-worker navigator
values, plus display geometry, WebGL identity and a simple canvas sample.
Missing or failed observations are recorded. The page has been exercised in
native Safari. Server transport checks alone are not Safari evidence. Its touch
button records the pointer, touch and mouse event sequence, including trust,
contact dimensions and pressure, without changing browser APIs.

Each worker observation includes a `measurement` with its outcome, elapsed
milliseconds and configured timeout. The default remains 15,000 milliseconds.
For a separate slow-start diagnostic, append `?worker_timeout_ms=60000` to the
printed page URL. Accepted values are integers from 1,000 to 120,000; an invalid
value records a configuration error without creating workers. Timing starts
before constructing the worker and includes script loading and its response.
It uses the page's `performance.now()` clock, which cannot be aligned directly
with host monotonic timestamps. A busy page can run a timeout callback late.
The probe terminates its dedicated worker or closes its shared-worker port
after the first response, error or timeout; closing a port does not establish
that the shared-worker process exited.

Keep extended-timeout runs separate from the default measurement. A response
with a longer allowance does not erase an earlier timeout or prove that the
worker met the original deadline. A saved canvas image also does not prove
that it remained visible in the final screenshot.

In the WebGL observation, `available` means `getContext` returned an object.
The separate `contextLost` and numeric `error` values describe its state after
the capability reads and before the probe releases its own context. A lost
context can still return an object while capability queries return `null`.
Do not treat object creation, a worker response or a saved callback as proof
that the page rendered. Review the screenshot and canvas alongside these values.

The `touchConstructor` observation constructs a `Touch` with distinct field
values and reads them back without dispatching an event. This separates DOM
field conversion from the native input path. Constructor support or failure
is recorded; this check does not establish native touch fidelity. Native event
records also include rotation and any legacy prefixed radius/force properties.

The pinned native input helper has a known contact-geometry defect. On a guest
reporting a 393 by 852 point display at scale 3, a native tap produced trusted
touch events with `radiusX: 4260` and `force: 4260`. Trusted event delivery does
not establish realistic contact geometry. A native correction remains under
investigation; the public controller still uses the pinned helper.

A private native calibration narrowed this discrepancy without replacing any
browser properties. For a five-point contact with zero requested pressure, a
native UIKit diagnostic app reported `majorRadius: 5` and `force: 0`. On the
same running guest, Safari's pointer event reported width and height `10` with
pressure `0`, while its touch event reported `radiusX: 5`, `radiusY: 0` and
`force: 5`. The constructor check still returned its requested values. These
observations distinguish the native UIKit fields, pointer events and touch
events; they do not identify the faulty conversion or establish a fix. Correct
pointer dimensions alone must not be reported as corrected touch fidelity.

A further private diagnostic reproduced these fields in a standalone
`WKWebView`. For the same event, its native window observed `majorRadius: 5`
and `force: 0` before and after normal UIKit dispatch, while the page recorded
the same pointer and touch discrepancy. This narrows the issue beyond Safari's
application UI to the shared native WebKit touch path. It still does not
identify an exact faulty function or establish parity with a physical iPhone.

A physical reference subsequently supplied three normal taps from an iPhone 15
whose owner reported iOS 27.0.1. An identical diagnostic page in both browsers
recorded these values; the VM reported iOS 26.6.2 and Safari 26.6.1:

| Observation | Physical taps | Calibrated VM contact |
| --- | --- | --- |
| Touch start radii X / Y | about `24.1702 / 24.1702` | `5 / 0` |
| Touch start force | `0` | `5`, with zero requested pressure |
| Pointer down width / height | about `48.3404 / 48.3404` | `10 / 10` |
| Pointer pressure | `0` | `0` |
| Pointer up width / height | `0 / 0` | `10 / 10` |

The absolute contact sizes intentionally differ between a finger and the
calibrated input. Compare field consistency, not equality to one person's
contact size. Both screens reported 393 × 852 points at scale 3, and both
constructor controls returned the requested radii and force. Using the same
page rules out a difference between diagnostic recorders; differing OS versions
and devices still prevent attribution to one exact cause.

An isolated change to the private native helper cleared both HID contact radii
on release. The VM then reported pointer-up dimensions `0 / 0`, matching this
physical observation, while touch-end retained its previous radii. Touch-start
`radiusY: 0` and `force: 5` persisted, along with a force-change event on release.
This separates a native input release issue from the remaining touch-field
discrepancy. It is an experimental helper result, not a fix in the public
controller or a claim of complete input fidelity.

This local HTTP test does not establish TLS behavior, service-worker coherence,
public IP/DNS/WebRTC isolation, external-page rendering or parity with a physical
iPhone. Those require separate measurements. A matching canvas hash alone is
also not a fidelity verdict.


Run `scripts/sample_resources.py CONTAINER_ID --seconds 120 --output PATH`
on the Docker host to sample whole-cgroup memory, CPU, throttling and OOM
counters. Keep the output outside the checkout. `scripts/measure_ready.py`
uses the VM launch clock for daemon, unlock and foreground/screenshot timing;
rendered-page verification remains a separate step.

Add `--host-memory` when evaluating a smaller host:

```sh
python3 scripts/sample_resources.py CONTAINER_ID --seconds 600 \
  --host-memory --output /path/to/resources-with-host.jsonl
```

This also reads host `MemTotal`, `MemAvailable`, swap usage, OOM kills and
memory pressure through `/proc`. The summary reports the lowest sampled
available memory, highest sampled swap usage and counter deltas for the same
observation window. These host counters include every workload on the machine;
an OOM kill or memory stall cannot be attributed to this container from those
counters alone. Container memory usage and host available memory are separate
measurements and should not be added. Shorter headroom dips between samples
can be missed. Use this on the Docker host, since container-visible `/proc`
values may not describe that container's own limits.

Host measurements are opt-in. Older recordings report `host_memory.recorded`
as `false`. Missing host pressure support is recorded as `null`, while a missing
required memory or OOM counter fails collection and preserves partial evidence.
The summary rejects missing requested samples, counter resets and changes in
host total memory. Zero OOM kills alone do not establish sufficient headroom.

The sampler creates a new file with mode `0600` and records the host boot ID,
container process identity and `CLOCK_MONOTONIC` start time. Each sample includes
`monotonic_ns`, allowing resource observations to be aligned with launch,
request-journal or profiling timestamps from the same host boot, even if UTC
time changes. Divide nanoseconds by one billion when comparing with
`launch.json`'s `started_monotonic` seconds. Clocks from a different host or boot
cannot be compared this way. The timestamp precedes the sequential counter
reads; the counters are not an atomic snapshot. If the container process exits
or changes, sampling fails and preserves the partial recording without a
terminal success summary.

The configuration also retains the kernel's `cpu.max`,
`cpuset.cpus.effective`, `memory.max` and `memory.swap.max` values separately
from Docker's requested limits. A two-core CPU quota can run work across four
allowed CPUs and throttle it when the period's budget is exhausted; it does
not pin the container to two CPUs. Missing controller files are recorded as
`null`, which does not mean unlimited. These values describe this cgroup;
ancestor quotas, other workloads and host capacity can constrain it further.
The sampler checks these values before each sample and the terminal summary.
A detected change fails the measurement while preserving its partial data.
Transient changes between checks are not detected. Start a new recording after
changing limits so a resource comparison does not mix configurations.

Convert a resource recording into a comparable JSON report without running
Docker or contacting the guest:

```sh
python3 scripts/summarize_resources.py /path/to/resources.jsonl \
  --output /path/to/resource-report.json
```

The report distinguishes the peak observed during sampling from the container's
lifetime peak. CPU usage is calculated from cumulative CPU time over the actual
observation duration, so delayed samples do not receive equal weight. It also
reports the largest sample gap and memory-event deltas; pre-existing OOM events
remain visible in the starting counters. Memory components are reported
separately: `file` includes `shmem`, and component peaks cannot be added.

When available, each sample also records cumulative `memory.pressure` stall
microseconds. The report's `memory_pressure` field gives counter deltas and
the percentage of the observed interval spent stalled. `some` means at least
one task was stalled on memory; `full` means all non-idle tasks were stalled
simultaneously. These times overlap and must not be added. Memory stalls can
increase page latency even when OOM counters stay at zero. See the
[Linux PSI documentation](https://docs.kernel.org/accounting/psi.html).
Missing pressure files and older recordings yield `available: false` and
`null` measurements, not zero pressure. A single sample retains availability
but cannot establish stall percentages. Decreasing counters or pressure data
that appears or disappears within a recording fail the summary.

A recording with no terminal summary produces an incomplete report and exit
status 2. Invalid records or decreasing counters fail with status 1. Complete
recordings return 0. A single sample cannot establish CPU utilization. Existing
output files are never overwritten; omit `--output` to print JSON to stdout.
Resource reports do not verify page rendering or establish a minimum host size.
