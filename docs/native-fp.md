# Native floating-point evaluation

The runtime remains pinned to its validated dependency revisions. An optional
QEMU experiment targets
[`14c9913d2231080bd234c69421ed4f293a25f767`](https://github.com/YahiaAngelo/qemu-vphone/commit/14c9913d2231080bd234c69421ed4f293a25f767),
nine commits after `4da65a96e039413faff00ed52e3d956bf931bfd9`.
This is the QEMU dependency revision, separate from the enclosing vphone-linux
checkout in `runtime.lock.json`.

The changes add host floating-point instructions, avoid redundant permission
register flushes, retain translation-cache entries across address-space switches,
and correct the synchronization fallback for `QEMU_TLB_UNSYNCED=2`.
The last fix applies to mode 2; the old runtime's default mode 0 already uses
exclusive synchronization. An update alone does not enable mode 2.

## Numerical correctness before browser performance

On an x86-64 host, the new revision with native FP enabled differed from both
its native-FP-disabled execution and the preserved original runtime in 476 of
644 instruction-form hashes. The aggregate hash differed too. Native FP disabled
matched the original. These are guest computation/status differences, so a
successful boot alone cannot qualify the unmodified update.

The optional [boundary patch](../patches/qemu-native-fp-boundary.patch) scopes
the guest's host-FPU state to execution of a translated instruction chain.
Translation and the surrounding emulator loop run with the host's original FP
state. Normal returns and nonlocal guest exits restore that state. This prevents
emulator-side FP status from accumulating in the guest's FPSR.

With the patch, both native-FP-enabled and disabled runs matched the original
runtime: 644 forms, 22 FPCR settings and 1,000 iterations per form and setting,
including accumulated exceptions and exception transitions. This is a numerical
regression gate, not a claim of complete ISA equivalence or faster browsing.

Use a separate checkout and build directory for evaluation. Preserve the
current runtime, prepared guest disks and cache state. The boundary patch is
separate from the required SPRR user-mask correction:

```bash
git -C "$candidate" checkout --detach 14c9913d2231080bd234c69421ed4f293a25f767
git -C "$candidate" apply --check "$project/patches/qemu-sprr-user-mask.patch"
git -C "$candidate" apply "$project/patches/qemu-sprr-user-mask.patch"
git -C "$candidate" apply --check "$project/patches/qemu-native-fp-boundary.patch"
git -C "$candidate" apply "$project/patches/qemu-native-fp-boundary.patch"
```

Build using the same configuration and dependencies as the control runtime.
The upstream fixture lives in `tests/fp/vphone-native-fp`; its `build.sh`
requires a compatible Clang/LLD toolchain. It runs on the synthetic `virt`
machine and needs no firmware, device image or network. Run that same fixture
on the control binary and on the candidate with `VPHONE_NATIVE_FP=0` and `1`.
Retain each process exit code, standard output, standard error, fixture hash,
binary hash and source patch. Require a successful exit, the complete `TOTAL`
record and identical outputs; an incomplete or timed-out run is a failure.

## Browser acceptance

Use the same immutable parent disks, copied shader cache, guest CPUs and RAM,
container CPU/memory limits, renderer and display settings for each trial.
Record the effective `VPHONE_NATIVE_FP` and `QEMU_TLB_UNSYNCED` values. Keep
clock experiments separate from the dependency comparison.

Measure cold startup, rendered local-page diagnostics, calibrated native input,
and the [Safari/Home transition](session-lifecycle.md). Continue to external
HTTPS only after those checks pass. Compare whole-cgroup CPU and memory over
equivalent successful workloads; a timeout, crash or empty page cannot establish
a resource improvement. Keep site-specific acceptance tests and their captures
outside the repository.

The patch does not alter browser JavaScript, browser APIs, device strings or
page content, and it does not require a hardware GPU. It is not applied by the
default build because the candidate has not qualified for adoption.

## Native comparison results

On 2026-11-04, three cold boots used a standard x86-64 host with four CPU cores
and 8 GiB host memory. Each container had a four-core quota, 6656 MiB memory
limit and no swap. Each guest used two application CPUs, 2 GiB RAM, a 1024 MiB
translation cache and one Lavapipe rendering thread at 1179 × 2556. There was
no hardware GPU. All trials used separate children of the same stopped disk
state, identical copied shader caches, the original Home layout and the same
native clock-maintenance settings.

| Runtime | Native FP | TLB mode | Safari foreground | Local page gate | 600 s sampled peak | Average CPU cores |
| --- | --- | --- | --- | --- | --- | --- |
| Original `4da65a96` | Off | 0 | 254.6 s | Shared worker timed out | 6.08 GiB | 2.27 |
| Patched `14c9913d` | On | 2 | 268.8 s | Passed | 6.25 GiB | 2.39 |
| Patched `14c9913d` | On | 0 | 272.9 s | Passed | 5.73 GiB | 2.29 |

All three local canvases had the same hash and WebGL reported no error or lost
context. Dedicated/shared worker replies took 3.94/4.86 seconds in candidate
mode 2 and 11.80/13.09 seconds in candidate mode 0. The original took 11.54
seconds for the dedicated worker and exceeded the 15-second shared-worker
deadline. No startup sample had a memory-limit event or OOM kill.

Both patched trials subsequently passed calibrated native touch and one
Home/Safari round trip with the same Safari PID and process-start time. Native
captures showed Home and the returned diagnostic page with its recorded touch.
Home still had visual artifacts; process continuity is not complete visual
fidelity. Touch and transition occurred after the 600-second resource window.
The failed original local gate stopped its sequence before either action.

The mode 0 candidate then rendered two external HTTPS pages through its
configured proxy. Native screenshots at 30 and 60 seconds showed the requested
content. These were sequential navigations in one guest session, with a
confirmed unchanged proxy exit. Mode 2 was stopped after its local transition;
external browsing was not compared between modes.

A subsequent 420-second browsing observation of the same mode 0 session
averaged 1.40 CPU cores and sampled a 6.49 GiB peak. The container's lifetime
peak reached its 6.5 GiB limit, with 110 memory-limit events during that window
and zero OOM kills. Its starting memory was already higher after the preceding
navigations. This reinforces the need to measure sustained browsing and tab
state; the lower startup peak does not qualify a smaller container.

These are one boot per configuration, in sequence, without resetting the host
page cache. A short proxy-route check also overlapped the final candidate's
resource window. The failed original workload cannot qualify a resource
comparison against the successful candidates. Mode 0's lower observed peak
and mode 2's faster worker replies require repeated successful comparisons.
Neither candidate improved observed cold startup. These results do not justify
a smaller supported host or changing the default runtime pin.
