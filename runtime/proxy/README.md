# Device proxy routing

Merge `compose.proxy.yaml` with the base Compose file. The iPhone shares the
egress container's network namespace. Only the egress container gets
`NET_ADMIN`, `NET_RAW` and `/dev/net/tun`; the iPhone retains no capabilities.

```sh
export TTT_VM_DIR=/absolute/path/to/your/ios-bundle
export TTT_RUNTIME_DIR=/absolute/path/to/built/vphone-linux
export TTT_PROXY_FILE=/absolute/path/to/private/proxy.json
docker compose -f runtime/compose.yaml -f runtime/compose.proxy.yaml up --build -d
docker compose -f runtime/compose.yaml -f runtime/compose.proxy.yaml exec iphone \
  python3 scripts/measure_ready.py /vm --qmp-socket /vm/control.sock \
  --output /vm/readiness.json
```

Create the private file outside the repository with mode `0600`. The following
values are placeholders; use your proxy service's hostname, resolved IPv4
address, credentials and country/session syntax:

```json
{
  "scheme": "https",
  "host": "proxy.example.net",
  "address": "192.0.2.10",
  "port": 443,
  "username": "YOUR_USERNAME",
  "password": "YOUR_PASSWORD"
}
```

Supported schemes are `https`, `http` and `socks5`. HTTPS CONNECT is the
validated transport; the other branches need equivalent workload validation.
The optional `country` field is metadata, not a routing instruction. Country
selection belongs in the upstream service's configuration or credentials.
Resolve the gateway on the runtime host before starting the isolated namespace.
HTTPS verifies the certificate against `host` while connecting to `address`.
Refresh that address and recreate the whole Compose project when it changes.
The iPhone must be recreated whenever the egress network namespace is replaced.

The router pins [sing-box 1.14.4](https://github.com/SagerNet/sing-box/releases/tag/v1.14.4)
and verifies a separate release SHA-256 for `amd64` and `arm64`. The image
selects the binary for its Debian architecture and rejects other architectures.
Both images have passed native builds and a `sing-box version` execution.
The ARM image still needs the same end-to-end proxy and native Safari checks
as the x86 deployment; building it does not establish ARM iPhone runtime support.

Its TUN sends TCP
through the proxy. DNS is intercepted and sent as HTTPS through the same
proxy. Other UDP traffic is rejected, so QUIC and general UDP are unavailable.
IPv6 is disabled. Safari retains its native TLS connection to the destination;
this setup does not install an interception certificate.

Because UDP NTP is blocked, this overlay enables native host-time offers every
60 seconds after the guest control daemon becomes healthy. The launcher owns
this worker and stops it before native guest shutdown. Use an accurately
synchronized host clock. `TTT_CLOCK_INTERVAL_SECONDS` accepts 10–3,600 seconds,
or `0` to disable maintenance. Manual guest time is preserved; a failed offer
stops maintenance and remains recorded in the unique state file identified by
`launch.json`. Run `measure_ready.py` before browsing; it detects maintenance
and waits for a successful automatic-time response from this launch before
unlocking the guest or launching Safari. This prevents the initial correction
from overlapping those startup actions and preserves the original boot deadline.
Do not also run a separate clock loop or readiness's `--sync-clock` step for
the same guest. See the [clock behavior](../../README.md#python-and-native-control)
for lifecycle and failure details.

The firewall drops external traffic by default. Only marked router connections
to the configured proxy gateway are allowed. The iPhone cannot set that mark.
Docker's embedded resolver is blocked. Loopback remains available for private
diagnostics. The container health check verifies the local tunnel, not remote
proxy availability or geographic targeting. Verify those through the browser
and verify failure behavior before relying on a new proxy configuration.

### Check the upstream route before browsing

An available TUN interface can coexist with failed upstream connections. Run
the optional one-shot check inside the egress namespace before an expensive
boot or an external navigation. Supply your own HTTPS endpoint that returns
a JSON object containing `ip` and, when checking country, `country_code`:

```json
{"ip": "192.0.2.10", "country_code": "GB"}
```

The address and endpoint below are reserved examples, not a hosted service:

```sh
docker compose -f runtime/compose.yaml -f runtime/compose.proxy.yaml exec egress \
  python3 /app/scripts/check_proxy_route.py \
  --url 'https://route-check.example.net/' \
  --expect-country GB --timeout 20 --output /run/route-check-001
```

Exit zero means the endpoint supplied a valid IP and matched all requested
expectations at that observation. Use `--expect-ip` to check a previously
observed exit as well. Exit one records a failed probe; exit two indicates an
invalid configuration or output path. There is one attempt, no redirect
following and no retry. Environment proxy variables are ignored so the check
uses the container's configured network path. The tool never changes routing
or enables a direct fallback.

A separate disposable process bounds DNS, TLS and response reading by the
requested total deadline, followed by bounded process cleanup. The response
is limited to 16 KiB. The report distinguishes DNS, connection, TLS, HTTP,
deadline, malformed-response and route-mismatch failures. A DNS failure alone
does not identify its upstream cause; inspect the router's retained logs too.

The output directory must be new. `report.json` has mode `0600` and retains
failure outcomes. It stores the endpoint origin and selected route fields,
without the request path/query, response body, headers or exception text.
The image's `/run` is temporary: copy evidence to your private workspace before
recreating the container. Reuse of an existing output directory is rejected.

This is an explicit host-side probe, not a periodic health check or a native
Safari request. It does not prove browser rendering, leak resistance, future
availability or the truth of an untrusted endpoint's geolocation response.
The local Compose health check retains its existing meaning and behavior.

An unchanged gateway and a healthy tunnel do not establish that the upstream
exit IP stayed the same. Proxy services can expire sticky sessions or replace
an unavailable residential peer. Choose session behavior and a lifetime that
cover the entire browsing experiment, and record the observed exit address and
country before and after each comparison. Keep these observations outside the
repository. A host-side request through the shared namespace checks that route;
it does not replace a native Safari observation or a browser leak test.

If the exit changes between runtime trials, preserve the page results and
record the network change before attributing a failure to an emulator or
resource setting. Do not replace the egress container underneath a running
iPhone. Stop the guest cleanly, apply the private proxy configuration, and
recreate both containers together so they share the new network namespace.

The router is limited to 256 MiB and half a CPU. Include its resource use when
measuring the complete browser deployment. Credentials are mounted as a private
file and are not included in source or image layers.
