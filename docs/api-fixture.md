# Portable browser API fixture

The API fixture is a small page served by the existing local diagnostic server.
It exercises the distinction between original HTML and rendered DOM, delayed
conditions, XHR response capture and native input. It requires no external site,
cloud provider, account or proxy. Its JavaScript belongs to the served page;
the controller does not inject it into Safari or other pages.

Start the server in the VM's network namespace, using an output directory
outside this repository:

```sh
python3 scripts/serve_probe.py --port 8767 \
  --output /absolute/external/api-fixture \
  --request-log /absolute/external/api-fixture-requests.jsonl
```

The server prints a random path and a `qemu_guest_url`. Append
`api-workflow.html` to that URL and navigate with the native browser controller.
The server listens only on loopback. With QEMU user networking, `10.0.2.2`
refers to that namespace's host endpoint. Other network arrangements must supply
their own private route; the repository does not configure provider networking.
Responses use `Cache-Control: no-store`.

Register `expect_response(fixture_url)` before navigation to retain the original
document. After navigation, wait for `#state[data-ready="true"]`, then read
`content()`. The original HTML contains `Original document response`; the DOM
contains `Rendered document ready`. `#delayed-marker` is inserted later and can
exercise an attached-state waiter without using a fixed sleep.

Append `?auto=1` to exercise network capture before native selector clicks are
available. In this mode the page itself sends its ordinary fixture XHR after
initializing the DOM. The default page still waits for a button click. Once an
owned session has attached to the fixture, register waiters before reloading;
the initial bootstrap response predates attachment and cannot be recovered by
registering a waiter afterward:

```python
# session is attached to api-workflow.html?auto=1.
document_url = session.url.split("#", 1)[0]
async with session.expect_response(lambda r: r.url.split("#", 1)[0] == document_url) as document, \
        session.expect_request("*api-fixture-data.json") as request, \
        session.expect_response("*api-fixture-data.json") as response:
    await session.reload()

payload = await response.value.json()
assert response.value.status == 200 and payload["item"]["id"] == "fixture-item"
await session.wait_for_selector('#state[data-response="200"]')
original_html = await document.value.text()
rendered_html = await session.content()
```

This mode validates observation of page-owned XHR and the difference between
document-response HTML and the resulting DOM. It does not exercise a native
click or establish touch behavior. WebKit can retain a document's fragment in
the reported network URL; the document matcher above accepts that native form
as well as a fragment-free response URL.

| Control or condition | Expected observation |
| --- | --- |
| `#load-data` | A real click sends an XHR to the relative `api-fixture-data.json` URL. Register request and response waiters first. The JSON contains `item.id == "fixture-item"` and `error == null`. |
| `#state[data-response="200"]` | The page received and rendered the JSON response. This is distinct from observing the network response in Inspector. |
| `#record-click` | Sets `#state[data-clicked="true"]`. It can be selected with CSS or XPath `//button[@id='record-click']`. |
| `#next-page` | Navigates to `api-workflow.html?page=2`; the next DOM contains `Second document ready`. |
| `#events` | Records the last 20 pointer, touch and click events, their target IDs, `isTrusted` values and available pointer types. |
| `#disabled-button`, `#hidden-button`, `#covered-button` | Negative action cases. A selector match alone must not justify sending a touch. |
| The final button | Has no HTML ID, to expose implementations that only support native identity mapping for elements with IDs. |

An event's `isTrusted` value alone does not prove correct touch semantics or
physical-device equivalence. Inspect the event sequence, resulting DOM and
screenshot together. Keep native action failures, timing records and captured
results outside the repository. The fixture provides test conditions; it does
not add selector-click support to the SDK by itself.

## Native observations

The opt-in automatic-XHR fixture completed a later warm sequence through the
public API: owned-session creation, reload with document/request/response waiters,
JSON validation, original and rendered HTML, a DOM condition, XPath focus,
paired screenshots, navigation to an external HTTPS page, CSS focus and verified
owned-tab cleanup. The original tab was preserved. The sequence took 65.7 seconds:

| Operation | Observed duration |
| --- | --- |
| Create and attach the owned session | 15.19 s |
| Fixture reload acknowledgement | 0.031 s |
| Read completed fixture JSON | 0.167 s |
| Read rendered / original fixture HTML | 0.036 / 0.012 s |
| Native navigation acknowledgement for the external page | 19.76 s |
| External committed-URL / DOM-condition waits after the response | 2.60 / 2.80 s |
| Read rendered / original external HTML | 0.092 / 0.017 s |
| Verified owned-tab cleanup | 9.14 s |

Both native and host images showed the expected page content. Toolbar details
and the focused control's outline differed between capture paths; this is not
pixel-perfect rendering qualification. Native captures took 4.68 and 3.35 seconds,
while each host capture took about 0.13 seconds. This small warm sequence does
not establish repeated startup reliability, commercial-site acceptance, generic
selector clicks or reduced memory use. An earlier attempt retained its failed
fragment-sensitive document matcher even though the XHR returned successfully.

One native fixture session completed original-response retrieval, rendered HTML,
the delayed DOM condition, request/response waiters, XHR JSON capture, two
reviewed touch actions, a bounded delay and screenshots in 31.3 seconds. The two
action sequences, including lookup, native checks and DOM readback, took 10.8 and
9.0 seconds. Cleanup retained no pending commands or native workers and reported
no errors. Both touches produced `pointerdown`, `touchstart`, `pointerup`,
`touchend` and `click`, all with `isTrusted == true`; the recorded pointer type
was `touch` for the pointer down/up events and `mouse` for `click`.

Those actions used the private diagnostic's visually reviewed fixture labels and
native frames. They are **not validation of generic CSS/XPath clicks**. The first
native accessibility snapshot returned HTML IDs for the buttons; subsequent
reads and hit tests omitted those IDs. Direct native queries returned no value
for the web nodes' identifier attribute. A preceding attempt that required those
IDs failed before sending any touch. Keep arbitrary selector actions unqualified
until DOM identity can be tied to a reliable native touch target.

The initial image five seconds after opening the fixture was blank; a later
image rendered it. Rendered controls and successful input do not erase that
initial observation. The fixture screenshots also retained artifacts around
Safari's address bar. No general rendering-fidelity claim follows from these
results.

At the end of that boot, container peak memory was 6.38 GiB, with no memory-limit
events or OOM kills. This smaller fixture workload is not a controlled resource
comparison. Shutdown completed in 16.04 seconds without forced termination, and
the parent disks, parent cache and six original baseline files were unchanged.
One native close shortcut was sent for the diagnostic's added tab. A separate
fresh Inspector listing confirmed that tab was absent and the original tab was
preserved; the initial cleanup recorder had incorrectly tested an integer ID
against listing keys stored as strings. No second close input was sent.

A final read-only native settings sample reported both accessibility and
automation enabled. Earlier samples, including the later warm address-field
helper's before/after values, reported both disabled. A subsequent flat native
tree query returned a truncated 30-element snapshot before the final settings
read. That sequence does not establish which operation changed the switches.
The disposable guest state remains unqualified for reuse until that change is
investigated and its intended settings are restored. The preserved parents were
not promoted or changed.
