# Optional native process observer

Use this experimental diagnostic to inspect a known process without enumerating
applications through RunningBoard. It is separate from browser navigation and
is not enabled by runtime creation or the default SDK controller.

The native helper reads `proc_pidinfo`, `proc_pidpath` and the kernel boot UUID.
Its Darwin ABI declarations follow Apple's pinned
[`proc_info.h`](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/sys/proc_info.h).
It checks PID and process-start time before and after reading the executable
path. Missing processes, kernel errors, malformed records, exiting/zombie
states and identity changes remain explicit outcomes. A process can exit after
an observation; no metadata snapshot establishes future liveness.

A live identity does **not** establish foreground state or rendered content.
Retain native foreground checks and screenshots when validating a browser
transition. Compare the executable path as well as PID, start time and boot UUID.

## Build and install

Build on Linux with Clang, the Mach-O LLD linker and `ldid`. No Apple SDK or
firmware is required for the build:

```sh
bash scripts/build_process_observer.sh "$TTT_EXTERNAL_BUILD/process-observer"
python3 scripts/install_process_observer.py \
  "$TTT_EXTERNAL_BUILD/process-observer" \
  --output "$TTT_EVIDENCE_DIR/process-observer-install"
```

Both output directories must be new and outside this repository. The builder
accepts `TTT_PROCESS_OBSERVER_CLANG`, `TTT_PROCESS_OBSERVER_LINKER` and
`TTT_PROCESS_OBSERVER_LDID` overrides. Keep the build manifest with the binary;
the installer verifies its architecture, source hashes and binary digest.

Installation requires a running prepared guest and its native administration
daemon. The installer uploads files through that daemon, verifies their bytes,
renames the staged files without replacing an existing destination, and requests
one service load. It sends no browser or UI input. It then checks the service's
own kernel identity and a nonexistent-PID control. A refused connection may be
polled while the original service load starts; timeouts, malformed replies and
load failures are retained without reloading the service.

The helper listens on VSOCK port 1342 and accepts only the Linux host CID. It
has no IP listener, imports no browser framework and uses no page scripts.
Its commands are health, one numeric PID observation, and stopping itself.
It has a 20-minute guest alarm and `KeepAlive` is disabled. The service plist is
kept outside the guest's automatic boot-service directories. This is an
explicit diagnostic session, not a persistent startup dependency.

## Read a process and preserve its identity

Run the client on the Linux VM host. Set `pid` and `executable` from a native
application observation that you have already verified:

```python
import json
from pathlib import Path
from tungtungtungbrowser.process_observer import ProcessObserver, process_identity

deployment = json.loads(Path("/external/evidence/deploy.json").read_text())
probe = ProcessObserver(cid=3, timeout=5)
boot_uuid = deployment["health"]["boot_uuid"]

observation = probe.observe(pid)
identity = process_identity(
    observation, pid=pid, boot_uuid=boot_uuid, executable=executable
)
# Save observation and identity with the original request and screenshot.
# Require the same identity when checking continuity later in the session.
```

`observe` retains native `absent`, `changed`, `not_alive` and `failed` results.
`process_identity` rejects those results and rejects a different boot, PID,
start-time shape or executable. Compare the returned identity dictionaries to
check continuity; the validator cannot infer an earlier start time for you.
The native `foreground_verified` field must remain false.

The client uses one connection and one request for each observation. It bounds
the complete response to 32 KiB and rejects duplicate JSON fields, non-finite
numbers, partial frames and multiple responses. `ProcessObserverError` retains
the transport phase, partial bytes or native response when available. A timeout
does not trigger reconnection or another request.

Stop the diagnostic helper after collecting evidence:

```python
probe.stop(
    pid=deployment["health"]["pid"],
    boot_uuid=deployment["health"]["boot_uuid"],
)
```

The client checks service identity before requesting stop. An unacknowledged
stop is retained as a failure and is not repeated automatically. A failed
installer also leaves its report and staging files available for inspection;
do not repeat a service load merely because its response timed out.

## Validation scope

Host tests exercise kernel lookup errors, malformed records, missing and
exiting processes, PID reuse, transport failures, response validation and
installation evidence. They do not establish native latency or browser
reliability. Native evaluation must cross-check the initial identity against
the existing inventory, preserve failed operations, and separately verify
foreground state and native captures. The default browser controller is
unchanged while that evaluation remains experimental.

On 2026-11-04, a native trial cross-checked Safari's PID and exact process-start
time against the existing native inventory. That initial direct observation
took 284 ms. The same five-second observation deadline expired after Home;
the original request was not repeated and no Safari return launch was requested.
Home was visible in the native capture, with rendering artifacts, so the failed
observation did not establish that Safari had terminated. No matching Safari
crash report was collected. This does not establish a crash-free session either.

After the original resource recording ended, the same observer process still
had its original boot and process identity. Its health and own-process reads
took about 6.2 and 2.2 ms. That later response does not turn the earlier timeout
into a successful transition. A separate native build/installation check of the
reusable tooling passed executable identity, a nonexistent-PID control and stop
acknowledgment without sending UI input. Local tests alone do not explain why
the earlier request stalled, and the observer remains optional.
