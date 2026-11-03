# Profiling translated guest code

`scripts/map_jit_samples.py` maps Linux `perf` instruction-pointer samples to
QEMU's timestamped JIT code records. This helps identify which guest code is
consuming host CPU when a normal profile mostly shows anonymous translated
instructions. It does not change the guest or browser.

The reader targets the pinned runtime's `-jitdump` format: version 1,
little-endian records, a 40-byte header and the monotonic clock. QEMU serializes
code-load records before executing the generated code. See the pinned
[QEMU writer](https://github.com/YahiaAngelo/qemu-vphone/blob/4da65a96e039413faff00ed52e3d956bf931bfd9/tcg/perf.c).
Code-move records and unsupported clocks/formats are rejected.

## Enable recording for a diagnostic boot

Stop the guest cleanly before changing its launcher. Create a private output
directory inside the external VM bundle and a wrapper that adds `-jitdump`:

```sh
install -d -m 700 "$TTT_VM_DIR/profiles/run-001"
cat > "$TTT_VM_DIR/profiles/qemu-jit.sh" <<'SH'
#!/bin/sh
set -eu
cd /vm/profiles/run-001
exec /runtime/qemu/build/qemu-system-aarch64 "$@" -jitdump
SH
chmod 700 "$TTT_VM_DIR/profiles/qemu-jit.sh"
cat > "$TTT_VM_DIR/profiles/compose.profile.yaml" <<'YAML'
services:
  iphone:
    environment:
      QEMU: /vm/profiles/qemu-jit.sh
YAML
```

Add `-f "$TTT_VM_DIR/profiles/compose.profile.yaml"` to your existing Compose
file list when starting the diagnostic guest. Keep your other configuration,
including any proxy override. For example, without additional overrides:

```sh
docker compose -f runtime/compose.yaml \
  -f "$TTT_VM_DIR/profiles/compose.profile.yaml" up -d --no-deps iphone
```

Use a fresh directory for each run. The dump contains generated machine code
and can grow by gigabytes during startup. Keep it outside the repository,
restrict access, and monitor free space. Recording adds overhead: use this
boot for diagnosis and measure performance separately without `-jitdump`.

## Capture a short interval

Run `perf` on the Linux host, with the permissions your host requires.
Set `QEMU_HOST_PID` to the main QEMU process's **host PID**, not the container
init, a renderer child, or a virtual CPU's thread ID. Inspect the container's
processes with `docker top <container> -eo pid,ppid,comm,args`; verify the
selected process's executable and its `NSpid` field in `/proc/PID/status`.
The innermost namespace PID should match `launch.json` in the VM bundle.
Record that identity and the launch clock with your experiment.

Set `TTT_PROFILE_DIR` to a private host directory outside the source tree:

```sh
umask 077
mkdir -p "${TTT_PROFILE_DIR:?Set a private profile directory}"
perf record -e cpu-clock:u -F 19 --clockid mono \
  -p "${QEMU_HOST_PID:?Set the verified host QEMU PID}" \
  -o "$TTT_PROFILE_DIR/perf.data" -- sleep 30
perf script --ns -F pid,tid,time,ip \
  -i "$TTT_PROFILE_DIR/perf.data" > "$TTT_PROFILE_DIR/samples.txt"
```

This samples host user-space CPU time. It includes translated guest kernel
code, but excludes host kernel CPU time. Continue measuring whole-container
CPU, memory and OOM events with `sample_resources.py`.

## Map the samples

Set `TTT_JIT_DUMP` to the host path of `jit-PID.dump` under the diagnostic
directory. The filename and header use QEMU's PID in its own namespace, which
can differ from `QEMU_HOST_PID`.

```sh
python3 scripts/map_jit_samples.py \
  "${TTT_JIT_DUMP:?Set the matching jitdump path}" \
  "$TTT_PROFILE_DIR/samples.txt" \
  --host-pid "$QEMU_HOST_PID" --live \
  --output "$TTT_PROFILE_DIR/mapping.json"
```

`--host-pid` excludes inherited child-process samples before mapping. Use
samples and a dump from the same QEMU lifetime and host monotonic clock.
The reader rejects samples older than the dump; that check cannot establish
the producer's identity on its own.

With `--live`, the reader requires a complete serialized JIT record newer
than the last selected sample. This establishes that earlier relevant loads
have left the writer's buffer. It then ignores the later tail, which may
still be growing. If the barrier is missing or incomplete, wait for more
records or stop the guest cleanly and analyze the completed dump without
`--live`. A parser error is not a reason to restart the VM.

Without `--live`, verify separately that the producer has exited and flushed
the file. A file ending at a record boundary does not prove that a live
writer has flushed everything. Incomplete records fail analysis.

The JSON report is created with mode `0600`; an existing output is never
overwritten. It records mapping coverage, excluded samples, per-thread counts
and the top guest blocks. Reused or overlapping host code addresses are
resolved using the latest preceding load that covers the sampled address.

## Interpret the result

A name such as `guest-0x40200000` identifies the **start of a translated
block**, not necessarily the exact sampled guest instruction, a function
name, or a guest process. Establish those separately before attributing a
hotspot. Host thread IDs can be correlated with QMP `query-cpus-fast` after
accounting for PID namespaces.

Unmapped samples can belong to QEMU helpers, renderer code or other native
host instructions. Keep their count in the denominator when reporting
coverage. Do not treat mapped percentages as a whole-container CPU or
memory benchmark. The parser has tests for address reuse, overlap, clock
mismatch, incomplete live records, process filtering and report preservation;
its mapping has also been exercised against a small ARM program on the
pinned QEMU build.

For subsequent performance runs, remove the profiling override from the
Compose file list and restart through the normal clean-shutdown path.
