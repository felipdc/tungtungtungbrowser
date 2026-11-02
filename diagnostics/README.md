# Browser observation page

This is a diagnostic page loaded normally through Safari's native open-URL
operation. Its JavaScript reads browser properties and draws on its own canvas;
it does not inject scripts into other pages, redefine properties or replace APIs.
The browser controller continues to reject page evaluation and script URLs.

Run the server **inside the iPhone container**, sharing QEMU's network namespace:

```sh
python3 scripts/serve_probe.py --output /vm/browser-observations
```

It listens only on loopback and prints a random-path URL for QEMU's guest
gateway, `10.0.2.2`. Open that URL through the native browser controller.
Each submission creates a separate directory containing browser observations,
the rendered canvas PNG and the HTTP request headers received by the server.
Keep the service out of baseline resource measurements and stop it when finished.

The page compares main-frame, dedicated-worker and shared-worker navigator
values, plus display geometry, WebGL identity and a simple canvas sample.
Missing or failed observations are recorded. The page has been exercised in native Safari. Server transport checks alone
are not Safari evidence. Its touch button records trusted input events without
changing browser APIs.

This local HTTP test does not establish TLS behavior, service-worker coherence,
public IP/DNS/WebRTC isolation, product rendering or parity with a physical
iPhone. Those require separate measurements. A matching canvas hash alone is
also not a fidelity verdict.


Run `scripts/sample_resources.py CONTAINER_ID --seconds 120 --output PATH`
on the Docker host to sample whole-cgroup memory, CPU, throttling and OOM
counters. Keep the output outside the checkout. `scripts/measure_ready.py`
uses the VM launch clock for daemon, unlock and foreground/screenshot timing;
rendered-page verification remains a separate step.
