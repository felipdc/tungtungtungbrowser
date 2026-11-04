# Graphics allocation accounting

Use `scripts/analyze_graphics_memory.py` to replay an existing metal2vulkan
memory log offline. It makes no guest requests and does not release memory.
Keep logs and reports outside this repository: console output can contain
captured URLs and application data.

```sh
python3 scripts/analyze_graphics_memory.py /external/trial/console.log \
  --output /external/trial/graphics-memory.json
```

The report is published atomically with mode `0600` and refuses an existing
destination. It records the SHA-256 of the input bytes actually read. Prefer a
stopped process's complete log, including startup, and keep its runtime build,
configuration and capture interval. Do not concatenate processes or trials.

## What the report establishes

The supported source is metal2vulkan
[`cd1a08ae5442065a11af0fb79d1bdc35c1f0e1b9`](https://github.com/YahiaAngelo/metal2vulkan/blob/cd1a08ae5442065a11af0fb79d1bdc35c1f0e1b9/crates/metal2vulkan-runtime/src/memory.rs).
Its allocator normally places resources in 64 MiB blocks, with separate pools
for buffers and images. Its existing memory log records pooled placements only
when they are at least 4 MiB. Imported host memory and dedicated allocations
are accounted for separately from pooled capacity.

The analyzer checks handle lifetimes, memory types, resource kinds, placement
bounds, overlaps and matching returns. It distinguishes a block allocated and
immediately freed from one retained by a pool. A retirement notification is
recorded separately because it can precede the driver's actual release.
Device teardown may discard remaining placement records; their count is retained.
Unknown events, partial memory-log lines, missing allocations and inconsistent
returns fail the command instead of producing a resource estimate. DMA-buf
import logs are currently unsupported and fail explicitly; the tested CPU
renderer recordings used host-pointer imports.

`consistent_logged_prefix` means the recognized events can be replayed. It does
not prove that the recording started at process creation, that no complete
lines were lost, or that the final line coincides with process exit. The report
therefore keeps `capture_completeness_verified` false. An EOF with live blocks
is a retained observation, not an implied free or an error by itself.

`pooled.unaccounted_bytes` is capacity not covered by the **logged** live
placements at the end of that prefix. In the unmodified logger it includes
unlogged small resources, free fragments, alignment gaps and unused capacity.
Likewise, a block with no logged placements is not necessarily empty.
`largest_unaccounted_range_bytes` describes a gap in the log's coverage, not
necessarily a usable contiguous allocation. `reclaimable_bytes` stays `null`.

Neither pool capacity nor imported memory sizes are resident-memory readings.
Use the [whole-container recorder](setup.md#resource-settings) and separate
host memory inspection when assessing actual cost. Shared mappings can alias
the same pages; adding their RSS can double-count them. Retired bytes are a
cumulative notification count, not the bytes still waiting. Pool and placement
peaks may occur at different events and must not be added.

Three preserved native log prefixes replayed without lifecycle errors. Their
final pools held 1,280, 1,408 and 1,664 MiB, with 491.9, 489.1 and 629.0 MiB
respectively not accounted for by the filtered placement log. These are
different historical workloads, not an isolated configuration comparison.
They identify a measurement gap, not a demonstrated memory saving.

## Optional complete-placement diagnostic

[`metal2vulkan-log-all-placements.patch`](../patches/metal2vulkan-log-all-placements.patch)
changes only logging coverage in the pinned source. With
`METAL2VULKAN_LOG_MEMORY=1`, the patched build records every nonempty pooled
placement and emits this declaration when the allocator receives its logger:

```text
metal2vulkan: memory: placement coverage: minimum 1 bytes; protocol 1
```

The analyzer requires that declaration before any memory event to interpret
sub-4-MiB placements. Repeated matching declarations from allocator creation
are accepted; a late first declaration fails. The patch leaves pool sizes,
resource lifetimes, shaders and browser APIs unchanged. It is not applied by
the default runtime build. Setting the environment variable on an unpatched
binary still gives the sparse upstream log.

Use a separate exact-revision source checkout and build directory:

```sh
git -C "$candidate" checkout --detach cd1a08ae5442065a11af0fb79d1bdc35c1f0e1b9
git -C "$candidate" apply --check "$project/patches/metal2vulkan-log-all-placements.patch"
git -C "$candidate" apply "$project/patches/metal2vulkan-log-all-placements.patch"
```

Rebuild the renderer and the emulator that links it with the same dependency
pins and build settings as the control. Preserve the original binary and exact
source diff. Keep the additional log in private external storage, bound its
duration and budget for its size. Logging every placement can add substantial
I/O and timing overhead; do not compare an instrumented run's performance with
logging disabled. Full placement coverage still needs capture integrity and
native validation; it does not make unplaced memory automatically reclaimable.

Before trying a smaller pool or changing retention, capture the same workload
with complete placement coverage, review allocation turnover and fragmentation,
then compare uninstrumented builds on identical stopped parents, tab state and
shader caches. Require reliable cold startup, warm local workers/canvas/WebGL,
native input and external-page acceptance. Preserve failed observations. The
[backing-page and pipeline-cache experiments](lavapipe-memory.md) did not
demonstrate a whole-browser resource win and remain separate changes.

## Local allocation and readback check

[`native/check_graphics_memory.rs`](../native/check_graphics_memory.rs) exercises
small and large buffer placements through the renderer's public API. Twenty
rounds fill a private buffer, copy to shared storage, wait for completion and
check every byte and an adjacent live allocation. Each round also creates and
releases a small image. Image allocation coverage is separate from texture
rendering fidelity; this fixture does not boot iOS or validate Safari.

Build in an external Cargo project with Rust 1.98.1, using the exact patched
source paths. For example, create this external `Cargo.toml`, replacing the
three absolute paths with yours:

```toml
[package]
name = "ttt-pool-coverage-check"
version = "0.1.0"
edition = "2021"
[dependencies]
metal2vulkan-runtime = { path = "/external/candidate/crates/metal2vulkan-runtime" }
metal2vulkan = { path = "/external/candidate/crates/metal2vulkan" }
[[bin]]
name = "ttt-pool-coverage-check"
path = "/project/native/check_graphics_memory.rs"
```

```sh
CARGO_INCREMENTAL=0 CARGO_BUILD_JOBS=1 CARGO_PROFILE_DEV_DEBUG=0 \
  cargo build --manifest-path /external/check/Cargo.toml
VK_DRIVER_FILES=/external/lvp_icd.json LP_NUM_THREADS=1 \
  MESA_SHADER_CACHE_DISABLE=true METAL2VULKAN_LOG_MEMORY=1 \
  timeout 90 /external/check/target/debug/ttt-pool-coverage-check \
  > /external/check/result.json 2> /external/check/memory.log
python3 scripts/analyze_graphics_memory.py /external/check/memory.log \
  --output /external/check/allocations.json
```

Select only the installed CPU Lavapipe ICD, whose manifest filename varies by
distribution, and expose no hardware GPU devices. The check refuses a device
name without `llvmpipe`. Require exit status zero, `status: passed`, twenty
buffer rounds and the coverage marker plus sub-4-MiB placement events in the
raw log. The analyzer must succeed. Keep the binary hash, source diff, Cargo
lockfile, toolchain, driver and process exits with the result. Repeat the fixture
with memory logging disabled to check the ordinary diagnostic-off path; an
empty memory log does not qualify for analysis. These bounded checks do not
establish browser memory savings or native guest reliability.

The pinned patched source passed a local compile check and 50 selected
allocator and memory-related tests. The standalone check then passed all twenty
rounds with logging enabled and disabled on CPU Lavapipe. The enabled log
contained 256-byte buffer and 4 KiB image placements, replayed consistently, and
ended with no logged live pool blocks. No instrumented iPhone run has been
qualified yet. A two-job build with debug information exceeded a 4 GiB build
container limit; the documented one-job, no-debug-information build completed
within that same limit. Build memory is separate from browser memory use.

## Linking the diagnostic into a runtime

QEMU links the renderer statically. Replacing a shared library beside an
existing QEMU binary does not install this diagnostic. Build a separate
renderer distribution, then configure and link a separate QEMU against it.
Keep the control binary, disk parents and browser caches intact.

Use the pinned reims-vgpu revision
`83dcaf58e484cff369996da7cc10a0cdd0dc8bc3`, with the patched metal2vulkan
checkout beside it as its `Cargo.toml` requires. Preserve the dependency
lockfile and verify its hash before and after the build. The QEMU source,
patches, compiler settings and enabled features must match the selected
control; installing this diagnostic does not require adopting a newer QEMU.
Follow the source repositories' worktree instructions when preparing separate
checkouts.

For an already prepared source tree, build the renderer into a new external
directory. `$build_root` must be an absolute path outside this repository:

```sh
cd "$renderer_source"
export CARGO_TARGET_DIR="$build_root/reims"
export CARGO_BUILD_JOBS=1 CMAKE_BUILD_PARALLEL_LEVEL=1 MAKEFLAGS=-j1
. scripts/internal/dev-env.sh
cargo +1.98.1 xtask dist --platform vulkan --out "$build_root/dist-vulkan"
```

This uses the renderer's release profile. The debug-information override in
the standalone fixture above is not a runtime optimization. For QEMU, create a
new build directory and set
`PKG_CONFIG_PATH="$build_root/dist-vulkan/lib/pkgconfig"` when running the
selected source's `configure`, retaining the control's options including
`--enable-pvg`. Build `qemu-system-aarch64` there. Record the source diffs,
toolchain, lockfile, static-library and final-binary hashes, and each build's
exit status.

Wait for the entire distribution command to exit. The pinned renderer's
`xtask dist` builds the release library, localizes its static symbols, and then
runs another Cargo invocation to discover native linker libraries before
writing package metadata. The first `Finished release` line or an archive
appearing on disk does not establish that the distribution is complete.

Before a native boot, check the resulting binary inside the intended runtime
environment: required shared libraries must resolve, the expected machine
must be available, and the graphics device must initialize with only the CPU
Lavapipe ICD exposed. A paused device check does not execute an iPhone guest.
The pinned QEMU fork's generic `virt` machine does not allow adding
`apple-gfx-mmio` as a dynamic device. Check that device on the actual machine;
a standalone renderer C API check covers the bridge, not QEMU's machine wiring.
If the selected QEMU includes the optional native-FP changes, retain the
[numerical regression gate](native-fp.md) as well. Only a subsequent cold boot
and warm workload can qualify the instrumented native runtime.

Run the loader check in the exact deployment image, even when the binary has
already run in a local test image. QEMU can select a system `libfdt` during
configuration; that binary then needs `libfdt.so.1` at runtime. The current
runtime Dockerfile includes `libfdt1`, but a retained older image may not. A
native preflight caught this mismatch before a guest was started. Rebuild the
deployment image from its intended Dockerfile, or retain the control build's
internal libfdt configuration, and record the resulting dependency change.
Preserve the failed preflight separately from the corrected check.
