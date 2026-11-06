# Experimental native selector clicks

`InspectorSession.click(selector, strategy='css', timeout=30)` resolves a unique
CSS or XPath target and requests a native touch. It currently supports visible
controls on the qualified 393 × 852 @3 portrait display. It does not automatically
scroll or wait for a control to appear. Use `wait_for_selector()` first, and
verify the resulting page condition or response after the action.

Install both optional dependencies and the separately qualified
[native touch helper](native-touch-helper.md):

```sh
python3 -m pip install -e '.[inspector,pointer]'
```

Configure the `Browser` with the helper digest from its completed installation.
Owned-tab creation, navigation and cleanup also need the
[native focus reader](native-focus.md). The click operation itself does not query
the native accessibility tree or change system accessibility switches.

With an attached or owned `page` and the portable fixture loaded:

```python
await page.wait_for_selector('#load-data', timeout=30)
async with page.expect_request('*api-fixture-data.json', timeout=60) as request, \
           page.expect_response('*api-fixture-data.json', timeout=60) as response:
    action = await page.click('#load-data', timeout=60)

payload = await response.value.json(timeout=30)
await page.wait_for_selector('#state[data-response="200"]', timeout=30)
await page.click('//button[@id="record-click"]', strategy='xpath', timeout=60)
await page.wait_for_selector('#state[data-clicked="true"]', timeout=30)
```

The return value distinguishes a dispatched contact from verified application
behavior: `click_dispatched` is true, while `page_result_verified` remains false.
A lost native reply is an uncertain outcome. The SDK sends no replacement touch.

## How the native point is checked

The SDK verifies the foreground Safari PID and resolves one DOM node under the
session's document lock. It rejects absent, ambiguous, disabled, hidden and
ignored targets. Two temporary, opaque Inspector highlights use distinct random
colors. Native screenshots locate pixels that changed with both highlights and
find an interior large enough for the qualified touch contact. Unchanged page
colors cannot match both captures. This uses Pillow on the controller; the base
SDK has no image-analysis dependency. Captures stay in memory.

After removing the highlight, the SDK enables iOS Inspector element selection
and sends one selection gesture. WebKit reports the selected native node. The
SDK resolves its identity with `DOM.requestNode`, accepts the requested node or
its descendant through native DOM ancestry, and disables selection mode. It
then checks the target again and sends one separate real touch. A covering
element rejects that final touch. This path uses no caller-injected page script,
`Runtime.evaluate`, synthetic DOM click or modified browser property.

The native implementation is described by WebKit's
[Inspector protocol](https://github.com/WebKit/WebKit/blob/7fd2ad1f3a55a1a43dd9b6ccbf04abeb69edf8cf/Source/JavaScriptCore/inspector/protocol/DOM.json)
and its [iOS gesture handling](https://github.com/WebKit/WebKit/blob/7fd2ad1f3a55a1a43dd9b6ccbf04abeb69edf8cf/Source/WebKit/UIProcess/ios/WKContentViewInteraction.mm).
The selection gesture temporarily replaces normal page touch recognizers.
Inspector highlights and selection mode are browser UI state and can be visible
in recordings or screenshots while the action runs.

## Limits and failure behavior

- This is an experimental combination of screen-image matching and native node
  selection. It is not a general proof of foreground ownership or resistance to
  adversarial animated content. Use one controller per guest and stable page
  layout; concurrent manual input is not supported.
- An off-screen target, a missing highlight or a region too small for the native
  contact fails before any selection touch. Native DOM focus does not guarantee
  that a control scrolled into view. No guessed coordinate is used as fallback.
- Rendering, zoom, display changes, frames, shadow trees and rapidly moving
  targets need further qualification. Cross-frame locators and automatic
  scrolling are not implemented. The calibrated helper independently checks the
  current portrait display before each contact.
- Timeouts and cancellation restore requested Inspector UI modes and join a
  native contact already in flight. Cleanup can therefore extend beyond the
  action timeout. An unacknowledged restoration is retained in `cleanup_errors`
  and makes the session unusable until it is closed.
- A selector match, successful input acknowledgement or `isTrusted` event alone
  does not establish a working application flow or physical-device equivalence.
  Validate response data, DOM changes, screenshots and session cleanup together.

## Native qualification so far

An initial native selection probe correctly identified a CSS button, an XPath
button and the covering element above a covered button. Its three selection
gestures produced no page pointer/touch/click events. A later prototype produced
two trusted clicks and captured the XHR JSON triggered by the first. Its final
covered-control attempt had no visible highlight after the event log expanded
the page, so that attempt failed; its owned tab still closed.

The public SDK implementation subsequently clicked a nested label within a CSS
button, captured and validated the resulting XHR JSON, rejected a covered
control, and clicked a second button through XPath. The fixture recorded exactly
ten trusted events: one pointer/touch/click sequence per successful action.
Those clicks took 17.48 and 16.89 seconds; covered-control rejection took 11.50
seconds. The same session then navigated to an external HTTPS page, read its
original and rendered HTML, performed a selector focus and captured native/host
screenshots. Both capture paths were visually reviewed for both pages. Owned-tab
cleanup was verified, preserving the original tab. The complete sequence took
158.21 seconds, including 44.54 seconds for creation, 21.34 seconds for the
external navigation acknowledgement and 13.50 seconds for cleanup.

An earlier attempt in the same guest failed at native address-field verification
and then timed out during cleanup's foreground query. Its owned tab was later
recovered and closed with retained ownership evidence. These results are
individual outcomes, not sustained external-site or startup reliability.
Network interception that modifies requests remains separate, unfinished work.

A separate background-tab attempt timed out while waiting for the inspected
document, before any native foreground read, screenshot or contact. Its two
owned tabs closed successfully. This verifies no input for that suspended-page
case; it does not qualify highlight discrimination for a background page that
continues answering Inspector requests.
