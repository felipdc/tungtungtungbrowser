# Experimental lavapipe memory reclamation

`patches/lavapipe-release-backing.patch` releases backing pages when Mesa's
llvmpipe allocator frees an ordinary allocation. It is an opt-in experiment;
the default runtime image and build script do not apply it.

In Mesa 25.0.7, ordinary allocations occupy ranges of one shared `memfd`.
The free path unmaps the allocation and returns its range to the allocator,
but leaves its backing pages in the file. Those pages can remain resident
until reused or the driver closes the file.

The patch uses `fallocate(PUNCH_HOLE | KEEP_SIZE)` before returning the range
to the free list. This ordering matters: once the range is returned, another
thread may allocate and write it. Reclaiming it afterwards could discard that
new allocation. An interrupted call is retried; an unsupported operation falls
back to the existing behavior. Imported host memory and exported/imported file
descriptors use separate free paths and are unchanged. See the
[Linux operation semantics](https://man7.org/linux/man-pages/man2/fallocate.2.html)
and [Vulkan memory lifetime requirements](https://docs.vulkan.org/refpages/latest/refpages/source/vkFreeMemory.html).

## Validation and limits

A standalone Vulkan test passed with Debian 13, LLVM 19.1.7 and three drivers
inside the same CPU-only runtime image:

| Driver | Backing before freeing 64 MiB | Backing after free |
| --- | ---: | ---: |
| Installed Mesa 25.0.7-2+deb13u1 | 192 MiB | 192 MiB |
| Rebuilt source, without this patch | 192 MiB | 192 MiB |
| Same build, with this patch | 192 MiB | 128 MiB |

All three passed a Vulkan buffer-fill/readback check, integrity checks on
neighboring allocations, 160 allocation/reuse cycles across four threads,
and checks that freeing an original exported allocation preserves its imported
owner's contents and that freeing a host-pointer import preserves its caller's
storage. The test checks allocated file blocks, not the file's logical length.

This establishes reclamation for the tested allocations. It does not establish
a percentage reduction in whole-browser memory, faster startup, or long-session
stability. Live graphics allocations still require their memory. Keep the
default runtime available for comparison and rollback.

A subsequent native guest experiment loaded the verified patched library and
showed matching allocated backing-file bytes and mapped resident bytes in two
snapshots. Its cold Safari launch response nevertheless exceeded the 120-second
limit. A later read-only foreground check found Safari without repeating the
launch; subsequent warm canvas, WebGL and worker checks passed. The canvas hash
matched the earlier driver, and the workers responded in 7.1 and 7.2 seconds.
The external-page acceptance check failed to retain the requested content.

That 15-minute diagnostic recording peaked at 6.26 GiB of sampled container
memory, with no memory-limit events, OOM kills or CPU throttling. Its workload
included a failed startup observation and an unsuccessful external navigation;
it is not a like-for-like successful-browsing comparison.

A follow-up native control loaded the rebuilt **unpatched** library, made with
the same build flags. It reached Safari foreground verification in 225.7 seconds;
its first image still showed a loading page. The local canvas hash matched and
both workers responded within 8.9 seconds. The external page's expected content
was visible at 92.5 seconds after navigation and retained at 212.7 and 332.8
seconds. Its 15-minute recording peaked at 6.01 GiB; the separate seven-minute
external-page recording peaked at 6.37 GiB and averaged 1.92 CPU cores. Neither
recording had memory-limit events, OOM kills or CPU throttling.

The control's final graphics snapshot had 149 MiB more allocated backing than
mapped resident bytes; the patched trial's two snapshots had no such difference.
These non-atomic snapshots support the backing-retention finding. They do not
establish whole-browser savings: the trials kept persistent browsing state,
restored different active pages at startup, and had different external-page
outcomes. Matching the subsequent navigation times does not remove those
limitations or establish that the patch caused the failed trial. Repeatable
startup and successful external-page checks with the patched driver remain
required before adopting it as a default.

## Released pipeline cache experiment

The pinned renderer's
[`kept.rs`](https://github.com/YahiaAngelo/metal2vulkan/blob/cd1a08ae5442065a11af0fb79d1bdc35c1f0e1b9/crates/metal2vulkan-runtime/src/pipeline/kept.rs)
retains up to 256 released pipeline **variant instances** for reuse. One instance
can hold multiple Vulkan pipelines. `M2V_KEPT_PIPELINES` accepts a positive
integer at first use; zero falls back to 256. A smaller limit may release
compiler objects sooner, but can require more compilation when states recur.
It does not shrink guest RAM or QEMU's translation cache.

A subsequent trial set this limit to 64 with the rebuilt unpatched driver,
the same runtime image, 2 GiB guest RAM, 1 GiB translation cache, four host CPU
quota and 6.5 GiB container limit. Native Safari foreground verification took
229.6 seconds. Local canvas, WebGL and both worker checks passed. The external
page's expected content was visible at 93.0 seconds after navigation and retained
at 213.2 and 333.4 seconds.

The 15-minute recording peaked at 6.06 GiB. The separate seven-minute browsing
recording peaked at 6.48 GiB and averaged 1.87 CPU cores, with no memory-limit
events, OOM kills or CPU throttling. The earlier 256-instance control peaked at
6.37 GiB during browsing. The trials retained persistent session state and
restored different active pages, so these numbers do not isolate the cache's
effect. They provide no demonstrated whole-container memory or startup gain.
The default cache limit remains unchanged.

## Reproducing the build

Build outside this repository, in an environment whose runtime libraries match
the container that will load the driver. Mesa detects optional dependencies;
for example, installing XCB keysyms development files can introduce a runtime
dependency absent from an existing container. Check loading inside that
container, not only with `ldd` on the build host.

The tested source inputs are:

| Source | SHA-256 |
| --- | --- |
| [Mesa 25.0.7](https://archive.mesa3d.org/mesa-25.0.7.tar.xz) | `592272df3cf01e85e7db300c449df5061092574d099da275d19e97ef0510f8a6` |
| [Debian 25.0.7-2+deb13u1 patches](https://deb.debian.org/debian/pool/main/m/mesa/mesa_25.0.7-2+deb13u1.debian.tar.xz) | `7ea51db6f4c6a39d28269975b7f652e37333a283819557c5901cbd3eccee446a` |

Verify the archives, unpack the Debian archive inside the Mesa source directory,
then apply the files listed in `debian/patches/series` in order with `patch -p1`.
Retain those distribution patches in both builds. The comparison above used
GCC 14.2 and the following configuration, with LLVM 19's binaries first in `PATH`:

```sh
meson setup build mesa-25.0.7 --buildtype=release --wrap-mode=nodownload \
  -Db_ndebug=true -Db_lto=false -Dplatforms=x11,wayland \
  -Dvulkan-drivers=swrast -Dgallium-drivers=llvmpipe \
  -Dopengl=false -Dgles1=disabled -Dgles2=disabled -Dglx=disabled \
  -Degl=disabled -Dgbm=disabled -Dgallium-xa=disabled \
  -Dgallium-vdpau=disabled -Dgallium-va=disabled \
  -Dllvm=enabled -Dshared-llvm=enabled -Dvalgrind=disabled \
  -Dbuild-tests=false
ninja -C build -j2 src/gallium/targets/lavapipe/libvulkan_lvp.so
cp build/src/gallium/targets/lavapipe/libvulkan_lvp.so baseline-libvulkan_lvp.so
```

The required development packages include Meson, Ninja, a C/C++ compiler,
LLVM 19, Mako, PyYAML, packaging, Bison, Flex, glslang, and the development
libraries for DRM, Expat, zlib, zstd, ELF, udev, Wayland and the selected X11/XCB
platforms. Follow [Mesa's build instructions](https://docs.mesa3d.org/meson.html)
and resolve the configuration checks before compiling. Vulkan headers and the
loader development library are also needed for the regression test.

Apply this repository's patch to the source, then rebuild the same target:

```sh
patch --batch --fuzz=0 -p1 -d mesa-25.0.7 \
  -i /path/to/repository/patches/lavapipe-release-backing.patch
ninja -C build -j2 src/gallium/targets/lavapipe/libvulkan_lvp.so
cp build/src/gallium/targets/lavapipe/libvulkan_lvp.so candidate-libvulkan_lvp.so
```

Keep both libraries, the source archives,
the compiler and dependency versions, and the build configuration outside the
repository. Do not install either library over the system driver to run the test.

## Running the regression test

Compile `native/check_lavapipe_reclaim.c` with the Vulkan loader and pthreads:

```sh
cc -O2 -Wall -Wextra -Werror -pthread /path/to/repository/native/check_lavapipe_reclaim.c \
  -lvulkan -o /path/to/external/check_lavapipe_reclaim
```

Copy the installed lavapipe ICD manifest to an external directory for each
rebuilt driver and change only `ICD.library_path` to the absolute path of that
library. Manifest names vary by distribution; discover the installed
`lvp_icd*.json` instead of assuming an architecture suffix. Paths in a manifest
must resolve inside the environment running the test.

```sh
VK_DRIVER_FILES=/path/to/installed/lvp_icd.json LP_NUM_THREADS=1 \
  MESA_SHADER_CACHE_DISABLE=true /path/to/external/check_lavapipe_reclaim retained
VK_DRIVER_FILES=/path/to/external/baseline-icd.json LP_NUM_THREADS=1 \
  MESA_SHADER_CACHE_DISABLE=true /path/to/external/check_lavapipe_reclaim retained
VK_DRIVER_FILES=/path/to/external/candidate-icd.json LP_NUM_THREADS=1 \
  MESA_SHADER_CACHE_DISABLE=true /path/to/external/check_lavapipe_reclaim released
```

Each invocation must exit zero. The program requires one CPU lavapipe device
and the external host-memory and file-descriptor extensions. An incompatible
driver, missing dependency, unexpected retention behavior, or integrity failure
is an error rather than a skipped pass. The test intentionally depends on this
Mesa allocator's Linux `memfd` behavior; it is not a general Vulkan conformance
test and does not require a running iPhone.
