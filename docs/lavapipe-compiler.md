# Software-renderer compiler experiment

Mesa's `GALLIVM_PERF=nopt` option reduces LLVM optimization work. It is an
experimental setting, not a project default. Less compilation work can also
produce slower shaders. A successful compute check does not establish native
Safari rendering fidelity, lower browser memory use or faster startup.

The reviewed implementation is Mesa 25.0.7 with LLVM 19:

- `src/gallium/auxiliary/gallivm/lp_bld_init_common.c` recognizes **`nopt`**.
  `no_opt` is not the spelling accepted by that option table.
- `lp_bld_init.c` selects LLVM code generation without optimization instead of
  the default level.
- `lp_bld_passmgr.c` retains the initial `default<O0>` passes, then uses
  `mem2reg` instead of its larger optimization sequence for LLVM 18 and newer.
- `src/gallium/drivers/llvmpipe/lp_screen.c` includes the Gallivm performance
  flags in the disk shader cache identity. The two modes have different cache
  keys; changing the flag does not reuse the other mode's compiled shaders.

## Standalone correctness and timing check

The optional [compute shader](../native/check_lavapipe_shader.comp) and
[Vulkan runner](../native/check_lavapipe_shader.c) exercise eight specialization
variants, integer arithmetic, exact binary floating-point fractions, shared
workgroup memory and a workgroup barrier. Every output component is compared
with a CPU reference after the first dispatch and 16 repeated dispatches.
Each dispatch has 1,024 invocations.

The runner requires one Vulkan device and rejects it unless it reports both
the CPU device type and llvmpipe. Select a lavapipe ICD explicitly. Do not pass
hardware GPU devices into a test container. Dependencies are a C compiler,
Vulkan development headers and loader, `glslangValidator`, and `spirv-val`.
On the validated Debian platform, these are supplied by `build-essential`,
`libvulkan-dev`, `glslang-tools`, and `spirv-tools`, alongside the renderer.

Run from the repository root, with an ICD file that identifies the library
being tested:

```sh
export VK_ICD_FILENAMES=/absolute/path/to/lavapipe-icd.json
export LP_NUM_THREADS=1
TTT_SHADER_CHECK_DIR=$(mktemp -d)
cc -std=c11 -O2 -Wall -Wextra -Werror native/check_lavapipe_shader.c \
  -lvulkan -o "$TTT_SHADER_CHECK_DIR/check"
glslangValidator -V --target-env vulkan1.1 native/check_lavapipe_shader.comp \
  -o "$TTT_SHADER_CHECK_DIR/shader.spv"
spirv-val --target-env vulkan1.1 "$TTT_SHADER_CHECK_DIR/shader.spv"

GALLIVM_PERF= MESA_SHADER_CACHE_DIR="$TTT_SHADER_CHECK_DIR/default" \
  "$TTT_SHADER_CHECK_DIR/check" "$TTT_SHADER_CHECK_DIR/shader.spv" \
  > "$TTT_SHADER_CHECK_DIR/default-cold.json"
GALLIVM_PERF=nopt MESA_SHADER_CACHE_DIR="$TTT_SHADER_CHECK_DIR/nopt" \
  "$TTT_SHADER_CHECK_DIR/check" "$TTT_SHADER_CHECK_DIR/shader.spv" \
  > "$TTT_SHADER_CHECK_DIR/nopt-cold.json"
GALLIVM_PERF=nopt MESA_SHADER_CACHE_DIR="$TTT_SHADER_CHECK_DIR/nopt" \
  "$TTT_SHADER_CHECK_DIR/check" "$TTT_SHADER_CHECK_DIR/shader.spv" \
  > "$TTT_SHADER_CHECK_DIR/nopt-warm.json"
GALLIVM_PERF= MESA_SHADER_CACHE_DIR="$TTT_SHADER_CHECK_DIR/default" \
  "$TTT_SHADER_CHECK_DIR/check" "$TTT_SHADER_CHECK_DIR/shader.spv" \
  > "$TTT_SHADER_CHECK_DIR/default-warm.json"
```

A failed check returns a nonzero exit status and can leave partial output;
require successful exit and `all_results_correct: true`. Keep the output
directory outside the source tree. Use new directories for cold-cache trials
and preserve them for the corresponding warm-cache runs. Do not delete a live
browser's shader cache for this test.

Pipeline creation and first dispatch are measured separately because shader
compilation can occur during dispatch. First-dispatch time includes execution;
it is not an isolated compiler timer. Repeated-dispatch timing includes queue
submission and waiting. Peak RSS is for this standalone process on Linux,
not the whole browser container.

One run on the validated software renderer produced:

| Mode and cache | First dispatches, total | Repeated dispatches, total | Peak RSS |
|---|---:|---:|---:|
| Default, cold | 171.02 ms | 14.58 ms | 81.41 MiB |
| `nopt`, cold | 120.69 ms | 17.52 ms | 78.33 MiB |
| `nopt`, warm | 3.75 ms | 17.41 ms | 66.05 MiB |
| Default, warm | 3.41 ms | 14.40 ms | 66.16 MiB |

All outputs matched in all four processes. These short measurements are not
a statistical performance study. They show a compilation/execution tradeoff
in this workload and justify separate native-browser measurements. Browser
validation must include cold and warm renderer caches, native screenshots,
canvas, workers, WebGL, external HTTPS, whole-container memory and CPU usage.
Keep guest state and resource limits explicit when comparing runs.

## First native-browser trial

The first native run with `nopt` did not meet the browser acceptance criteria.
On the experimental four-core host profile with a 6.5 GiB container limit,
2 GiB guest RAM, two application CPUs and a 1 GiB translation cache, Safari
foreground identity was verified at 248.7 seconds after launch. This confirms
application identity, not loaded page content.

Two optional address-bar navigation attempts stopped before pasting a URL or
pressing Return: one received a truncated accessibility tree without the URL
field identifier; the other received a native accessibility error. A separate
native URL open then rendered the local fixture. Its canvas checksum matched
the baseline, and WebGL reported no error or lost context, but both dedicated
and shared workers exceeded their 15-second response deadlines. No new external
acceptance navigation was requested after this failure. Native diagnostics also
recorded a background PosterBoard launch watchdog termination during startup;
there was no newly listed Safari or WebContent crash report.

The 900-second resource recording completed without container memory-limit
events, host/container OOM kills or CPU throttling. It covered startup, the
failed navigation attempts and the beginning of the explicit local URL open;
the local fixture results arrived after that window. Navigation paths, timing,
persisted tab state and compiler-cache keys differed from the preceding default
run, so its resource totals are not a comparison of equivalent browsing work.

Keep the default compiler configuration. This result does not isolate the cause
of the worker or accessibility failures, and the standalone compute timings
do not establish a browser startup or memory improvement. A future experiment
needs a controlled default comparison and successful browser checks before
progressing to a warm-cache performance comparison.

A subsequent cold run with the default compiler verified Safari foreground
identity at 257.7 seconds. Both local workers answered within their original
deadline, in 3,446 and 3,499 milliseconds, and canvas and WebGL checks passed.
The first post-callback screenshot still had incomplete text; a later read-only
capture at 166.1 seconds after navigation submission showed the complete local
fixture. The controller preserved the active tab and saved-tab count. This is a
bounded recovery check. Persisted guest state and controller code differed, so
it does not isolate the cause of the earlier failures or establish a performance
improvement.
