# Experimental native navigation keys

The optional keyboard helper preallocates each chord and dispatches its press
and release events without a voluntary sleep while keys are held. It waits
20 milliseconds after releasing every key. This avoids extending a deliberate
key hold when the emulated guest is busy; scheduling can still interrupt the
sequence. Each receipt includes the actual dispatch timestamps.

Each event requests `kIOHIDKeyboardEventOptionsNoKeyRepeat`. These navigation
operations intend one chord even when scheduling delays its release. The option
does not acknowledge Safari's handling or make an uncertain result safe to retry.

Supported keys are `cmd+a`, `cmd+l`, `cmd+t`, `cmd+w` and `return`. Configuring
this helper rejects other keys before input. It does not change the default
daemon route for unconfigured guests. Failed or uncertain helper calls never
repeat input or fall back to the daemon.

## Build and install

Use Linux with `clang-19`, `ld64.lld-19`, `ldid` and Python 3.11 or newer.
Executable paths can be supplied through `TTT_KEYBOARD_CLANG`,
`TTT_KEYBOARD_LINKER` and `TTT_KEYBOARD_LDID`. The project supplies its linker
stub and entitlements; no Apple SDK is required.

```sh
bash scripts/build_keyboard_helper.sh /path/to/private-workspace/keyboard-build
python3 scripts/install_keyboard_helper.py /path/to/private-workspace/keyboard-build \
  --output /path/to/private-workspace/keyboard-install
```

Both output directories must be new and outside this checkout. Run installation
where the guest's native VSOCK endpoint is reachable. The installer verifies
source hashes, the arm64 executable and its digest, uploads to a unique staging
path, verifies readback, and moves it to a path containing its SHA-256. Existing
installations are not overwritten. Its native check resolves symbols and creates
the HID client without sending input. Failure evidence and staging files remain
available for inspection. Select an existing installation by hash rather than
reinstalling it before each key.

The helper requires platform and HID dispatch entitlements supported by the
tested custom firmware.

## Select the helper

```python
import json
from pathlib import Path
from tungtungtungbrowser import Browser
from tungtungtungbrowser.guest import Guest

installation = json.loads(Path('/path/to/private-workspace/keyboard-install/install.json').read_text())
assert installation['status'] == 'complete'
guest = Guest(keyboard_helper_sha256=installation['helper_sha256'])
browser = Browser(guest=guest)
```

Pass this browser to `InspectorSession` to use the helper for session creation,
navigation and cleanup. Tab reuse still needs the separately installed
[native focus reader](native-focus.md). Keyboard configuration is currently
available through the Python `Guest` constructor.

A successful result establishes that all expected native dispatch calls
returned, including releases. `application_consumption_verified` remains false:
the HID dispatch API supplies no Safari consumption acknowledgement. Continue
to verify native focus, the expected URL and the resulting page or tab state.
This helper does not make an uncertain close safe to repeat.

## Targeted validation

Host tests check chord ordering, allocation failures before input, release
before the post-release wait, interrupted waits, strict result validation and
the absence of replay or fallback. These tests do not establish iOS behavior.

In trial 146, the native candidate created one owned fixture tab, navigated it,
produced visible native screenshots, and closed only that tab. A separate HID
monitor recorded the exact 22 expected events. All six pre-existing tabs
remained through the 60-second observation. Two of those tabs came from an
earlier failed daemon bootstrap and remained unowned; that original failure
was not converted into a successful session.

In fresh trial 147, the same binary created exactly one owned tab. The next
native focus query reached its alarm before navigation input, so the cold
case failed and the full workflow was not run. The monitor recorded exactly
eight events for the requested `cmd+t` and `cmd+l`. Independent checks found
both accessibility switches restored and no remaining focus helper processes.
The public source rebuilt to the same native binary and its installer passed
the no-input native check. A separate warm recovery through the public Python
configuration verified the exact owned URL and closed that tab once. Its HID
recording contained the eight expected events, and all four original tabs
remained through 60 seconds. These observations support the bounded keyboard
implementation; they do not establish the cause of earlier duplicate tab
effects or resolve cold focus reliability.

## Preservation failure with the native helper (2026-11-11, trial 162)

A later cold session created one owned tab, then stopped before navigation
input because native address discovery found no control. Separate cleanup
verified the exact owned URL through the published Python focus path and sent
one acknowledged `cmd+w` using the same optional native keyboard helper.
The owned tab disappeared, but a pre-existing tab also disappeared and a new
blank tab appeared. Independent database copies confirmed this after the
Inspector listing first reported the change; later copies retained the loss.
No second close or other recovery input was sent.

The bounded HID observer completed without overflow and recorded exactly eight
events: one `cmd+l` chord and one `cmd+w` chord, including all releases. The
close key's release dispatch call took 0.530 seconds. This evidence does not
establish a repeat or duplicate-consumption cause: the observer boundary does
not report Safari's handling. Both accessibility settings were subsequently
confirmed restored, with no remaining focus helpers.

The earlier successful preservation checks remain valid for their runs, but
this failure leaves cleanup reliability unresolved with the native helper too.
The experimental focus changes used in this run remain unpublished. Preserve
original failures and verify settled tab state; a successful dispatch receipt
alone cannot establish that only the owned tab closed.

## Native no-repeat option validation (trial 163)

A no-input native probe allocated the four close-chord events with options zero
and with the no-repeat bit. It read their flags after construction, after the
existing integer-field setting and after setting the sender. All four candidate
events retained the bit through every step. The probe created no dispatch client
and sent no events. Apple's [event option definition](https://github.com/apple-oss-distributions/IOHIDFamily/blob/777ccd9698845aadf711e32d843c8c9b777431d9/IOHIDFamily/IOHIDEventServiceTypes.h)
and [keyboard filter](https://github.com/apple-oss-distributions/IOHIDFamily/blob/777ccd9698845aadf711e32d843c8c9b777431d9/IOHIDEventSystemPlugIns/IOHIDKeyboardFilter.mm)
describe this option and its repeat-processing check. Those sources do not
establish the guest's complete injected-event route or Safari consumption.

The same candidate binary then closed one owned fixture tab after the published
Python focus path verified its editing URL. Native screenshots showed the
fixture, the HID observer recorded the expected address and close chords, and
all original tabs remained through 60 seconds. Creation and focus used the
previous keyboard binary; only the close chord used the candidate. Focus was
checked after fixture rendering, native captures and a 15-second observation,
so this case does not qualify immediate cold navigation focus.

A later unchanged control on the same guest and Safari process also passed its
fixture captures, close and 60-second preservation checks. The observation
connection interruption left a substantial warm interval between these cases.
This comparison therefore demonstrates neither a repeat-triggered failure nor
candidate superiority.

This change requests the documented behavior for one-shot keys. It does not
establish the cause of trial 162's tab loss or resolve intermittent cleanup
reliability. The existing no-replay policy and tab-preservation checks remain
necessary. The earlier held-key tab-creation comparison was inconclusive because
both the unchanged control and the candidate created one tab.
