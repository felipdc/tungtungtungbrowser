# Development roadmap

These are planned capabilities, not current SDK features. Keep implementations
provider-neutral and validate them against native Safari. Deployment details,
credentials, external-site acceptance tests and captured content stay outside
this repository.

## Current runtime work

- [ ] Make cold startup and Safari readiness reliable, with measured timings.
- [ ] Resolve periodic clock-offer timeouts under slow native inspection while
      preserving once-only requests and recorded failure outcomes. The separate
      30-second offer timeout has accepted native replies taking 20.0 and 5.6
      seconds; sustained reliability and clock accuracy still need validation.
- [ ] Reduce total host/container resource usage under real browsing workloads.
      The [renderer compiler experiment](lavapipe-compiler.md) has passed a
      standalone compute check, but its first native run exceeded both worker
      deadlines. The compiler setting remains unadopted; native browser
      performance and fidelity are separate adoption requirements.
      The [ARM wait-hint scheduling experiment](qemu-wfe.md) has a separate
      two-core handoff check and native comparison procedure. Its optional
      backoff remains disabled; lower synthetic CPU use is insufficient for
      adoption without a real browsing benefit.
- [ ] Measure saved-tab accumulation and graphics-preview memory. Support
      explicit tab ownership and validate the existing experimental
      `goto(reuse_tab=True)` path through sustained native navigation, so repeated
      URL opens do not silently grow the browsing session. Compare resource
      changes with the same tab state and preserve session data.
      Automatic address-field focus recovery has passed a native local-page
      check; slow foreground and accessibility queries remain a latency issue.
- [ ] Correct native touch geometry and verify pointer/touch field consistency.
      Calibrated contacts of different sizes still produce touch force values
      that follow the radius while UIKit and pointer pressure remain zero.
      The diagnostic now reads constructed and native Touch objects through
      the same literal, indexed and prototype-getter paths. A native Safari
      check reproduced the field discrepancy through all those readers while
      constructed controls remained correct. Compiled-framework inspection and
      an isolated AArch64 probe identified an incorrect field mapping in the
      native touch handler. A guarded two-load repair with its code-page hash
      passed a post-reboot Safari test. Combined with a separate calibrated HID
      helper, four trusted contacts had consistent touch/pointer fields and
      zero pointer dimensions on release. The [offline preparation tool](touch-repair.md)
      now packages exact build verification, reversible copies and signature
      verification. An optional [native tap backend](native-touch-helper.md)
      now provides a build, installer and calibrated SDK/CLI input. Complete
      framework installation and validate integration into runtime defaults;
      creation still does not install the framework repair automatically.
      Validate other builds, drags, multitouch and stylus behavior separately.
- [ ] Verify native touchscreen registration during readiness. A USB device
      can be present in the emulator while iOS has no main-screen digitizer;
      an acknowledged input request then has no UI effect. Validate detection
      and bounded recovery without restarting the guest or hiding the original
      failure. Keep gesture timing and contact-field fidelity as separate checks.
- [ ] Validate repeated navigation, worker consistency, native TLS and proxy
      failure/recovery behavior.

## Browser API expansion — pursue after the current runtime work

- [ ] **Original response HTML:** retrieve the main document response body
      before DOM parsing/loading completes, including URL, status, headers,
      encoding and redirect history. Define whether the API streams bytes or
      waits for the complete response body.
- [ ] **Rendered HTML:** retrieve a serialized DOM snapshot after an explicit
      lifecycle milestone, including `DOMContentLoaded` and `load`. Keep the
      original response body and the current DOM snapshot distinct; a page can
      continue changing after either event.
- [ ] **`waitForSelector`:** support attached, visible, hidden and detached
      states, with frame scope, configurable timeout and cancellation. Define
      navigation and stale-element behavior.
- [ ] **`waitForTimeout`:** provide a bounded, cancellable delay in a documented
      time unit. Keep condition-based waits available for deterministic flows.
- [ ] **`waitForNetworkRequest`:** wait for requests by URL, method, resource
      type or predicate, with timeout and cancellation. Register the waiter
      before the triggering action so fast requests are not missed. Add the
      corresponding response wait and request/response correlation.
- [ ] **XHR/fetch observation:** expose request and response events, headers,
      status, timing and bodies, including redirects and failed requests.
- [ ] **Network interception:** investigate native WebKit support for pausing
      XHR/fetch requests and continuing, modifying, aborting or fulfilling them;
      investigate response interception separately. Report unsupported behavior
      explicitly and define handling for streaming, cache and service workers.
- [ ] **Consistent lifecycle and action APIs:** expose navigation milestones,
      action-plus-wait patterns, scoped locators and actionable timeout errors.
      Distinguish document readiness, network activity and application readiness.
- [ ] **Developer integration:** provide Python/CLI examples, clear sync/async
      behavior, typed results and compatibility documentation for supported
      Safari/runtime versions.

## API implementation requirements

- Prefer native WebKit inspection/automation and operating-system interfaces.
  Do not add injected page scripts, JavaScript API replacements or fingerprint
  overrides. Evaluate inspection-related device/browser signals before adopting
  a transport.
- Preserve the configured proxy, DNS isolation and native TLS behavior. Do not
  require a TLS interception certificate merely to observe browser requests.
- Define ownership and lifetime of subscriptions, pending waits and captured
  bodies. Bound retained data, support cancellation and clean up on navigation,
  tab closure or transport failure.
- Keep credentials and captured bodies out of logs by default.
- Verify original HTML versus mutated DOM, delayed selectors, fast XHR/fetch,
  redirects, failed requests, timeouts, cancellation, streaming and service
  worker/cache behavior with local test fixtures. A successful API call alone
  must not be reported as successful page rendering.
