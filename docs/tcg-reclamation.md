# Optional TCG code reclamation

The [reclamation patch](../patches/qemu-tcg-reclaim.patch) releases obsolete
translated-code backing pages after a complete QEMU translation-block flush.
It preserves the virtual cache capacity. This is an experimental Linux option,
disabled by default and omitted from the default runtime build.

The patch targets QEMU revision `14c9913d2231080bd234c69421ed4f293a25f767`.
Apply it to a separate source checkout and rebuild `qemu-system-aarch64` with
the same dependency revisions and other patches as the control. Enable it with
`-accel tcg,thread=multi,tb-size=1024,split-wx=off,tb-reclaim=on`; use
`tb-reclaim=off` for the same-binary control. The option cannot be changed after
machine initialization. Split W^X mappings and unsupported hosts are rejected.

Reclamation runs after the existing safe full-flush barrier and destruction of
translation metadata. It retains pages containing the permanent prologue and
leaves guard pages intact. Failed `madvise` retains the old backing and emits a
warning. The patch also clears the serial execution loop's previous-block
pointer after an inline flush, before it can refer to discarded metadata.
It does not change guest memory, renderer allocations or browser page code.

## Disk-free native check

The [diagnostic](../scripts/check_qemu_tcg_reclaim.py) runs a disposable ARM
program with two active virtual CPUs. Each CPU executes a separate bank of
blocks and reports its checked arithmetic result through four epochs. Every
subsequent epoch must cause a capacity flush, retain cache capacity and produce
the expected result. A third configured CPU remains halted. QMP and GDB use
local Unix sockets; no existing guest disk or network is opened.

Build the small fixture outside this repository with LLVM 19:

```sh
mkdir /tmp/tcg-fixture
llvm-mc-19 -triple=aarch64 -filetype=obj native/check_qemu_tcg_reclaim.S \
  -o /tmp/tcg-fixture/small.o
ld.lld-19 -Ttext=0x40080000 -e _start /tmp/tcg-fixture/small.o \
  -o /tmp/tcg-fixture/small.elf
llvm-nm-19 /tmp/tcg-fixture/small.elf > /tmp/tcg-fixture/small.symbols
python3 scripts/check_qemu_tcg_reclaim.py --qemu /path/to/qemu-system-aarch64 \
  --fixture /tmp/tcg-fixture/small.elf --symbols /tmp/tcg-fixture/small.symbols \
  --output /tmp/tcg-serial-on --cache-mib 4 --thread single --reclaim on
```

Repeat with `--reclaim off` and a new output directory. For the 1024 MiB
multi-threaded check, generate a large fixture by replacing
`.equ BLOCKS, 32768` with `.equ BLOCKS, 2097152`, then assemble and link it in the
same way. Run `--cache-mib 1024 --thread multi --cpus 3` with `default`, `off`
and `on`, each in a new directory. Keep output paths short enough for Unix
sockets. Run these checks separately from browser measurements.

The report retains commands, binary and fixture hashes, per-epoch flush counts,
generated-code capacity and resident executable pages. A completed correctness
check alone does not assert memory savings: compare the same-binary reports.
The 4 MiB serial case specifically exercises inline capacity flushes.

## Measured limits

The candidate passed the four-epoch serial and multi-threaded checks, rejected
split W^X and matched the existing 646-line floating-point baseline with native
FP both enabled and disabled. In the original 1024 MiB synthetic comparison,
default/off code residency stayed around 939–949 MiB; enabled residency varied
from 47 to 650 MiB as the cache refilled. Capacity remained 1,073,618,944 bytes
and all three modes reported the same sampled flush counts. These runs are
correctness and residency evidence, not an isolated throughput benchmark.

The bundled disk-free diagnostic also passed all five cases: serial off/on and
1024 MiB multi-threaded default/off/on. Its enabled multi-threaded samples used
75–677 MiB, versus 944–949 MiB for default/off, with identical sampled flush
counts and capacity. Both serial modes completed 20 flushes and four correct
epochs.

A subsequent four-phone comparison used this same candidate binary with
reclamation on/off/on/off, 1024 MiB caches, 3 GiB guests and 6 GiB whole-container
limits. At one warm snapshot, executable-code PSS was 299/988/565/955 MiB.
The enabled guests had completed 15 and 14 full flushes. These snapshots show
discard taking effect in native guests; their differing workload histories
prevent a causal whole-container or graphics-memory comparison. At the final
snapshot, enabled code PSS had refilled to 782/739 MiB, versus 961/953 MiB for
the controls. All four containers were again near their 6 GiB limits; lower
code residency did not establish a lower whole-container peak.

Cold qualification failed: one enabled and one disabled guest failed native
address discovery, another enabled guest created two tabs after one acknowledged
shortcut, and the other control's private read-only preparation query timed out.
Separate warm recovery does not change those results. The experiment remains
unqualified for reliable four-phone browsing and is not enabled by default.

Three guests subsequently reached readable login responses in separate warm
cases; none returned a valid product response. The disabled control also lost
an original tab during cleanup after one observed close chord. The enabled
guest's separate cleanup and the other control's shortcut recovery preserved
their original tabs through 60 seconds. These outcomes do not establish a
reclamation-related cause or fix for the input failures.

The guests ran concurrently for 32.2 minutes. Sampled phone RAM peaked at
24.00 GiB, or 24.10 GiB including the four proxies; minimum host available RAM
was 5.29 GiB. No OOM occurred. Host swap counters increased by one page in and
zero pages out, and zswap reported zero written-back pages. All four guests
stopped natively without forced termination, with unchanged parent images and
cache. The complete evidence archive was downloaded and verified. This was
a failed qualification run, not sustained browsing validation.
