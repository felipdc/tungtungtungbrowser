# Native browser control audit

Source inspection on 2026-11-02. Live Safari behavior remains a separate
check; this audit establishes the control path, not fingerprint parity.

## Pinned sources

- vphone-linux `888d17ca17268aaecd277c64f2d95385b184744b`.
- vphone-cli release `2.14.2`.
- Its [workspace dependency lock](https://github.com/Lakr233/vphone-cli/blob/2.14.2/VPhone.xcworkspace/xcshareddata/swiftpm/Package.resolved)
  pins `owngoal-dev/icli` 0.10.0 at
  `2fbdb5ee61d8c320732deb9768b22506f5e3d411`.

## Navigation

1. `tungtungtungbrowser/guest.py` accepts only absolute HTTP(S) URLs without
   embedded credentials. It explicitly supplies Safari's bundle identifier.
2. vphoned's [`GuestAPI.swift`](https://github.com/Lakr233/vphone-cli/blob/2.14.2/VPhoneDaemon/Daemon/GuestAPI.swift)
   handles `apps.open_url` by calling `openAppURL(url, bundleID:)`.
3. IcliKit's [`Apps.swift`](https://github.com/owngoal-dev/icli/blob/2fbdb5ee61d8c320732deb9768b22506f5e3d411/Sources/IcliKit/Apps/Apps.swift)
   delegates to `icli_open_url_in_app`.
4. On iOS, [`AppHandlers.m`](https://github.com/owngoal-dev/icli/blob/2fbdb5ee61d8c320732deb9768b22506f5e3d411/Sources/IcliPrivate/AppHandlers.m)
   uses LaunchServices' resource-opening operation for the supplied app,
   with native URL-opening fallbacks. It does not evaluate page scripts.

The operation can return success after starting an asynchronous open.
Therefore the controller's evidence manifest deliberately leaves navigation
unverified until screenshots and page content have been inspected.

## Reusing a tab

The higher-level `Browser.goto` defaults to explicit native URL opening.
Its experimental `reuse_tab=True` option uses the pinned native `input.key` and
`input.paste` methods when Safari is verified in the foreground. It confirms
Safari's accessibility address field before text entry and reads the URL back
before pressing Return. IcliKit's `pasteText` emits a Unicode HID event; it does
not run page code or use the administrative JavaScriptCore bridge. The native
open-URL path is used when Safari is not the verified foreground app. Tab reuse
has not passed sustained page-load validation and is not the default.

## Screen evidence

The same vphoned dispatcher handles `screen.screenshot` through
`takeScreenshot(base64: true, nativeResolution: true)`. The pinned Icli
[native implementation](https://github.com/owngoal-dev/icli/blob/2fbdb5ee61d8c320732deb9768b22506f5e3d411/Sources/IcliPrivate/IcliPrivate.m)
uses the native screen image path, including `_UICreateScreenUIImage`, and
encodes JPEG. It does not take the screenshot through browser JavaScript.

A foreground bundle identifier and a valid JPEG do not prove that the page
rendered. The startup report records these separately and keeps visual review
pending. Expected page content must be checked separately.

## Scope and remaining checks

The source search of `VPhoneDaemon`, `VPhoneGuestComponents` and the firmware
patcher found no `evaluateJavaScript`, `WKUserScript`,
`WKUserContentController` or `navigator.` injection calls in those paths.
This is a bounded source check, not a proof about every dependency or the
behavior of prebuilt guest binaries. The controller's contract tests
separately check the native Safari request and reject scripting methods and
non-web URL schemes before transport.

vphoned also offers a general administrative JavaScriptCore bridge upstream.
This project's browser controller does not expose or invoke it. Its presence
is not evidence that scripts are being injected into Safari.

A guest daemon is re-signed during installation, so its whole-file hash can
differ from the upstream release. For provenance checks, compare its Mach-O
text sections to the pinned release and keep installation-specific hashes
with deployment records outside the source repository.

Runtime validation should cover native device/display coherence, workers,
graphics, locale, TLS and network routing. No JavaScript fingerprint overrides
or page-script injection are part of the browser controller.
