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
default build while native browser evaluation is incomplete.
