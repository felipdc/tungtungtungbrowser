# Native Graviton investigation

Graviton remains an experimental host option. Native builds and a local Safari
fixture have run on an eight-core, eight-vCPU, 32 GiB `m9g.2xlarge` host. There is
no qualifying four-phone Shopee result or measured browser speed advantage yet.

The target comparison uses four simultaneous phones, eight distinct normal
Shopee product URLs per phone, at least five successful products on every phone,
and per-phone nearest-rank p95 successful capture time at most 75 seconds.
Original host and native screenshots must show the product title, price and main
image alongside validated product data. Login pages, errors, timeouts and
unattempted requests remain failures. Browser traffic uses the configured proxy;
other device traffic connects directly.

## What has been checked

On 2026-11-17, the native Lavapipe selector passed five tests and `vulkaninfo`
reported a CPU Vulkan device. The runtime image built natively for ARM64 with
Mesa 26.1.6. All 52 native ARM memory-access checks passed. The experimental QEMU
source and retained changes used for AMD tests also built natively for ARM64.
A 644-form numerical fixture across 22 FPCR settings matched the retained golden
output with the experimental FP switch both off and on.

That FP switch does not currently enable the custom scalar FP backend on ARM
hosts. The pinned implementation enables it in the x86 TCG backend and
synchronizes x86 MXCSR; its ARM backend retains the software fallback for those
operations. A native ARM scalar FP backend is a possible future optimization,
requiring exception, rounding and state-restoration checks as well as browser
measurements. The numerical fixture results alone do not measure browser speed.

## Local browser findings

The first disposable guest failed before boot because its renderer cache was
on a read-only root. With the normal writable cache mount, a fresh guest reached
Safari readiness in 173.34 seconds and returned main-thread, dedicated-worker
and shared-worker observations. Its WebGL context was lost and the console
recorded GPU stalls. This older parent had a 1179×2556 framebuffer at scale 3.

A separate fresh clone used a 393×852 framebuffer and a scale-1 LLB setting.
Its WebGL query returned without an error or lost context, and original host and
native images showed the local document. The cached UIKit scale remained 3,
however, producing a 131×284 logical viewport, clipped content and malformed
browser-toolbar layout. It does not qualify correct rendering or product
capture. Both guests shut down cleanly; parent file metadata was unchanged.
The full parent disk was not rehashed in these compatibility checks.

These observations motivate testing the same prepared scale-1 parents used on
AMD. Changing framebuffer size alone is insufficient: verify `device.screen`
and both original screenshots. Keep numerical checks, local compatibility,
product correctness and matched performance results separate.

## Four-phone startup with matched parents

A later run on an eight-core, eight-vCPU, 32 GiB `m8g.2xlarge` host used the
prepared scale-1 parents from the AMD experiments. All four phones reached
native Safari foreground readiness with three application CPUs each. They
started serially and remained running as the next phone booted. Time from each
phone's own QEMU launch to readiness was 506.35, 466.42, 651.00 and 806.27 seconds.
These are startup times, not product-load times or an isolated CPU comparison.

All four native screen checks reported 393×852 at scale 1. Paired native and
host images from the first phone showed correctly sized browser chrome but
black restored-page content; its console recorded graphics detach stalls.
Correct screen geometry therefore does not establish correct page rendering.

The cohort passed its settings/resource checks, then failed before any product
request because a routing helper was absent from the ARM host. All 32 scheduled
products count as failures. All four guests shut down cleanly and the host was
stopped. The next harness bundles the original helper and verifies its hash and
all planned network allocations before creating phone state.

A 15-second profile of the first phone's black-page state collected 773 samples
without reported loss. Translation lookup was the largest named helper;
anonymous translated code limits attribution. This is not a successful Shopee
workload and does not establish whether an ARM floating-point port would improve
product loading. That decision still requires workload measurements.

The public capture profile continues to use three application CPUs per phone.
The prospective two-CPU experiment on AMD has not yet qualified a default
change. Native ARM driver discovery is covered by public tests; full Graviton
browser support remains unqualified.
