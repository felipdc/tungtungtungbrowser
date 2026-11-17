# Experimental translation jump-cache size

`patches/qemu-jump-cache-size.patch` exposes the pinned QEMU jump-cache size as
the build-time macro `VPHONE_TB_JMP_CACHE_BITS`. It accepts 12–17 and preserves
the upstream default of 17. The default runtime build does not apply this patch.
It targets QEMU `14c9913d2231080bd234c69421ed4f293a25f767`; retain the other
validated runtime patches and dependency configuration.

A 15-second observation of a ready browser attributed about 17% of 689 host
user-space samples to `tcg_flush_jmp_cache`, with no lost samples. The executable
and its unstripped symbols had the same build ID. This was a startup observation
while another phone booted, not a successful product workload or a CPU-saving
result. The retained frame showed diagonal rendering artifacts over a language
and cookie screen, so this profile does not qualify correct page rendering.

The first candidate uses 14 bits: 16,384 entries instead of 131,072. At 48 bytes
per entry, the entry array falls from 6 MiB to 768 KiB per virtual CPU. Full
clearing visits eight times fewer entries. Additional hash collisions can make
lookups slower; this is a hypothesis to measure, not a predicted net speedup.
The patch retains the existing hashing, page buckets, generations, atomic entry
clearing and translation invalidation paths. It changes neither floating-point
execution nor guest pages.

Apply the patch in an isolated source checkout. Add
`-DVPHONE_TB_JMP_CACHE_BITS=14` to the existing compiler flags when configuring
that build, preserving every other flag. Keep a separately identified 17-bit
control. All objects must be rebuilt consistently after changing this macro;
never mix objects compiled with different cache layouts. Retain the source diff,
effective build flags and binary hashes. This setting cannot change in a running
VM. Do not overwrite a deployed binary.

Before adoption, run the existing exact floating-point fixture with native FP
off and on, serial and multithreaded translation-flush checks, and native
navigation/rendering checks. Then run a fresh four-phone product cohort with
eight distinct normal product URLs per phone, retaining every scheduled failure,
requiring at least five successful captures on each phone and per-phone p95 at
most 75 seconds. Compare CPU cost over successful workloads and review original
host/native images. Native validation is pending. Architecture-independent
source does not establish that an ARM build or browser run has passed.

The disk-free instruction-update check assembles a project-authored fixture,
starts two emulated CPUs, and verifies 256 acknowledged updates to executable
code on both CPUs. Each binary runs with single- and multithreaded TCG; every
case must exit successfully with the fixture's completion marker. The runner
preserves logs, rejects unresolved relocations and bounds each machine to 30
seconds. It never opens an existing VM or guest disk:

```sh
python3 scripts/check_qemu_jump_cache.py \
  --qemu /external/control/qemu-system-aarch64 \
  --qemu /external/candidate/qemu-system-aarch64 \
  --output /external/jump-cache-check
```

The output directory must be new. LLVM 19 assembler, linker and ELF-inspection
tools are required; their paths are configurable. This checks the fixture's
instruction updates, not every address-space alias or ARM memory-ordering case.

The x86-64 14-bit candidate passed the exact 646-line numerical fixture with
native FP both off and on, serial translation-flush checks with reclamation
off/on, and the multithreaded 1 GiB translation-cache check with reclamation off.
Both 17-bit control and 14-bit candidate passed all four instruction-update
cases. Compiler checks accepted the preserved default and 12/14/17-bit values,
and rejected values outside the supported range. These checks establish only
the stated synthetic behavior; browser qualification and CPU benefit remain
pending.

This patch modifies GPL-covered QEMU code; see [third-party notices](../THIRD_PARTY_NOTICES.md).
