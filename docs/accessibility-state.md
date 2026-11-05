# Observe native accessibility settings without enabling them

The optional [native reader](../native/accessibility_state.c) reports the mobile
user's application-accessibility and automation switches. It resolves only
`_AXSApplicationAccessibilityEnabled` and `_AXSAutomationEnabled` from
`libAccessibility.dylib`. It does not resolve their setters, request a UI tree,
send input or execute JavaScript in Safari.

This distinction matters when investigating startup or control failures. The
pinned [Icli query implementation](https://github.com/owngoal-dev/icli/blob/2fbdb5ee61d8c320732deb9768b22506f5e3d411/Sources/IcliPrivate/Accessibility.m)
can enable both switches when reading a tree. Inspecting the tree first can
therefore change the state being investigated. The switches are system-wide;
their values do not identify which application or client enabled them.

## Build and execute

On Linux, use Clang, the Mach-O LLD linker and `ldid`:

```sh
scripts/build_accessibility_state.sh /external/accessibility-state-build
```

The script requires a new output directory outside this repository. Tool paths
can be supplied through `TTT_ACCESSIBILITY_STATE_CLANG`,
`TTT_ACCESSIBILITY_STATE_LINKER` and `TTT_ACCESSIBILITY_STATE_LDID`. It uses the
same platform-application entitlement as the optional process observer and
records the source, tool names and signed binary hash in `build.json`. It
installs nothing and does not start a guest. The produced Mach-O executable
runs inside the supported iOS research guest, not on the Linux host.

Use a trusted administrative installation channel, verify the complete binary
hash after transfer, then invoke the helper once. Preserve its process exit
status, signal, stdout, stderr and the host timestamps bracketing the request.
The native program accepts no configuration. If launched as root it drops its
group and user IDs to 501 before loading the library; another non-mobile user
is rejected. A 15-second process alarm bounds a stuck native call. An outer
transport timeout is an unknown observation outcome, not permission to repeat
the invocation automatically.

A successful invocation exits with status zero and prints one JSON object:

```json
{
  "protocol": 1,
  "boot_uuid": "12345678-1234-1234-1234-123456789ABC",
  "uid": 501,
  "gid": 501,
  "application_accessibility": false,
  "automation": false,
  "settings_written": false,
  "ax_tree_queried": false
}
```

This is an illustrative result. Retain the actual boot identity and values.
Missing symbols, an unavailable boot identity, failed privilege changes or a
getter returning a value other than zero or one cause a nonzero exit without
a success object. Do not interpret missing output as `false`.

| Exit status | Meaning |
| --- | --- |
| 3 | Failed to become the mobile user/group |
| 4 | Process is not running as the mobile user |
| 5 | Boot-identity query failed or returned an unsupported size |
| 6 | Native accessibility library could not be loaded |
| 7 | A required getter could not be resolved |
| 8 | A getter returned an unsupported boolean value |

A signal termination, including the alarm, is a separate failure. The private
interfaces and user identity are specific to the tested guest. This is not a
general iOS compatibility guarantee or a supported App Store interface.

## Evidence and limits

The C fixture supplies controlled native dependencies. It covers all four
boolean combinations, privilege and boot-query failures, missing libraries or
symbols, and unsupported boolean values. It rejects attempts to resolve any
API other than the two getters and checks that failures produce no success
object. These checks exercise the actual reader source; they do not reproduce
the operating system's settings implementation.

The Linux build script completed compilation, Mach-O linking and signing with
Clang/LLD 19 and `ldid`. The signer also required its system `libplist` dependency;
a first isolated build without that dependency failed at signing and produced
no success manifest. Build success does not establish native execution of that
particular output binary.

In a native run on 2026-11-05, an independently built and signed helper from the
same C source executed successfully as the mobile user and reported both
switches off. This first measurement occurred after the cold local page had
failed and before any UI-tree query in that trial. A second measurement after
warm local/external browsing and the display comparison also reported both off,
with the same boot UUID. No UI-tree query or setting write was made between them.

The collected reports nevertheless contained an `AccessibilityUIServer`
30-second process-launch watchdog attributed to that boot. Its sampled main
thread was in CoreFoundation's run loop during UIKit startup. The settings
measurements do not establish their values earlier in boot, exclude transient
changes, or explain the watchdog or first-page failure. No newly inventoried
WebKit crash explained that page failure; this bounded collection does not
establish the absence of a WebKit termination. Neither the getter values nor
the crash establishes a remotely observable browser signal.

Keep settings observations separate from foreground identity, UI-tree results,
clock health, page completion and crash reports. Runtime creation does not
install or invoke this helper, which provides no setting-changing operation.
Observe and preserve the original state before evaluating a change in an isolated
guest; do not disable accessibility owned by another client or device user.
