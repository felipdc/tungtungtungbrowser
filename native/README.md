# Native diagnostics and workarounds

## Accessibility settings reader

`accessibility_state.c` reads the mobile user's native application-accessibility
and automation switches without enabling them or querying a UI tree. The
[guide](../docs/accessibility-state.md) covers the Linux build, process result,
native observation and limits. It is an optional diagnostic and does not run
as part of browser startup or navigation.

## Renderer allocation log check

`check_graphics_memory.rs` exercises small buffer and image allocations through
metal2vulkan on CPU Lavapipe. It checks completed buffer fill/copy readback and
a neighboring allocation while validating the optional complete-placement log.
The [allocation guide](../docs/graphics-memory.md) covers the external build,
offline analyzer and evidence limits. It does not start an iPhone or qualify
native browser behavior or a smaller memory limit.

## Direct process identity observer

`process_observer.c` is an optional read-only native service for inspecting a
known PID through kernel metadata. It avoids application enumeration, retains
identity changes and errors, and makes no foreground claim. The
[observer guide](../docs/process-observer.md) covers building, verified
installation, the bounded Python client, native results and the unresolved
post-Home timeout. It is not enabled by runtime creation.

## QEMU display trace check

The separate [display trace check](../docs/display-trace.md) uses
`check_qemu_display_trace.c.in` as a synthetic renderer/queue harness for the
actual pinned QEMU display functions. It compares unmodified and instrumented
behavior, including failure paths, under ASan/UBSan. It starts no VM and does
not establish native rendering correctness.

## QEMU wait-instruction check

`check_qemu_wfe.S` is a small AArch64 program for the optional
[QEMU WFE scheduling experiment](../docs/qemu-wfe.md). Its two cores exchange
values with and without an explicit event, then execute wait hints without a
future event. `scripts/check_qemu_wfe.py` builds it and runs bounded disposable
emulator processes, retaining each command and failure. It opens no existing
guest disks. Run it outside browser resource-measurement windows.

## Restore transport workaround

`usbmuxd_send_fix.c` normalizes positive error returns from `usbmuxd_send`
to negative errno. It is preloaded only by the host firmware creation script.
It does not modify Safari, WebKit or the iOS guest.

On the Debian research host, the cloudOS restore reported `Status: Restore
Finished` after unmounting and sealing its filesystems, then spun in the
final `ReceivedFinalStatusMsg` send. A debugger stack showed
`socket_send` → `usbmuxd_send` → `idevice_connection_send`. The
[libusbmuxd implementation](https://github.com/libimobiledevice/libusbmuxd/blob/2.1.0/src/libusbmuxd.c)
negates an already negative socket error; the
[libimobiledevice caller](https://github.com/libimobiledevice/libimobiledevice/blob/1.3.0/src/idevice.c)
recognizes only negative errors and otherwise repeats with zero bytes sent.

The shim preserves successful and already negative returns. Its native check
first sends real bytes through a socket pair, then closes the receiving end
and requires a negative error with zero bytes sent. Run both against the
installed library and with `LD_PRELOAD` to reproduce the difference. A full
iOS 26.6.2 restore completed with the shim enabled on 2026-11-02 at 16:08 UTC;
the host returned `DONE` and resumed at CFW installation. The socket-pair
check separately verifies the disconnect-error path.

## ARM64 Linux memory-access check

The optional [ARM memory check](check_arm_memory.c) probes four instructions
used by the graphics runtime's ARM implementation: `ldar`, `ldapr`, `stlr`, and
`ldsetal`. It checks the Linux ATOMICS, LRCPC and USCAT capability bits, then
checks the returned or stored values at every byte offset from 0 through 12
inside a 16-byte-aligned granule. Each four-byte operation runs in a separate
child with a two-second alarm and core dumps disabled. A fault or wrong result
fails the check; missing required capabilities skip execution and also fail.

On an ARM64 Linux host with a compiler supporting ARMv8.4-A:

```sh
TTT_ARM_CHECK_DIR=$(mktemp -d)
cc -std=c11 -O2 -Wall -Wextra -Werror -march=armv8.4-a \
  native/check_arm_memory.c -o "$TTT_ARM_CHECK_DIR/check"
timeout 30s "$TTT_ARM_CHECK_DIR/check" > "$TTT_ARM_CHECK_DIR/result.json"
```

Require exit status zero, `case_count: 52`, and
`all_values_correct_without_fault: true`. A tool or system-call error can leave
partial output. Keep the result outside the repository. This program uses only
its own anonymous memory and requires no GPU, guest or elevated privileges.

This is a host compatibility probe, not a concurrency test. It does not prove
atomicity under concurrent access, memory ordering, accesses crossing a granule,
guest page-size compatibility, successful runtime compilation, iOS startup or
Safari fidelity. ARM runtime support still requires those separate checks.
