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

This local HTTP test does not establish TLS behavior, service-worker coherence,
public IP/DNS/WebRTC isolation, external-page rendering or parity with a physical
iPhone. Those require separate measurements. A matching canvas hash alone is
also not a fidelity verdict.


Run `scripts/sample_resources.py CONTAINER_ID --seconds 120 --output PATH`
on the Docker host to sample whole-cgroup memory, CPU, throttling and OOM
counters. Keep the output outside the checkout. `scripts/measure_ready.py`
uses the VM launch clock for daemon, unlock and foreground/screenshot timing;
rendered-page verification remains a separate step.

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

A recording with no terminal summary produces an incomplete report and exit
status 2. Invalid records or decreasing counters fail with status 1. Complete
recordings return 0. A single sample cannot establish CPU utilization. Existing
output files are never overwritten; omit `--output` to print JSON to stdout.
Resource reports do not verify page rendering or establish a minimum host size.
