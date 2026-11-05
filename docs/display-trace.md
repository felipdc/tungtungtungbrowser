# Tracing host display updates

The optional [QEMU display trace patch](../patches/qemu-display-trace.patch)
records the render work behind a QMP screenshot. Use it to investigate the
[native/QMP capture disagreement](display-sequence.md#navigation-and-capture-disagreement).
It changes no rendering decisions, guest properties, browser APIs or pixels.
It is not applied by the default runtime build and is not a display fix.

The supported source is QEMU
[`14c9913d2231080bd234c69421ed4f293a25f767`](https://github.com/YahiaAngelo/qemu-vphone/tree/14c9913d2231080bd234c69421ed4f293a25f767).
The tested runtime uses `apple-gfx-mmio`, whose asynchronous rendering path is
in `hw/display/apple-gfx.c`. Identify the active device in the actual run before
interpreting this trace; `apple_displaypipe_v4.c` is a different display path.

## Build and check

Use a separate source checkout and retain the original runtime binary. The
checker copies three pinned display files into a new external evidence directory,
applies the patch there, and tests the actual extracted render functions with
synthetic renderer and event-queue callbacks. It leaves the supplied checkout
unchanged and starts no guest or GPU workload. A C compiler with AddressSanitizer
and UndefinedBehaviorSanitizer, plus Git, is required.

```sh
python3 scripts/check_qemu_display_trace.py \
  --qemu-source /external/qemu-candidate \
  --output /external/display-trace-check

git -C /external/qemu-candidate apply --check \
  /project/patches/qemu-display-trace.patch
git -C /external/qemu-candidate apply \
  /project/patches/qemu-display-trace.patch
```

Rebuild QEMU with the control runtime's dependency pins, CPU corrections and
build settings. Keep the exact source diff, binary hash and compiler output.
The trace patch touches only the display implementation, its shared structure
and trace declarations; it does not replace the separate
[native floating-point boundary correction](native-fp.md).

Twelve local scenarios passed with tracing disabled and enabled: clean and dirty
surfaces, overlapping requests and presents, encode/readback failures, dimension
changes, same-size target replacement, a missing target, an unwatched present,
and a library-owned window, plus nonzero commit and wait results. The fixture
checks row padding, reference counts, completion behavior and trace stage
ordering. Disabled tracing made no additional
nanosecond-clock reads. This is a behavior check with synthetic callbacks, not a
native visual or performance result. A full QEMU build succeeded, and a separate
machine-without-a-guest check verified that all four events start disabled and
can be enabled and disabled through QMP. A bounded native recording is described
below; the trace remains an optional diagnostic, not a runtime fix.

## Record a bounded observation

Use QEMU's `log` trace backend and give the experiment a fresh external log with
`-D /external/trial/qemu-display.log`. Keep tracing disabled during startup and
warm-up. Once the intended browser state is established, negotiate QMP capabilities
and inspect the exact event set:

```json
{"execute":"trace-event-get-state","arguments":{"name":"apple_gfx_frame_*"},"id":1}
```

Require all four events below to be present and disabled. Then enable them once
for the bounded capture sequence:

```json
{"execute":"trace-event-set-state","arguments":{"name":"apple_gfx_frame_*","enable":true},"id":2}
```

Save original navigation/capture times, the local page's disjoint counter ranges
and run identifiers, and sequential QMP/native/QMP images. End the observation
by disabling the same event set and verifying its state:

```json
{"execute":"trace-event-set-state","arguments":{"name":"apple_gfx_frame_*","enable":false},"id":3}
{"execute":"trace-event-get-state","arguments":{"name":"apple_gfx_frame_*"},"id":4}
```

Do not silently repeat a timed-out screenshot or native input request. An
incomplete observation remains incomplete. Preserve raw logs, captures, host
boot identity, process identity and resource samples outside the repository.
Logging adds work; compare performance only with equivalent tracing settings.

### What the paired captures observe

QMP reads QEMU's display surface after requesting an update through the device.
The native screenshot takes a separate path. In the pinned
[Icli implementation](https://github.com/owngoal-dev/icli/blob/2fbdb5ee61d8c320732deb9768b22506f5e3d411/Sources/IcliPrivate/IcliPrivate.m),
`orientedScreenImage` first tries `_UICreateScreenUIImage` when its SpringBoard
and capture-readiness conditions hold. Otherwise, or when that returns no image,
it asks `CARenderServerRenderDisplay` to draw into a newly allocated IOSurface,
then copies its pixels. The
[native API](https://github.com/owngoal-dev/icli/blob/2fbdb5ee61d8c320732deb9768b22506f5e3d411/Sources/IcliKit/Input/Screen.swift)
encodes a JPEG but does not report which branch supplied the image.

Neither path reads the other's returned pixels. A completed native image
between blank QMP captures therefore demonstrates a disagreement between the
capture paths; it does not locate the fault in QMP readback. Native capture can
request additional guest rendering, and its exact image-production time lies
somewhere inside the recorded RPC interval. Keep both surrounding QMP captures,
compare page counters and run identifiers, and retain any later regression.
Do not treat sequential captures as simultaneous or infer the native branch
from the resulting image alone.

## Read the events

Each event includes `ns`, the host-side `QEMU_CLOCK_REALTIME` value in nanoseconds.
It is not the guest wall clock or the page's `performance` clock. Associate the
process and host clock before correlating it with other recordings. `display`
and `target` are host pointer identities, and `job` is a per-display render
sequence number; they are meaningful only within the recorded process lifetime.

| Event | What it records |
| --- | --- |
| `apple_gfx_frame_state` | A display request, a delivered guest-present notification, completion, or a request with no render to wait for; includes dirty/rendering flags and the latest job number |
| `apple_gfx_frame_job` | A queued job's display, retained target and dimensions |
| `apple_gfx_frame_stage` | Encode, commit, wait and pixel-readback boundaries; each end event carries the library return status |
| `apple_gfx_frame_copy` | Whether encoding succeeded, dimensions still match and the retained target is still current, after the existing conditional surface-copy path |

For a successful job, measure queue delay and the time spent encoding,
committing, waiting, reading pixels and completing the job separately. A frame-state job number
identifies the latest queued job, not a verified guest frame or page. A present
notification can be coalesced before it reaches QEMU. Enabling tracing in the
middle of a job can omit its beginning; retain that partial record.

`encoded=1` and `same_size=1` describe the existing copy condition. The additional
`same_target` field is an observation: this patch does not reject a copy merely
because the target changed while retaining the same dimensions. The fixture
explicitly preserves that upstream behavior. A successful copy also does not
prove that the source pixels represent the latest page.

The original path ignores the commit and wait return values before attempting
readback. This trace records those values without changing that behavior. A
successful read can therefore yield `encoded=1` even after a nonzero commit or
wait status. Inspect every stage result; do not interpret `encoded` as proof that
all renderer operations succeeded. No such native failure has yet been observed
with this instrumented candidate.

`request-no-render` means the request found no render underway and queued none.
That can mean the surface is current, or that a surface/target is missing; it
does not establish readiness. Retain the dirty flag and surrounding mode events.

QMP completion can wake a screenshot waiting for a render while a newer present
is pending. The trace helps distinguish that ordering from an encode/readback
failure or a request that queued no render. It does not, by itself, explain
blank content, establish correct guest composition, or replace
the paired images. Keep the failed captures when a later image matches.

## Summarize a recording

After disabling tracing, save a stable copy of the log outside the repository.
The [offline analyzer](../scripts/analyze_qemu_display_trace.py) records its hash
and writes a new JSON report without replacing earlier evidence:

```sh
python3 scripts/analyze_qemu_display_trace.py \
  /external/trial/qemu-display.log \
  --output /external/trial/display-summary.json
```

The report groups work by display pointer and job number, retains original line
numbers and timestamps, and summarizes queue, encode, commit, wait, readback and
completion delays. It counts requests that queued no render separately. Use a
single process's recording; identities cannot be combined across restarts.
Request timestamps and completions with a pending present are retained for
correlation with capture intervals.

`fully_observed_jobs` means the expected trace boundaries were present in order.
It does not mean the library calls succeeded or the pixels were correct.
Nonzero end statuses and false copy flags remain separate findings, including
a commit or wait error followed by a successful pixel read. Jobs cut off by the
observation window, duplicate events and inconsistent ordering remain in the
report. Duration summaries include available boundary pairs from partial jobs;
consult the job's issues before interpreting them.

Events from different threads can reach the log in a different order from their
timestamps. The analyzer reports those inversions and orders each job by its
recorded time. It rejects malformed recognized events rather than silently
dropping them. Neither a clean trace nor its timing summary establishes visual
readiness or a performance improvement; correlate it with the original paired
captures and equivalent workloads.

## Native observation

A recording on 2026-11-05 UTC used the same standard four-core x86-64 host,
two guest application CPUs, 2 GiB guest RAM, 1024 MiB translation cache and
6656 MiB container limit as the preceding trial. Rendering used one Lavapipe
thread at 1179 × 2556, with no hardware GPU. The patched native-FP runtime and
TLB mode 0 remained unchanged. Tracing was disabled during cold startup and the
warm baseline, then enabled for three normal URL opens in foreground Safari.

All three sequence pages reached their final counters and retained the same
Safari PID and process-start time. Sequential QMP/native/QMP capture groups
showed the expected final counters at 45 and 90 seconds. No sampled counter
regressed or became blank after new content was observed. Immediate captures
still showed the previous page, demonstrating why an acknowledgment is not
content readiness. Saved tabs grew from 11 to 14; this was not a tab-reuse trial.

The 507 trace events contained 36 fully observed jobs. Every recorded end
status was zero, and every copy had `encoded`, `same_size` and `same_target`
set. No recorded completion had a pending present. All 21 QMP screenshot
intervals contained exactly one traced request; 12 requests reused a cached
surface without queuing a render.

| Recorded interval | Median | Maximum |
| --- | --- | --- |
| Render job creation to surface copy | 97.7 ms | 472.6 ms |
| Library encode | 0.319 ms | 183.8 ms |
| Library wait | 93.7 ms | 470.2 ms |
| Pixel readback | 0.892 ms | 5.27 ms |
| Complete QMP screenshot call, 21 samples | 36.5 ms | 228.9 ms |
| Complete native screenshot call, 9 samples | 3.50 s | 8.46 s |

The three navigation acknowledgments took 1.33, 0.52 and 0.62 seconds.
Separately, the untraced warm diagnostic acknowledged navigation in 0.68 seconds
and delivered its page results in 17.97 seconds. Its 15-second capture still
showed collection in progress; the 30-second capture showed the matching saved
observation, and the 60-second capture was identical. These are distinct
measures of control, page execution and sampled visual completion.

Cold foreground verification took 264.6 seconds. The first cold page delivered
its diagnostic results at 353.7 seconds from launch; its captured canvas was
correct, but the screen still showed collection in progress. Foreground timing
therefore does not establish a completed first page within the approximate
300-second target. Comparing the returned crash inventory with the immutable
parent found one added report and no changed entries; the addition carried an
older boot's panic identity. No new current-boot crash was found in that
inventory. The guest subsequently stopped cleanly in 7.4 seconds.

The 420-second cold, 300-second warm and 600-second sequence/resource windows
averaged 2.36, 1.97 and 1.80 host CPU cores respectively. Their sampled memory
peaks were 6.42, 6.43 and 6.43 GiB, with zero memory-limit or OOM events. These
different workloads and tracing intervals do not form an optimization
comparison. Memory remains close to the 6.5 GiB cap; a smaller cap is not
qualified. A separate [untouched-browser CPU observation](qemu-wfe.md#cpu-use-with-a-settled-warm-page)
identifies the busiest guest CPU threads for further profiling.

This run did not reproduce the earlier blank-QMP/native-completed disagreement.
That failure remains unresolved. The earlier sequence included address-field
focus and tab reuse, whereas these were all default URL opens; tracing also
changes timing. Neither the navigation path nor the instrumentation is
established as the reason for the different outcome. Keep the prior failed
captures and use this recording as a control for a traced reproduction, without
changing rendering decisions or claiming a resource improvement.

## Mixed focus and reuse observation

A second recording used the same CPU-only configuration and binary. It retained
an initial default navigation, two stopped recorder attempts, and a separately
guarded address-bar continuation followed by another default navigation. The
[control audit](native-control-audit.md#bounded-read-settling-and-existing-url-display-forms)
describes the recorder corrections and the native root timeout followed by one
successful read. Tracing was disabled between the original sequence and the
continuation, and disabled again before shutdown. No focus or text input was
repeated to recover a missing reply.

All three pages reached their final counters. The reused page's 15-second
QMP/native/QMP group showed the old page at 20, then the new page at 65 and 66.
Both later groups showed 68. The subsequent default navigation showed 129 in
all three 15-second captures and 132 in the 45- and 90-second groups. No sampled
counter regressed or became blank after new content appeared. The earlier
45-second blank-content failure was not reproduced. The separated continuation
and its longer settled interval prevent treating this as an uninterrupted
reproduction or a fix.

Visual correctness remains incomplete. The paired focus captures did not show
the address controls even though accessibility reported the field; the editing
state was later confirmed before text entry. The reused page's first 15-second
QMP capture also showed rectangular toolbar artifacts beneath the old content.
The following native capture showed new content with the ordinary toolbar.
Retain these differences; correct diagnostic regions and eventual agreement
do not qualify all browser chrome or physical-device fidelity.

The trace contained 1,002 events and 60 fully observed render jobs, all with
zero end statuses and all copy flags set. Each of the 25 QMP screenshot calls
contained one traced request, with none unassigned. Thirteen requests queued
no render. Seven completions had a pending present, all within the first three
seconds after Return; this identifies scheduling overlap but does not explain
the earlier blank-content failure. Median job-to-copy time was 98.9 ms, with
a 361.2 ms maximum. Complete QMP calls had a 43.4 ms median and 252.3 ms maximum;
eleven native screenshot calls had a 2.71 s median and 6.20 s maximum.

The 420-second cold and 300-second warm resource windows averaged 2.31 and
1.96 host CPU cores, with sampled peaks of 6.39 GiB each. The 600-second
focus/resource window averaged 1.85 cores and peaked at 6.40 GiB, with zero
memory-limit or OOM events during that window. It includes the stopped attempts
and idle time and ends before the continuation's last captures; it is not an
equivalent-workload comparison or a complete resource measurement of the
continuation. No smaller memory limit is qualified.

The returned crash inventory had one added report and one changed entry; both
available bodies identified older native boots. No current-boot crash was
found in that inventory. The guest stopped cleanly in 7.7 seconds, preserving
its parent disks and renderer cache. Local diagnostics do not establish
external-page acceptance, and runtime defaults remain unchanged.
