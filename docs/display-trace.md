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

This run did not reproduce the earlier blank-QMP/native-completed disagreement.
That failure remains unresolved. The earlier sequence included address-field
focus and tab reuse, whereas these were all default URL opens; tracing also
changes timing. Neither the navigation path nor the instrumentation is
established as the reason for the different outcome. Keep the prior failed
captures and use this recording as a control for a traced reproduction, without
changing rendering decisions or claiming a resource improvement.
