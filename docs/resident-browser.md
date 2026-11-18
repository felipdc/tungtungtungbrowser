# Bounded native browser sessions

The optional resident session keeps native focus, foreground inspection and
navigation-key helpers alive across a batch. It avoids starting another native
process for every observation or key. `BrowserProfile.browser()` still uses
the ordinary helpers unless you explicitly supply `resident=...`.

This is experimental. Use one controller per guest and a separately prepared
guest whose native display reports 393 × 852 at scale 1 for the resource profile
below. Changing the framebuffer dimensions alone does not change the guest's
logical display scale. The existing coordinate-touch calibration is for scale
3; this path uses native keyboard navigation and does not qualify scale-1
coordinate or selector clicks.

The optional `--accessibility-scope navigation` restores the saved accessibility
switches between native navigations. Host tests and a four-phone native trial
verified its lifecycle and restoration. The trial failed the product success
target and does not establish CPU savings. The default remains
`--accessibility-scope session`.

## Build and run

First follow the [API setup](inspector-api.md) to build and install the ordinary
browser helpers, start the Inspector bridge, and verify Safari is in the
foreground. Keep builds and session records outside the checkout. Build on
Linux with `clang-19`, `ld64.lld-19` and `ldid` available:

```sh
export TTT_RESIDENT_BUILD="$HOME/.local/state/tungtungtungbrowser/resident-build-001"
export TTT_RESIDENT_SESSION="$HOME/.local/state/tungtungtungbrowser/resident-session-001"
bash scripts/build_resident_browser.sh "$TTT_RESIDENT_BUILD"
python3 scripts/resident_browser.py start "$TTT_RESIDENT_BUILD" \
  --cid 3 --output "$TTT_RESIDENT_SESSION"
python3 scripts/resident_browser.py status "$TTT_RESIDENT_SESSION"
```

Override tool paths with `TTT_RESIDENT_CLANG`, `TTT_RESIDENT_LINKER` and
`TTT_RESIDENT_LDID` if needed. Each build/output directory must be new. The
launcher verifies source and binary hashes, uploads and reads back each helper,
then verifies its process and boot identity. It refuses an existing listener.
An uncertain launch is reconciled only through its exclusive native log; the
launcher does not repeat the spawn.

Select the session in Python, using the ordinary helper deployment directory
as `TTT_API_DEPLOYMENT`:

```python
import asyncio
import os
from tungtungtungbrowser import BrowserProfile

async def main():
    profile = BrowserProfile(os.environ['TTT_API_DEPLOYMENT'])
    browser = profile.browser(
        resident=os.environ['TTT_RESIDENT_SESSION'],
        input_settle=.25,
        address_focus='shortcut',
        focus_read_attempts=3,
    )
    async with profile.session(
        browser=browser, new_tab_url='https://example.com/', retain_tab=True
    ) as page:
        await page.wait_for_selector('h1', timeout=90)
        print(await page.content())

asyncio.run(main())
```

Stop after all page sessions have closed, including after a workload error:

```sh
python3 scripts/resident_browser.py stop "$TTT_RESIDENT_SESSION"
```

Require `status: closed`; inspect any `closure_unverified` result before
starting another batch. Then perform the ordinary Inspector bridge and VM
cleanup. Retaining a tab keeps its cookies and website data; it is not a fresh
browser context. The resource preset and resident session do not clear website
storage, change proxy credentials or warm pages automatically.

## Ownership and limits

Host-only VSOCK ports 1346–1348 carry the accessibility lease, focus and control
protocols. The lease temporarily enables the two native accessibility switches
and records their baseline. An independent guardian restores them after normal
closure or controller loss. Every focus read retains the existing guarded
attribute, PID and editing checks; foreground inspection reads fresh native
state rather than caching a foreground claim. Native keys name the expected
helper PID and guest boot UUID before dispatch and return balanced key events.

The Python client stops accepting new calls 1,650 seconds after the earliest
helper launch. Native services expire after 1,800 seconds; the independent lease
guardian has a 1,900-second fallback deadline. Close the batch explicitly well
before these deadlines. There is no automatic renewal, input replay or fallback
to a different keyboard transport after an uncertain result. Paste continues
through the ordinary guest transport. Shorter voluntary input waits preserve
foreground, editing-field and exact address readback checks. A failed read can
still terminate a navigation before paste.

## Experimental navigation scope

Start a new session with `--accessibility-scope navigation` to try the candidate.
It requires newly built helpers. Startup verifies the original switch values,
then pauses the accessibility lease before exposing the session to the SDK.
`BrowserProfile.browser(resident=...)` automatically holds the lease across
`goto()`, `new_tab()` and `close_tab()`, including native focus checks, paste
readback and keyboard dispatch. The saved values are restored before those
synchronous methods return, including after an input error. Inspector task
cancellation cannot end the lease while its native thread is still running.
Page loading and screenshot waits occur with the original switch settings.
Original switches that were already enabled stay enabled.

Transitions verify the helper PID, guest boot UUID, guardian identity and fresh
switch values. An uncertain or invalid transition disables further SDK calls
for that session and is never retried. Run the ordinary `stop` command to close
its owned helpers and verify restoration. The independent guardian retains its
exclusive ownership lock and original lifetime while paused; health requests
and navigations cannot extend it. Session records use version 2 so older clients
refuse this mode. Existing version-1 sessions remain supported.

This option changes the duration of accessibility enablement. It preserves
normal product-page navigation and native verification. Qualify it on the
intended workload before adopting it; host tests do not establish CPU savings
or Shopee success rates.

## Resource profile

Register a prepared scale-1 guest with
`--resource-profile capture-experimental --cpuset 0-3` using
[`phone_instance.py`](../scripts/phone_instance.py). Select CPU numbers from
the host topology: logical siblings are not separate physical cores. Explicit
CPU sets in the same registry must be disjoint; other workloads and registries
remain the operator's responsibility.

The profile sets three guest CPUs, 3 GiB guest RAM, one renderer thread, a
1 GiB translation cache, a four-CPU container quota, 7 GiB container memory,
11 GiB total memory-plus-swap and 6 GiB shared-memory capacity. Swap must exist
on the host to be useful. These settings do not enable host compression or
change the emulator. Existing `api-experimental` registrations and default
memory/swap behavior remain unchanged. See [multiple phones](multiple-phones.md)
for separate VM storage and CID requirements.

## Measured scope

On 2026-11-14, the private prototype completed a four-phone warmed trial on a
16-physical-core, 32-GiB Linux x86_64 host. Each phone had four disjoint physical
cores and the resource/display settings above. Three fixed normal-product page
warmups per phone preceded 16 measured captures per phone.

| Phone | Successful captures | p95 capture time |
| --- | ---: | ---: |
| A | 15/16 | 51.39 s |
| B | 16/16 | 54.87 s |
| C | 16/16 | 49.95 s |
| D | 15/16 | 55.82 s |

The total was 62/64 (96.875%), with pooled p95 52.16 seconds. Two native focus
failures remained in the denominator. There were no login outcomes. Captures
required matching live product JSON, document identity, native/host screenshots
and visible title/price. One main product image was still pending; requiring
that image would count 61/64 instead. Timing ran from local worker entry through
result publication, excluding warmups and external HTTP queue/transport time;
it did not wait for every asset.

The experiment also used fresh website storage, fixed per-phone proxy sessions,
and a separately validated emulator configuration. These were combined changes,
so the result does not isolate the contribution of each optimization or qualify
cold startup. Host compressed swap was enabled, but measured swap usage peaked
at only 256 KiB; it is not an established source of the latency improvement.
There were no OOM events and minimum available host memory was 4.42 GiB.

The public implementation adds source-verified packaging, explicit lifecycle
commands and keyboard ownership checks. Its verification is recorded separately
from the prototype benchmark; the benchmark is not a performance guarantee for
this package or another site, guest image or host.

### Navigation scope validation, 2026-11-18 UTC

The optional navigation scope was updated against the current runtime and passed
885 local tests, including controller loss, restoration, uncertain transitions,
concurrent ownership and the existing browser behavior. All 25 resident native
source files match the previously tested navigation-scope build. This source
comparison does not replace verifying the executable hashes before deployment.

The earlier [four-phone accessibility pilot](cpu-scheduling.md#navigation-scoped-accessibility-pilot-2026-11-16)
completed 16/16 candidate captures with a worst per-phone p95 of 60.078 seconds
on a 16-core host. It failed that trial's strict 60-second gate; the original
result remains rejected. Its lower CPU cost per successful capture also included
fewer failures and a shorter measurement window, so it is not an isolated
background-CPU reduction. The interrupted follow-up did not provide a paired
comparison.

A subsequent trial on eight physical AMD cores / eight vCPUs ran four fixed
warmups and eight distinct normal Shopee product requests per phone. Original
native/host image review accepted 9/32 captures: 2/8, 0/8, 7/8 and 0/8. Successful
per-phone p95 values were 55.614 and 66.839 seconds. One machine-classified product
was rejected because its main photo was blank. Login outcomes and a retained
Safari timeout document caused failures; the latter phone's eight scored
attempts failed before native input. The trial failed the required five of eight
successes on every phone and does not qualify four-phone capacity.

Across 62 native scopes, every acknowledged resume was followed by a pause
verifying the original 0/0 accessibility switches. All four original helper,
guardian and boot identities matched, with no uncertain transitions. Final
closure restored the original switches; all VMs exited without force, their
clock workers stopped, immutable parents remained unchanged and both test hosts
were stopped. Actual helper binaries and all 314 SDK source hashes were checked.
The scored window averaged 6.085 host cores, but included immediate failures and
login-only work, so it cannot establish CPU savings. The default scope remains
the whole session.

### Public package check, 2026-11-15 UTC

All 765 tests passed, including native guard lifecycle, transport failure,
keyboard ownership and profile integration cases. The four resident ARM64
executables cross-compiled and ran on a separate scale-1 guest copy. The live
resident foreground result matched the ordinary native observer; a keyboard
request naming the wrong helper PID was rejected. Native non-browser traffic
used the host's direct public IP, while Safari used a different proxy IP.

Three distinct normal-product requests followed a cold boot, without the
prototype's three product warmups. The first stopped before paste on native
focus error `-25216`. The next two produced matching product JSON, document
identity and visually reviewed native/host screenshots in 83.04 and 54.47
seconds. Both showed the product image, title and price. This is a 2/3 targeted
functional check, not a repeat of the warmed four-phone acceptance test or a
cold-start success-rate qualification. The initial setup attempt also remains
recorded: the ordinary installer refused to overwrite an existing touch helper;
the next attempt verified and reused the matching touch/keyboard binaries.

The full boot/browser sample averaged 3.02 host CPU cores and peaked at a
sampled 6.94 GiB of whole-cgroup memory under the 7 GiB limit, with no OOM or
CPU-throttling events. Host swap was disabled in this single-phone check. Native
settings returned to their 0/0 baseline, all focus guardians were reaped, both service
listeners closed, page/bridge/proxy-guard cleanup completed, and the VM stopped
without forced termination. The parent disk/cache hashes were unchanged. All
150 runtime source files matched the tested checkout byte for byte; evidence
was downloaded and hash-verified before the test host was stopped.
