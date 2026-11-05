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
For a deliberate credential or session change on the same gateway, the optional
[session reload](#reload-credentials-without-recreating-the-iphone) below keeps
the existing namespace and guest.

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
the same guest. See the [clock behavior](../../docs/browser-control.md#managed-clock-offers)
for lifecycle and failure details.

The firewall drops external traffic by default. Only marked router connections
to the configured proxy gateway are allowed. The iPhone cannot set that mark.
Docker's embedded resolver is blocked. Loopback remains available for private
diagnostics. The container health check verifies the local tunnel, not remote
proxy availability or geographic targeting. Verify those through the browser
and verify failure behavior before relying on a new proxy configuration.

### Check gateway acceptance before boot

Before starting either container, an optional host-side check can attempt one
CONNECT using the same private proxy JSON. Supply a destination authority with
an explicit port:

```sh
python3 scripts/check_proxy_gateway.py --target example.org:443 \
  --timeout 15 --output /external/private/gateway-check-001 \
  < /external/private/proxy.json
```

This helper supports HTTP and HTTPS proxies. It connects only to the configured
IPv4 gateway address. HTTPS verifies the gateway certificate using `host`;
credentials arrive through stdin and never appear in command-line arguments.
It sends one CONNECT, records the HTTP status and closes the connection without
sending destination TLS or application data. It does not follow redirects,
retry, select another session, read the response body or change routing.
Environment proxy settings are not used.

Exit zero means the gateway returned a 2xx CONNECT response. Exit one preserves
a failed observation; exit two indicates invalid input or an unusable output
path. SOCKS5 is explicitly unsupported by this diagnostic. The report records
the last stage and distinguishes gateway TCP, gateway TLS, response timeout,
malformed response and proxy HTTP-status failures. A disposable process bounds
the entire check by the requested deadline, followed by bounded cleanup. A
timeout during a write or response wait leaves delivery uncertain; it does not
authorize retrying the operation automatically.

The output directory must be new, outside the source checkout. The directory
has mode `0700` and `report.json` has mode `0600`. Raw response headers, reason
phrases, bodies, credentials and exception text are omitted. Keep provider
diagnostics and session choices in private evidence alongside this report.

Gateway acceptance does not establish the egress namespace's route, country,
DNS operation, destination TLS, future availability or native Safari behavior.
Follow it with the separate route check below after router initialization.
A DNS failure in that check may originate from the proxy rejecting the DNS
service's CONNECT. Compare the retained router log and gateway observation
before attributing it to the DNS service or device. A session that pins an
unavailable peer can fail even while the gateway and local tunnel remain up.

In a native evaluation, this preboot guard returned HTTP 410 and stopped the
launch before creating guest storage or starting the iPhone. A deliberately
selected replacement configuration passed CONNECT and the initialized route
check before a subsequent launch. Both reports were retained. This validates
the guard's sequencing; it does not establish future proxy availability or
authorize automatic session rotation.

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

Wait for the egress service to become healthy before invoking the check. A
container being `running` does not establish that its initialization finished.
The tool checks the local router process, shared network namespace, tunnel and
IPv4/IPv6 DROP policies before constructing its HTTP client. If initialization
is incomplete, it records `failure_kind: router_not_ready` with zero probe
attempts. It does not wait, send traffic, modify the firewall or retry. These
checks establish the expected initialization state, not a complete audit of
every firewall rule or a guarantee that the proxy is reachable.

Exit zero means the endpoint supplied a valid IP and matched all requested
expectations at that observation. Use `--expect-ip` to check a previously
observed exit as well. Exit one records a failed preflight or probe; exit two
indicates an invalid configuration or output path. There is at most one attempt, no redirect
following and no retry. Environment proxy variables are ignored so the check
uses the container's configured network path. The tool never changes routing
or enables a direct fallback.

A separate disposable process bounds the local preflight, DNS, TLS and response
reading by the requested total deadline, followed by bounded process cleanup. The response
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

This guard follows a native evaluation in which an unguarded diagnostic ran
immediately after starting the egress container and observed the host route
before initialization completed. The guest had not started. A separate check
after initialization matched the configured proxy exit. Compose already waits
for egress health before starting the iPhone; manually executing a diagnostic
inside a newly started container previously bypassed that ordering.

Live validation of the guarded source rejected ordinary host execution with
zero probe attempts and confirmed the configured exit from the initialized
router with one attempt. The guest launch and both container identities stayed
unchanged. This exercised the helper in an existing router image; it was not a
fresh image-build test.

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

### Reload credentials without recreating the iPhone

When an upstream session becomes unavailable, finish outstanding browser work
and preserve its failure before deliberately selecting replacement credentials.
For the same gateway, protocol, port and TLS identity, the optional reload tool
can update only the router's username/password. It uses the pinned router's
[SIGHUP reload path](https://github.com/SagerNet/sing-box/blob/v1.14.4/cmd/sing-box/cmd_run.go).
Reloading closes existing proxy connections. It does not recreate the egress
container, replace its network namespace, reboot the guest or send browser input.

Supply a complete replacement private proxy JSON in the format above through
stdin, so credentials do not appear in command-line arguments:

```sh
docker compose -f runtime/compose.yaml -f runtime/compose.proxy.yaml exec -T egress \
  python3 /app/scripts/reload_proxy_session.py --output /run/reload-001 \
  < /external/private/proxy-next.json
```

The output directory must be new, outside the source tree and on the same
filesystem as `/run/proxy-config.json`. The tool rejects concurrent reloads,
checks that PID 1 is the router using that exact configuration, validates the
candidate with `sing-box check`, and verifies the original configuration and
process identity again before atomic replacement. A Linux process descriptor
targets one SIGHUP at that verified process. There is no automatic retry or
rollback after an uncertain result.

Exit zero and `status: reload_requested` mean the signal was sent. They do not
establish reload completion, upstream connectivity, a particular exit IP or
successful browser rendering. Run a new `check_proxy_route.py` observation with
the intended country/address, inspect the recorded container/guest identities,
and then verify Safari on an external page. Keep the original failed route and
browser observations; a later recovery does not change their outcomes.

The evidence directory has mode `0700`; its files have mode `0600`.
`previous-config.json` contains the original credentials and must remain
private. A failed validation can also leave `candidate-config.json` for review.
The report contains hashes, stage outcomes and error types, without credential
values or subprocess output. If `configuration_replaced` is true on failure,
inspect the existing router before any further action; do not assume the old
configuration is still active. An `outcome_unknown` CLI result likewise requires
inspection rather than another signal.

This updates the live `/run` configuration only. The mounted private source
file and future Compose launches are unchanged. Select the intended private
configuration explicitly for the next launch. Gateway/address changes still
require the complete namespace recreation described above. HTTPS has native
workload evidence; HTTP and SOCKS5 credential reloads need equivalent validation.

On 2026-11-04, a native evaluation of this tool sent one reload after the
original browsing and resource recordings finished. The tool retained the
current credentials; this validated the public reload path without selecting
another exit. It returned in about 208 ms. A separate route check then matched
the expected country and IP, and the existing container/PID/network-namespace
identities and guest launch record were unchanged. No browser input was sent.
An earlier private credential replacement in the same session had restored an
unavailable upstream route, but the browser's independent rendering and
acceptance failures remained failures. Neither observation qualifies general
proxy failure recovery or continuous connections during reload.
