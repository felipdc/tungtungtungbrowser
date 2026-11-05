# Comparing page execution with displayed frames

Use the local display-sequence page to investigate stale or partially updated
frames. It changes its own DOM and canvas; it injects nothing into other pages
and replaces no browser APIs. It is an observation workload, not a performance
optimization or a test of external-site acceptance.

Start the existing private diagnostic server in the guest runtime's network
namespace, with evidence outside the source checkout:

```bash
python3 /app/scripts/serve_probe.py --port 8767 \
  --output /vm/display-observations \
  --request-log /vm/display-requests.jsonl
```

Use the announced `qemu_guest_url`, appending
`display-sequence.html?steps=16&dwell_ms=12000`. Open it once through native
Safari control. The page displays increasing values from 1 through 16 in DOM
elements and a 2D canvas. Each region has eight bits, most significant first,
between magenta and cyan markers. The lower row is the bitwise complement of
the upper row. Disagreeing rows or regions can expose a partial update; a lower
value in a later capture can expose a backwards frame sequence.

The defaults are 16 steps and a 12-second dwell. `steps` accepts integers from
1 to 64; `dwell_ms` accepts integers from 1,000 to 60,000. Each step waits for
two animation-frame callbacks, saves a record through the existing `input`
collector, then waits the dwell interval before advancing. Failed callbacks
stop the sequence without retry. A fresh page run has a fresh identifier.

For navigation tests, use nonoverlapping counter ranges so an earlier page is
recognizable in either region. For example, open `?start=17&steps=4&dwell_ms=5000`
and then `?start=65&steps=4&dwell_ms=5000` in distinct native navigation requests.
The first page displays 17 through 20; the second displays 65 through 68.
`start` defaults to 1 and accepts integers from 1 to 255; the final value
(`start + steps - 1`) must also be at most 255. Invalid ranges stop before
drawing or sending callbacks. Callback records include both endpoints. Retain
the run identifier as well as the counter range, since a reload could use the
same range with a new run. Counter ranges identify content; they do not verify
which tab is active or that Safari finished navigating.

Keep the requested URL, run identifier and original navigation clock. Save
capture start and finish times, backend, pixels and metadata. Compare the
per-step `input-*.json` records with captures of both regions. Request-journal
times use the host's monotonic clock; page `performance` timestamps have a
different origin. Do not subtract those clocks directly. A callback or two
animation frames does not prove that the host displayed those pixels.

Observe a period with QMP captures alone, then a separately marked period with
native iOS screenshots bracketed by QMP captures. The images are sequential,
not simultaneous. Native screenshot requests and a higher capture frequency
can change rendering workload and latency, so retain actual timings and compare
equivalent periods before drawing performance conclusions. Record incomplete,
undecodable or mismatched captures as such; do not silently drop them or repeat
navigation. A settled matching image does not explain an earlier regression.
Check the visible run identifier before comparing counters across navigation:
an immediate capture can still show the preceding page's completed sequence.
Keep that image as navigation-latency evidence and attribute its counter to the
preceding run. It is not a backwards update within the newly requested page.

Keep renderer and CPU changes separate. Establish the active display device
before adding tracing, and record the exact source, binary and runtime settings
for any instrumented build. Keep source revisions, captures and derived frame
records alongside the private runtime evidence.

## Native observations

An experimental software-rendered runtime completed all 16 callbacks in a
foreground Safari session. A five-minute observation took 46 captures: QMP
every five seconds initially, followed by seven sequential QMP/native/QMP
groups and a final QMP capture. The first four images did not yet contain the
sequence. All 42 subsequent images had matching DOM and canvas values and
complement rows, with no backwards counters. Safari's PID and start time stayed
the same. The navigation acknowledgement took 1.27 seconds; the first readable
sequence was captured at 20 seconds. Those measure different things.

A separate later navigation used ten captures spread over six minutes, with
only one QMP/native/QMP group at the end. Its immediate capture still showed the
preceding run's completed value. All nine captures of the new run had matching
regions and no backwards counters; the final group retained the completed
value after an extended idle period. All 16 callbacks and one completion arrived,
with Safari's process identity unchanged. Native acknowledgement took 0.80
seconds; the first scheduled capture containing the new run was at 30 seconds.
Different capture schedules and the fixed run order prevent a causal comparison.

This separate warm diagnostic followed a failed first page after cold startup.
The original foreground response arrived within the approximate 300-second
startup budget, but its screenshot was blank and the first page reached Safari's
error screen. The warm result preserves that cold failure. It also does not
explain the earlier status-label regression in the broader browser diagnostic,
qualify external-page reliability, or justify a runtime-default change.
