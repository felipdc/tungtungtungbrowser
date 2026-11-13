# Browser-only proxy routing

This optional mode routes Safari's WebKit HTTP(S) requests through the configured
upstream proxy. Other device traffic uses the container's normal direct route.
The existing device-wide mode remains the default.

There are two parts: a loopback HTTP relay in the phone's network namespace and
Safari-specific guest preferences. The relay listens on `127.0.0.1:1080`, which
QEMU's user networking exposes to the guest as `10.0.2.2:1080`. It forwards to the
private upstream configuration, keeping provider credentials outside the guest.
HTTPS uses CONNECT; the relay does not decrypt traffic or install certificates.
No host ports, TUN device, routing marks or network-administration capabilities
are required by this overlay.

WebKit currently reads `WebKit2HTTPProxy` and `WebKit2HTTPSProxy` for Mobile Safari
when its data store has no explicit proxy override. This is an internal WebKit
setting and must be verified on each supported firmware. See the
[WebKit implementation](https://github.com/WebKit/WebKit/blob/main/Source/WebKit/UIProcess/WebsiteData/Cocoa/WebsiteDataStoreCocoa.mm).
It is not a per-app VPN: WebRTC, arbitrary UDP, system DNS and Safari's separate
system services are outside this HTTP(S) setting.

## Configure a phone

Use a prepared guest with Safari stopped. Build the optional helper using the
same Linux compiler/linker/signer prerequisites as the other native helpers:

```sh
scripts/build_safari_proxy.sh /absolute/external/safari-proxy-build
```

Use the same [private proxy configuration format](../runtime/proxy/README.md)
as the device-wide gateway. For named instances, add these registration options:

```sh
--proxy /absolute/private/proxy.json --proxy-scope browser
```

For direct Compose use, select this overlay **instead of** `compose.proxy.yaml`:

```sh
export TTT_PROXY_FILE=/absolute/private/proxy.json
docker compose -f runtime/compose.yaml -f runtime/compose.browser-proxy.yaml up --build -d
```

Once the guest daemon is ready, and before starting Safari or running the Safari
readiness check, configure its preferences from the Linux host:

```sh
python3 scripts/configure_safari_proxy.py /absolute/external/safari-proxy-build \
  --cid 3 --mode browser --output /absolute/external/proxy-enable-001
```

The helper discovers Safari's data container, uses the mobile user's native
preferences service, changes only the two proxy keys and verifies them in a
separate process. It refuses changes while MobileSafari is running or when
either key contains a different proxy. It sends no launch, touch or page script.
Failures are retained without automatic replay. Successful preference readback
does not itself prove routing.

Start Safari normally after configuration. Verify a browser IP page against the
relay's upstream exit, then compare a native non-browser request against the
direct route. The device-wide `check_proxy_route.py` requires a TUN/firewall setup
and is not the check for this mode. Test normal product pages after routing is
verified.

## Inspect or remove the setting

Use `--mode read` with a new evidence directory to inspect without changing
preferences. With Safari stopped, `--mode direct` removes these two owned keys.
It does not change Compose networking. Remove the guest setting before returning
that bundle to device-wide proxy mode or to a configuration without the relay.
The settings persist in the guest disk, so keep separately configured bundles
when comparing modes.

The relay has no direct fallback outbound. Non-browser direct access is
intentional; this mode provides no device-wide egress containment. Browser
behavior when the relay is unavailable still needs native failure testing.

## Native validation

A prepared iOS 26.6.2 guest verified Safari's public IP against the relay's
upstream exit. A separate native Foundation HTTPS request from the same guest
matched the direct host exit, which differed from Safari's exit. Native device
and host-display captures corroborated the browser observation. No page scripts
or TLS interception were used. The helper also refused a preference write
while Safari was running; a separate native read confirmed unchanged settings.

Three distinct normal Shopee product URLs were opened through Safari. All three
product-data requests returned HTTP 200 responses containing Shopee's
login-required error, rather than validated product JSON. A later native
screenshot of the final page showed the login-required screen. This verifies
reachability through the browser proxy; it does not establish successful product-data capture or a success rate.

The targeted proxy and instance-isolation suite passed 55 tests. Both Compose
modes were checked: the browser relay adds no TUN device, published port or
network-administration capability; existing registrations retain device-wide
routing by default.
