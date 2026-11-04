# Measuring native session transitions

Use `scripts/measure_transition.py` to record one Safari → Home → Safari
transition after a page has loaded. Run it inside the same container and PID
namespace as `scripts/run_vm.py`. Keep one controller active for the guest.

First capture and inspect the requested page using the
[browser-control procedure](browser-control.md). A successful readiness report
only establishes foreground Safari; it does not establish networking or page
content. Then run:

```sh
docker compose -f runtime/compose.yaml exec iphone \
  python3 scripts/measure_transition.py /vm \
  --qmp-socket /vm/control.sock --output /vm/transition-001
```

The command requires a new evidence directory. It checks the VM launch's host
boot ID and process-start identity before native requests, and retains the
original launch clock in the report. It verifies Safari's foreground identity,
records its PID and process-start time, and captures `before.png`. It then sends
one native Home request, waits 15 seconds, captures `home.png`, and checks that
Safari still has the same process identity. Only then does it unlock and request
Safari once. After five seconds it captures `returned.png` and checks process
identity again.

`--home-seconds` and `--return-seconds` each accept 0–60 seconds. These are
explicit observation delays, not readiness checks. `--timeout` is a timeout per
native request, with a 120-second default; it is not a deadline for the entire
measurement. Each request and its start time are saved before dispatch. A
transport error, invalid identity, changed process or unverified return stops
the sequence. Native input is never retried automatically. A best-effort failure
screenshot uses the emulator display without sending another guest request.

Exit code 0 and `status: complete` mean the process-continuity and foreground
checks completed. They do **not** mean Home was displayed correctly, the page
survived intact, or external browsing works. Inspect all three screenshots.
`home_screen_verified`, `navigation_verified` and the pending visual review are
deliberately left unresolved by the command. Preserve the report from a failed
trial; a later observation or a separate successful trial does not replace it.

For a crash or process replacement, collect native reports and use the
[crash-diagnostics procedure](crash-diagnostics.md) to attribute them to the
guest boot. The transition recorder uses PID **and** start time to detect
process replacement, including PID reuse; it does not retrieve crash logs.

## Comparing session sizes

Repeated default `goto` calls can accumulate tabs. Record the initial session
state when comparing startup or memory. Saved tab databases can include multiple
groups and internal records, so distinguish total saved rows from the active
group's visible tab count. Sequential database and WAL copies are not an atomic
live snapshot, even when SQLite's integrity check succeeds.

Keep an intact stopped VM state before a tab-cleanup experiment. Close test
tabs through native Safari controls, inspect the confirmation and resulting
start page, and record the resulting session state. Closing tabs is distinct
from clearing cookies, history or website storage. Do not silently clear a
developer's session as part of readiness or memory optimization.

Measure the whole container across startup and a real page workload using the
[resource procedure](setup.md#resource-settings). Include the memory-limit and
OOM counters: zero OOM kills alone does not mean the workload avoided memory
pressure. Compare equivalent page workloads and renderer-cache state. A later
boot after cleanup also has a different cache and execution history, so one
sequential before/after result cannot isolate the effect of tab count.

## Initial native validation

On 2026-11-04, a loaded local diagnostic page survived one Home/Safari round
trip through the published SDK with the same Safari PID and start time. The
native return request took 13.8 seconds. After closing 41 test tabs through
Safari's UI and cleanly restarting the same runtime, foreground readiness took
242 seconds, compared with 233 seconds before cleanup. The later boot rendered
the local canvas, but both 15-second worker checks timed out. It was not a
successful page-readiness result.

The transition recorder's first live run used that later session. It verified
Safari in the foreground and recorded its process identity before sending one
Home request. After the observation delay, the native process inventory had no
Safari executable. The command retained the screenshots and failed before
requesting unlock or launch. This validates its stop-and-record path; it does
not establish reliable background transitions or the reason Safari exited.

Whole-container 600-second samples reached 6.50 GiB before cleanup and 6.39 GiB
afterward, with average CPU use of 2.21 and 2.36 cores respectively. The first
sample hit the memory limit without an OOM kill; the second had no memory-limit
events or OOM kills. Both runs failed their initial page checks, and their
workloads and warmed caches differed. These measurements do not qualify a
resource optimization or a smaller supported host.

## Follow-up with native logging

A subsequent trial recorded native system logs before sending Home. Safari
passed a later local page check, then disappeared during the transition. The
log and a matching-boot crash report identified a background `scene-update`
watchdog with a 10-second allowance. The sampled main-thread stack included
UIKit layout and snapshot work. This establishes the termination and sampled
work, not which function consumed the deadline.

A reversible experiment removed two unused Home widget stacks and the Today
widgets. After a clean restart with the same two guest CPUs, 2 GiB guest memory
and 1024 MiB translation cache, Safari foreground readiness took 299 seconds.
The initial local canvas, worker, WebGL and subsequent calibrated touch checks
passed. On the next Home/Safari transition, the original Safari process still
existed at the Home observation but exited before the launch response. The
recorder rejected that response because its PID differed from the original.
Matching-boot reports identified another background watchdog and a
`scene-create` watchdog in the replacement process. An intermediate successful
process check therefore does not establish continuity through the return.

Both 600-second startup samples peaked at approximately 6.32 GiB, with average
CPU use of 2.23 cores before cleanup and 2.28 afterward. Neither sample had
memory-limit events or OOM kills. The initial startup failed before cleanup,
the workloads and caches differed, and both transitions failed. Widget cleanup
has not established a performance or reliability improvement and remains an
experiment. External-page acceptance was not attempted in these trials.
