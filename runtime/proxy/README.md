# Device proxy routing

Merge `compose.proxy.yaml` with the base Compose file. The iPhone shares the
egress container's network namespace. Only the egress container gets
`NET_ADMIN`, `NET_RAW` and `/dev/net/tun`; the iPhone retains no capabilities.

```sh
export TTT_VM_DIR=/absolute/path/to/your/ios-bundle
export TTT_RUNTIME_DIR=/absolute/path/to/built/vphone-linux
export TTT_PROXY_FILE=/absolute/path/to/private/proxy.json
docker compose -f runtime/compose.yaml -f runtime/compose.proxy.yaml up --build -d
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

The router pins sing-box and verifies its release SHA-256. Its TUN sends TCP
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
`launch.json`. Inspect its native response before time-sensitive browsing.
Do not also run a separate clock loop or readiness's `--sync-clock` step for
the same guest. See the [clock behavior](../../README.md#python-and-native-control)
for lifecycle and failure details.

The firewall drops external traffic by default. Only marked router connections
to the configured proxy gateway are allowed. The iPhone cannot set that mark.
Docker's embedded resolver is blocked. Loopback remains available for private
diagnostics. The container health check verifies the local tunnel, not remote
proxy availability or geographic targeting. Verify those through the browser
and verify failure behavior before relying on a new proxy configuration.

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
