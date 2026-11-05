# Development roadmap

These are planned capabilities, not current SDK features. Keep implementations
provider-neutral and validate them against native Safari. Deployment details,
credentials, external-site acceptance tests and captured content stay outside
this repository.

## Current runtime work

Prioritize reliable cold startup and responsive, repeatable use of warmed
browsers, then consistent external-page browsing and measured resource
reductions. Roughly 300 seconds is an acceptable cold-start budget for the
current work; reducing it further is not the main optimization target. This is
a development target, not a supported startup guarantee. Retain failures and
check native rendering at each stage before adopting an optimization.

- [ ] Make cold startup and Safari readiness reliable within the approximate
      startup budget. Measure warm navigation, page execution, native input and
      Home/Safari return latency separately from boot. Keep the original launch
      clock and cold outcome when a later warm observation succeeds; it does
      not turn the failed cold sequence into a pass. Qualify repeated navigation
      with Safari kept in the foreground as the primary browser request path.
      Track background/Home behavior separately and preserve its failures.
      Validate repeated
      Home/Safari transitions and process identity as well as initial launch.
      A [native transition trial](crash-diagnostics.md) passed
      local page and touch checks but hit a background scene-update watchdog;
      its experimental fast state observer remains unadopted.
      The [session-transition recorder](session-lifecycle.md) provides a
      repeatable request and evidence sequence through the published controller;
      its completion still requires separate screenshot review.
      The optional [direct process observer](process-observer.md) cross-checks
      kernel PID/start-time identity without enumerating applications. Native
      installation checks passed, but a post-Home query timed out; it remains
      a diagnostic and does not replace foreground or rendering checks.
- [ ] Establish consistent external-page browsing across recorded runs. An
      unexpected login wall or challenge is a failed acceptance outcome.
      Investigate device/browser signals and proxy behavior without treating a
      single successful page or one failure as proof of the cause.
- [ ] Resolve periodic clock-offer timeouts under slow native inspection while
      preserving once-only requests and recorded failure outcomes. The separate
      30-second offer timeout has accepted native replies taking 20.0 and 5.6
      seconds; sustained reliability and clock accuracy still need validation.
- [ ] Reduce total host/container resource usage under real browsing workloads.
      Use the [graphics allocation analyzer](graphics-memory.md) to distinguish
      logged live placements from unaccounted pool capacity. The filtered
      upstream log cannot establish reclaimable bytes; the optional complete
      placement diagnostic remains separate from runtime defaults and measured
      performance comparisons.
      The [renderer compiler experiment](lavapipe-compiler.md) has passed a
      standalone compute check, but its first native run exceeded both worker
      deadlines. The compiler setting remains unadopted; native browser
      performance and fidelity are separate adoption requirements.
      The [ARM wait-hint scheduling experiment](qemu-wfe.md) has a separate
      two-core handoff check and native comparison procedure. Its optional
      backoff remains disabled; lower synthetic CPU use is insufficient for
      adoption without a real browsing benefit.
      The [translation-cache comparison](translation-cache.md) failed Safari
      readiness at 512 MiB; retain the 1024-MiB default. New comparisons isolate
      the full renderer-cache mount and verify both filenames and hashes.
      The [native floating-point update](native-fp.md) requires a host-FPU
      boundary correction to pass the numerical fixture. Patched native trials
      passed local rendering, touch and one session transition each, but did
      not improve cold startup. Repeat successful workloads before adopting
      either TLB mode or claiming a resource reduction.
      A [guest CPU-count comparison](cpu-scheduling.md) reached foreground
      Safari sooner with three application CPUs, but both worker checks timed
      out. Its two-CPU control passed local rendering and touch, then hit another
      native snapshot watchdog. Keep CPU settings experimental; host CPU
      pressure now has a separate optional recorder and offline summary.
- [ ] Verify storage coherence in isolated runtime experiments. When firmware
      and operating-system devices address the same disk, their writable views
      must share one block node. Independent overlays can diverge even when
      they have the same parent. The [storage diagnostic](storage-isolation.md)
      verifies separate-overlay controls, shared-node writes in both directions,
      persisted bytes and unchanged parents without booting a native guest.
      Native migration and repeated browser acceptance remain under evaluation;
      preserve the existing state and explicitly choose a canonical source.
- [ ] Measure saved-tab accumulation and graphics-preview memory. Support
      explicit tab ownership and validate the existing experimental
      `goto(reuse_tab=True)` path through sustained native navigation, so repeated
      URL opens do not silently grow the browsing session. Compare resource
      changes with the same tab state and preserve session data.
      Automatic address-field focus recovery has passed a native local-page
      check; slow foreground and accessibility queries remain a latency issue.
      A [focused address-field experiment](native-control-audit.md#focused-address-field-query-experiment)
      first failed native symbol binding. A follow-up verified the required
      names and exact application-container process path. A larger root-request
      budget and explicit handling of absent identifiers subsequently passed
      three reads of one unchanged native address field. A later exact
      controller comparison and one settled-state navigation passed, retaining
      the saved tab count and active-tab record, but initialization and the
      immediate post-tap read failed. Qualify bounded settling and sustained
      focus/navigation transitions while preserving foreground and process
      ownership checks before adopting a narrower query path. Measure the whole
      control operation; a subsecond native scan still incurred multi-second
      RPC and foreground overhead. One later native root error recovered through
      a [bounded read-only follow-up](native-control-audit.md#bounded-read-settling-and-existing-url-display-forms)
      without repeating input, but recorder interruptions and initial stale
      display content remain in that trial. An
      [uninterrupted corrected repeat](native-control-audit.md#uninterrupted-repeat-after-the-recorder-corrections)
      reproduced the immediate post-typing timeout and passed its separate
      read after ten seconds. Qualify that state and retain exact complete-URL
      verification before adoption; the reference tree alone took 36.9 seconds.
- [ ] Trace warm display updates independently of navigation callbacks. A
      [foreground trial](browser-control.md#warm-navigation-observations)
      retained Safari and passed consecutive local diagnostics, but later
      captures sometimes showed an earlier page state without new input.
      Record frame ordering, requested URL, local callback identity and fixed
      capture times. The [local sequence page](display-sequence.md) separates
      DOM/canvas values from callback delivery evidence. Identify the active
      display device before instrumenting it; keep renderer experiments separate
      from CPU and proxy changes. A subsequent disjoint-range navigation showed
      completed content in a native screenshot while both surrounding QMP
      captures were blank, after QMP had already shown the new page. Trace this
      [capture disagreement](display-sequence.md#navigation-and-capture-disagreement)
      without treating callback completion or eventual agreement as a pass.
      An optional [display-stage trace](display-trace.md) now has a compiled
      candidate, twelve sanitizer-checked equivalence scenarios and verified QMP
      event controls. A bounded native recording completed three default URL
      opens without a sampled counter regression; all 36 render jobs had zero
      reported end statuses. The earlier mixed focus/navigation failure was
      not reproduced and remains unresolved. Use the offline trace analyzer
      and paired captures to qualify that path before changing display behavior.
      A subsequent separated focus/reuse continuation retained correct final
      counters but showed incomplete focus controls and an initially stale QMP
      page with toolbar artifacts. Clean render-stage statuses did not establish
      complete visual correctness.
      A [corrected repeat](display-trace.md#corrected-sequence-and-external-browsing)
      reproduced cached QMP content around a newer native image and later lost
      already-visible content on an external page. Test whether requesting a
      fresh render changes those observations, retaining both capture paths and
      the possibility of a deeper guest-rendering fault. Measure its cost
      separately and keep runtime defaults unchanged until native qualification.
      The first [fresh-render candidate](display-trace.md#fresh-render-candidate-stopped-during-readiness)
      built successfully but stopped at a native Safari launch transport timeout,
      before warm browsing. Its display effect and performance remain unmeasured;
      preserve that failed boot when repeating the controlled comparison.
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
      now provides a build, installer and calibrated SDK/CLI input. The separate
      [framework installer](native-touch-installation.md) adds full backups,
      guarded replacement, deliberate reversal and explicit reboot verification.
      Validate integration into runtime defaults; creation still does not
      install the framework repair automatically.
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
