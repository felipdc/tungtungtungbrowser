# Experimental calibrated native taps

The optional native touch helper sends a five-point, zero-pressure contact
through iOS IOKit and clears its radii on release. It supports the tested
393 × 852 point, scale-3 portrait display. It fails before input for other
geometry, rotation, a locked screen or a blanked display. It does not change
the device's display, browser properties, fingerprint fields or page code.

This addresses HID contact size and release dimensions. The separate
[framework repair](touch-repair.md) addresses the native touch field mapping.
Installing the helper alone does not correct that framework defect. The default
`Browser.tap` still uses the pinned daemon's native touch sequence; enabling the
helper is explicit, and a failed helper request never falls back to that sequence.

## Build and install

The Linux build needs LLVM's `clang-19`, `ld64.lld-19`, Python 3.11 or newer,
and `ldid`. The script accepts executable paths through `TTT_TOUCH_CLANG`,
`TTT_TOUCH_LINKER` and `TTT_TOUCH_LDID`. It uses a project-authored linker stub
and entitlement list; it does not require or distribute an Apple SDK. Keep
tool binaries and generated artifacts outside this checkout. Review each
tool's own license before redistributing it.

```sh
export TTT_TOUCH_BUILD_DIR=/path/to/private-workspace/touch-build
bash scripts/build_touch_helper.sh "$TTT_TOUCH_BUILD_DIR"

# Run where the guest's native VSOCK endpoint is reachable.
python3 scripts/install_touch_helper.py "$TTT_TOUCH_BUILD_DIR" \
  --output /path/to/private-workspace/touch-install
```

Both output directories must be new, outside the repository, with existing
parent directories. The build produces `touch` and `build.json`, recording
source and binary hashes. The installer requires matching source hashes for
this checkout and a matching arm64 Mach-O executable. This verifies the
local build record, not a third-party binary's provenance.

The installer uploads to a unique staging path, reads back and hashes the
binary, sets its mode to `700`, and moves it to a path containing its SHA-256.
The pinned daemon's native move operation refuses an existing destination.
It then invokes the helper's no-input `check` command. This checks native
symbols and client creation; it does not establish digitizer registration or
input delivery. The installer sends no touch events and changes no frameworks.

`install.json` records every request before sending it and retains failures.
An uncertain transport result is not retried. Staging files and prior
installations are preserved. An existing installation is selected by its hash;
installation is not an operation to repeat before every tap.

The helper needs the tested custom firmware's support for its platform and
HID dispatch entitlements. An ordinary physical iPhone does not acquire those
capabilities by installing this executable.

## Select the helper

Use the `helper_sha256` from the completed installation report:

```python
import json
from pathlib import Path
from tungtungtungbrowser import Browser

installation = json.loads(Path('/path/to/private-workspace/touch-install/install.json').read_text())
assert installation['status'] == 'complete'
browser = Browser(touch_helper_sha256=installation['helper_sha256'])
result = browser.tap(196, 105)
```

The corresponding CLI option is global:

```sh
python3 -m tungtungtungbrowser --touch-helper-sha256 "$TTT_TOUCH_HELPER_SHA256" tap 196 105
```

Coordinates are native screen points. The backend reads `device.screen`,
requires the tested unlocked portrait geometry, validates the point, and
converts coordinates and radius to normalized digitizer units. It sends one
helper invocation with a 50 ms requested hold. Geometry observation and input
are sequential, not an atomic transaction; keep one controller per guest and
do not rotate the device during an action.

The helper allocates both complete events before dispatching the press. It
timestamps each parent and finger immediately before that phase's dispatch,
sets both contact radii to zero for release, and releases its native objects.
An interrupted sleep still sends release and returns an error. A killed or
crashed process can still leave delivery uncertain; no request is repeated to
hide that uncertainty. The IOKit dispatch function has no page-delivery
acknowledgement, so a successful result retains `page_result_verified: false`.

## Execution boundary

The pinned daemon's direct process endpoint only permits a few diagnostic
programs. This backend therefore uses a fixed `proc.run` expression through
the daemon's administrative JavaScriptCore session to launch the native helper.
That session is a separate process outside Safari. Its source contains only
the fixed helper path and JSON-encoded arguments; it receives no caller script,
browser object or page execution context. The public `Guest.call` interface
continues to reject arbitrary `script.eval` calls.

The upstream [session API](https://github.com/Lakr233/vphone-cli/blob/2.14.2/Research/vphoned_http_api.md)
and [session implementation](https://github.com/Lakr233/vphone-cli/blob/2.14.2/VPhoneDaemon/Daemon/GuestAPI%2BScript.swift)
describe this process boundary and its timeout behavior. The native event
construction follows the audited pinned
[IOKit input path](https://github.com/owngoal-dev/icli/blob/2fbdb5ee61d8c320732deb9768b22506f5e3d411/Sources/IcliPrivate/IcliPrivate.m),
with explicit contact radii and release handling.

## Validation

Host tests check geometry rejection, the exact native request, malformed
responses, interrupted holds, allocation failures before input, and preservation
of transport failures without retries. The C test substitutes native functions
to check event ordering and fields; it is not an iOS compatibility test.

A live check built the helper on Linux, installed it with byte-for-byte
readback verification, and passed its native no-input check. After the guarded
framework repair and a clean reboot, the complete framework hash still matched
and the System volume was read-only. Safari rendered the local reference canvas
and returned both worker observations without a WebGL error or lost context.

One Python tap and one CLI tap then each produced a trusted click callback.
Both reported touch radii `5 / 5`, force `0`, pointer-down dimensions `10 × 10`
with pressure `0`, and pointer-up dimensions `0 × 0` with pressure `0`. All four
property-reading paths agreed, and the native screenshots showed the button
receiving the touch. Each recorded contact lasted 60 ms for a requested 50 ms
hold. Total request time was about 4.22 seconds for Python and 5.37 seconds for
the CLI, including geometry and process work; these are observations, not a
throughput benchmark.

The first CLI test loaded an older host checkout and failed argument parsing
before input. That failed attempt was retained. A separate CLI-only check
verified the candidate module's path, used Python's safe-path mode, and passed;
the successful Python tap was not repeated. When testing multiple checkouts,
verify which module `python -m` imports rather than assuming `PYTHONPATH` takes
precedence over the working directory.

Live validation must load the local diagnostic page, invoke `Browser.tap` on
its visible button, retain the callback and native screenshot, and run
`scripts/check_touch_probe.py` with `--radius 5 --require-property-reads`.
Record the installed framework hash separately. An accepted native command
alone does not establish trusted page events or correct touch properties.

The helper does not yet support calibrated swipes, drags, multitouch, stylus
input, configurable pressure, other orientations or other display geometry.
It makes no claim of complete physical-device equivalence.
