# Native crash attribution

Preserve the original failed operation, its transport response and a native
screenshot before investigating. A request timeout does not cancel the guest
operation. A later successful observation does not change the original result.

iOS can republish an earlier panic or crash report with a new filename and
modification time. Attribute reports using the **guest boot UUID**, together
with the process identity and action timeline. The host boot ID in `launch.json`
is a different identifier. Neither a crash incident ID nor a reused PID proves
that the crash belongs to the current guest boot.

## Offline inspection

Copy the guest reports into an evidence directory outside the source repository.
Record the guest boot UUID independently during the run, then inspect those
saved files without contacting the guest:

```sh
python3 scripts/summarize_crashes.py \
  "$TTT_EVIDENCE_DIR"/crashes/*.ips \
  --boot-uuid "$TTT_GUEST_BOOT_UUID" \
  --output "$TTT_EVIDENCE_DIR/crash-summary.json"
```

The tool accepts a JSON crash body or metadata followed by a JSON body. It
reads `bootSessionUUID` and the explicit boot-session line in `panicString`.
Each input is limited to 8 MiB. The summary records source hashes and separates
matching, different, missing and conflicting boot identities. Malformed files
remain listed as errors alongside successfully inspected files. The output
must be new; an existing report is never overwritten.

Add `--thread-frames 60` to include a bounded stack from threads identified by
the main queue, a `triggered` flag, or the report's `faultingThread` index. The
option accepts 0–100 frames per selected thread; zero, the default, omits stacks.
Each stack retains its selection reason, thread index, total frame count and
truncation flag. Symbol names and offsets come directly from the report. Image
names are resolved using its image table; the tool does not symbolize missing
symbols, infer that thread zero is the main thread, or include image paths.
Invalid image indexes and ambiguous selected fields remain input errors. Reports
without a selected thread produce an empty stack list, not evidence that the
main thread was idle.

Use these sampled stacks to choose the next measurement. A thread captured in
layout, keyboard or snapshot work may have spent earlier parts of its deadline
elsewhere. The stack alone does not measure which function consumed the time.

Some stackshot reports describe a termination in a top-level `reason` rather
than a `termination` object. The summary preserves that text as `report_reason`.
When `procName` is absent, it can read `procname` from the exact reported PID's
`processByPid` entry; `process_source` records which field supplied the name.
Ambiguous selected PID/name entries are errors. Other process and thread maps
are not used to infer ownership or boot identity. An XPC timeout reason alone
does not establish a `0x8badf00d` watchdog, and a report without a boot UUID
remains `unknown_boot` even when its process and termination reason are known.

Exit status zero means the supplied files were inspected, even when they
contain current-boot crashes. Missing or conflicting identities remain
unattributed. Exit status one indicates an input or output error. No result
proves browser readiness, complete crash collection, or absence of a crash.
Duplicate keys in selected fields are rejected. Repeated IDs in unselected
panic task/thread maps are counted without using those maps for attribution.
Keep summaries private too: process names, paths and termination reasons can
contain deployment data.

Apple documents `0x8badf00d` as a watchdog termination and distinguishes
`scene-update` failures from launch failures. Use the reported deadline and
event to locate the failed transition; a single sampled stack does not establish
its root cause. See [Addressing watchdog terminations](https://developer.apple.com/documentation/xcode/addressing-watchdog-terminations).

## Observed transition failure

A CPU-only native Safari trial on 2026-11-04 passed its local canvas, WebGL,
dedicated/shared worker deadlines and calibrated touch checks. Pressing Home
then exposed a different failure: a fresh crash report identified the same
Safari PID and guest boot UUID, with a background `scene-update` watchdog and
a 10-second allowance. The captured stack included UIKit keyboard and snapshot
work. This identifies the termination and sampled work, not which operation
consumed the deadline or whether the controller caused it.

The experimental resident state observer exceeded its 20-second response
deadline during that transition. A later observation found no Safari process.
One native launch request returned code 11 after about 14.9 seconds; Safari
subsequently appeared under a new PID and restored the local page. That later
render did not convert the failed control sequence into a pass.

The same trial also contained an earlier panic republished with a new filename.
Its embedded boot UUID belonged to another boot. The offline tool correctly
separates that report from the fresh Safari watchdog.

The first 900-second measurement sampled a 6.31-GiB whole-container peak under
a 6.5-GiB limit, with no container or host OOM kills. These counters do not prove
the absence of guest memory pressure, and no resource reduction was established.
Keep the experimental controller unadopted until repeated transitions pass
with native process identity, transport records and rendered-page evidence.

## A listening daemon can still be unreachable

In a later eight-core AMD trial, one phone returned no health response during
900 seconds: all 299 observations failed, with connection resets reported
during connection establishment. A host screenshot showed the lock screen;
the guest console did not contain the daemon's startup log.

A separate forensic overlay subsequently recovered `/var/log/vphoned.log`
from that stopped failed image. The inherited failed-boot records included the
daemon reporting a listener on VSOCK port 1339 and later watchdog activity.
The original image stayed read-only, with every source file hash unchanged.
The diagnostic clone reached health and shut down cleanly; its different CPU
count, CID, host boot and concurrency prevent attributing that success to one
change. No browser or product request ran, and the original failed trial
remains failed.

This evidence directs the next measurement toward endpoint binding and VSOCK
queue state. It does not establish a transport root cause. Preserve the exact
transport-error phase and original process identity, and collect read-only
queue observations during the original boot. A guest-side listening message
does not prove host reachability; a connection reset does not prove the daemon
never started. Keep inherited log order and boot provenance, since guest wall
time can move backward independently of host observation time.

The pinned runtime's
[VSOCK ring reader](https://github.com/YahiaAngelo/vphone-linux/blob/888d17ca17268aaecd277c64f2d95385b184744b/tools/vsock-rings.py)
shows the relevant QMP queue-layout and ring-index queries. Log recovery alone
establishes neither browser readiness nor a CPU saving.

## Inspect the original VSOCK endpoint

`tungtungtungbrowser.vsock_diagnostics.inspect_vsock` collects a bounded,
read-only report through the running QEMU process's Unix QMP socket. It needs
the expected CID and an ownership callback returning the same identity fields
as `MinimalServicePolicy`. Here, `host_pid` and `host_start_ticks` must identify
QEMU itself. The callback must freshly verify the original process, container,
host boot and launch record; a saved dictionary alone is insufficient.

Linux peer credentials must match that QEMU PID before any command is sent.
The collector checks ownership before and after each query, verifies the CID,
and records device status and three queue descriptions. It reads only four
bytes at each initialized avail/used ring address, using fixed monitor syntax
and validated integer addresses. Uninitialized queues produce no memory read.
No guest RPC, descriptor payload read, native input or recovery is issued.

The default three-second deadline bounds QMP I/O across the whole session.
The caller must also bound its ownership callback and surrounding worker.
Retain the returned report even when its status is `failed_observation`:
completed queries, query errors and host timestamps remain available. An
`observed` result means the queries completed, not that health or Safari is
ready. Samples are sequential and do not form an atomic VM snapshot.

Interpret the actual memory ring indices separately from QEMU's cached queue
fields when the kernel vhost backend owns the queues. Queue activity alone
does not prove that the daemon accepted a connection or that interrupts work.
Diagnostic results must not extend the original startup deadline or trigger
another boot, CID change or input attempt.

In a subsequent eight-core AMD run, this collector completed both scheduled
samples on the original two-CPU guest: one after 180 seconds without health
and one at the 900-second deadline. Each made 13 queries in under 6 ms, with
matching process ownership and CID. All three queues had 256 entries and
page-separated rings. The first two queues' avail/used indices each advanced
by 381 between samples; the event queue indices stayed unchanged. This rules
out static rings across that interval, but does not identify the connection
failure or prove correct packet contents, interrupts or listener binding.

The guest returned no health response across 299 checks: 297 connection resets,
one connection timeout and one early unavailable-device error. It required
forced shutdown, the remaining three phones never booted, and all 32 scheduled
products remain failures. No failure screenshot was retained in that run.
Both hosts were verified stopped after archive export. This validates the
collector on a failing native boot; it does not qualify a CPU configuration,
browser workload or performance improvement.
