# Experimental scheduling of ARM wait hints

`patches/qemu-wfe-backoff.patch` adds an optional host sleep for AArch64 `WFE`
instructions under multithreaded TCG. It is disabled by default, and
`scripts/build_runtime.sh` does not apply it. This is a scheduling experiment,
not a replacement implementation of ARM event-register semantics or an adopted
browser optimization.

The pinned QEMU translator treats `WFE` as a no-op under parallel TCG. A previous
30-second startup profile attributed 85% of its host user-space samples to two
guest kernel blocks in a lock-wait loop containing `LDXR` and `WFE`. That profile
used four application CPUs and does not establish the same distribution for
other configurations. See the [pinned translator](https://github.com/YahiaAngelo/qemu-vphone/blob/4da65a96e039413faff00ed52e3d956bf931bfd9/target/arm/tcg/translate-a64.c)
and [QEMU's scheduling design](https://www.qemu.org/docs/master/devel/multi-thread-tcg.html).

The patch adds the ARM CPU property `wfe-backoff-us`, default `0`, with a maximum
of `1000`. A positive value advances the guest PC, calls `g_usleep`, then returns
through the CPU loop so pending interrupts can be serviced. The host scheduler
can extend the requested delay. Architectural single-step and single-threaded
TCG retain their existing paths. `WFET`, `SEV` and guest locks are unchanged.
There is no dependency on a future event to end the sleep, but process failure
or host scheduling can still delay execution.

## Build an experimental binary

Use a separate external checkout at QEMU commit
`4da65a96e039413faff00ed52e3d956bf931bfd9`, with the normal runtime dependencies
and required SPRR fix. Preserve the baseline binary and source diff before
applying another patch:

```sh
git -C "$TTT_QEMU_SOURCE" apply --check /path/to/tungtungtungbrowser/patches/qemu-wfe-backoff.patch
git -C "$TTT_QEMU_SOURCE" apply /path/to/tungtungtungbrowser/patches/qemu-wfe-backoff.patch
ninja -C "$TTT_QEMU_SOURCE/build" -j2 qemu-system-aarch64
```

This patch modifies GPL/LGPL-covered QEMU files; it is not covered by the
controller's MIT license. See [third-party notices](../THIRD_PARTY_NOTICES.md).
Keep built binaries, guest data and experimental output outside this repository.

## Check bounded handoffs before running iOS

The project-authored AArch64 test boots two Cortex-A57 CPUs in a disposable
64-MiB `virt` machine. It exchanges values with and without `SEV`, then executes
consecutive `WFE` instructions without a future event. This checks progress for
those paths. It does not prove interrupt behavior, all ARM memory-ordering cases,
physical-device equivalence or Safari compatibility.

The runner needs Python 3.11 or newer and LLVM's assembler, object-copy and
ELF-inspection tools. Tool paths are configurable with `--llvm-mc`,
`--llvm-objcopy` and `--llvm-readelf`.

```sh
python3 scripts/check_qemu_wfe.py \
  --qemu "$TTT_QEMU_SOURCE/build/qemu-system-aarch64" \
  --output /path/to/external/wfe-check
```

The evidence directory must be new. The runner records every command before
execution, keeps stdout and stderr, rejects unresolved relocations, and bounds
each emulator process to 12 seconds. It runs alternating disabled/enabled
multithreaded checks, a single-threaded check, and an invalid-property check.
Require process exit zero and `report.json` status `complete`. A timed-out
disposable process is terminated and recorded as a failure; the runner never
operates on a running browser or retries a failed test.

Run this check separately from browser measurements. Its CPU totals cover the
disposable process, not a containerized iPhone. Less synthetic busy-wait CPU does
not establish faster browser startup.

A live run of this runner passed all five handoffs and the invalid-range check.
The two disabled multithreaded processes used 2.06 and 2.05 CPU seconds; the two
enabled processes used 0.228 and 0.229 CPU seconds. The guest program deliberately
waits for about one second, so this primarily measures avoided busy-wait work.
The single-threaded control used 1.05 CPU seconds. These few observations are not
a statistical study. A separate invocation against the unpatched binary failed
at the first unsupported CPU property, retained its error and ran no later test.

During preparation, the relocation check rejected a global entry-symbol
reference before any emulator process ran. The fixture now uses a local label
for that address. The runner also preserves the invoked LLVM tool name:
resolving the `llvm-readelf` symlink to `llvm-readobj` changes its output format.
Both the failed preparation and corrected check were retained.

## Native comparison method

Use the same experimental binary for both sides, selecting `0` or `50` through
`-global arm-cpu.wfe-backoff-us=VALUE`. This global setting also reaches the
emulated SEP CPU; record every CPU's effective property through QMP.

The native comparison uses two application CPUs, 2 GiB guest RAM, a 1 GiB
translation cache, one lavapipe thread, and identical container limits. A single
recorded disk state supplies both boots, and each trial receives a separate copy
of the same initial renderer cache. The local page open and native tap are
scheduled relative to the original launch clock, with the same deadlines.

QEMU's `-snapshot` option directs disk writes into temporary copy-on-write
files. This is a disk-testing mode, **not** saved machine state or fast resume.
It discards the trial's disk changes when QEMU exits. Do not use it for a session
whose new cookies, downloads or other state must survive shutdown. See the
[QEMU disk documentation](https://www.qemu.org/docs/master/system/images.html).

For this runtime, inspect all five inserted drive views: firmware and PCI views
of the main disk and NVRAM, plus SEP storage. Require temporary qcow2 tops and
read-only original raw/file nodes. Put `TMPDIR` on external storage with enough
free space; the container's small `/tmp` is unsuitable. Verify original file
metadata and recorded small-file/cache hashes after each clean shutdown. These
checks supplement the observed read-only block graph; they are not a full hash
of a large guest disk.

The method controls saved device and renderer-cache contents. Host page-cache
warmth, guest scheduling, clock correction and external network responses can
still differ. Linux charges memory to the cgroup that instantiated it until
release; shared file pages do not necessarily belong to the current consumer.
Compare anonymous/file/shared-memory counters and host available memory as well
as the cgroup total. Do not add the overlapping `file` and `shmem` counters or
attribute a lower peak to the patch without repeatable evidence. See the
[kernel's memory ownership rules](https://cdn.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html#memory-ownership).

Record cold foreground time separately from loaded-page time,
canvas/WebGL/worker results, trusted touch fields, whole-container resource
measurements, and clean shutdown. A failed readiness or local-page check is
retained; it does not trigger another launch or navigation automatically.

An earlier four-application-CPU trial did not produce a usable Safari session.
It followed an unclean diagnostic exit and an unrelated Home-layout change;
the layout was restored during that trial. That mixed result was retained and
the setting was left disabled. It cannot establish either a browser benefit or
that this scheduling change caused the failure.

## Three sequential native runs

The controlled comparison completed three 600-second startup/local-page windows
in disabled/enabled/disabled order on one standard x86-64 host with four CPUs
and 8 GiB RAM. Each container allowed four CPUs and 6.5 GiB RAM with no swap.
Rendering used the same unmodified lavapipe build and no hardware GPU. The
guest configuration and disk/cache procedure were those described above.

| Measurement | Disabled, first | 50 microseconds | Disabled, repeat |
| --- | ---: | ---: | ---: |
| Cold readiness including foreground verification and screenshot, seconds | 225.0 | 228.0 | 235.6 |
| Local page request to diagnostic callback, seconds | 16.9 | 51.1 | 50.9 |
| Average CPU cores over 600 seconds | 2.212 | 2.218 | 2.239 |
| Container lifetime peak at the end of that window, GiB | 6.412 | 6.090 | 6.326 |
| Minimum sampled host available memory, GiB | 1.307 | 1.498 | 1.247 |
| Observed contact duration for one requested 50 ms tap, milliseconds | 91 | 58 | 58 |

The page request was scheduled at 350 seconds and one calibrated native tap at
500 seconds in every run. All three produced the same canvas digest, successful
dedicated/shared-worker responses, a live WebGL context with error zero, and
native screenshots showing the page and recorded touch. The tap checks passed
radius 5/5, force zero, pointer dimensions 10/10 on contact and 0/0 on release
through all four property readers. These checks cover that local workload and
contact; they do not establish complete physical-device equivalence.

All three windows had zero cgroup memory-limit events, OOM events and CPU
throttling. The candidate's earlier partial memory result looked substantially
better than its final 600-second peak. At the 410-second samples, most of its
difference from the first control was in file-backed memory; anonymous memory
was slightly higher. Host available memory did not move by the same amount.
The repeat control also shows why host cache/accounting and run order must be
considered. This comparison does not isolate the cause of the smaller candidate
peak or establish a lower safe container limit.

There is no demonstrated CPU or cold-start improvement. The first control's
shorter local callback did not repeat, so the candidate's longer callback
cannot be attributed to the patch alone. With one enabled observation and two
controls, these results are not a statistical performance study. Keep the
backoff disabled and the patch outside the default runtime build.
