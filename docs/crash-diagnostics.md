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
