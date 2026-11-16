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

The optional Safari UDP guard below blocks UDP attributed to Safari, including
WebRTC traffic that would otherwise bypass the HTTP relay. Non-browser traffic
continues to connect directly.

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

## Reload a browser proxy session

The existing relay can reload credentials without replacing its container or
rebooting the phone. Finish the current browser request and save its result first:

```sh
docker compose -f runtime/compose.yaml -f runtime/compose.browser-proxy.yaml exec -T egress \
  python3 /app/scripts/reload_proxy_session.py --scope browser --output /run/reload-001 \
  < /absolute/private/proxy-next.json
```

The replacement must use the same upstream gateway, protocol, port and TLS
identity. Only credentials change. `--scope browser` selects the running
browser-relay configuration; it does not change device routing or Safari's
preferences. The default scope remains `device` for existing callers. See the
[reload guarantees and private evidence](../runtime/proxy/README.md#reload-credentials-without-recreating-the-iphone).
A reload closes existing proxy connections, and `reload_requested` is only a
signal receipt. Verify the new upstream session and browser route before sending
the next product request.

For an existing Evomi locked session, add `--rotate-evomi-session` to generate a
new ten-character session token from the supplied private configuration. It
preserves the gateway, country and other parameters, rejects ambiguous session
modes, and does not print credentials. Evomi documents locked sessions as fixed
to one peer, with no automatic replacement when that peer disconnects. See
[Evomi's session parameters](https://docs.evomi.com/proxy-instructions/residential-proxies/proxy-sessions/).

## Automatic recovery after a login response

`LoginProxyRecovery` provides an optional per-phone policy for a capture worker.
It calls an application-supplied async reset function after a completed,
confirmed login-required response. It does not infer login from a timeout,
arbitrary page text or an unrelated HTTP error.

```python
from tungtungtungbrowser import LoginProxyRecovery

recovery = LoginProxyRecovery(
    "/absolute/private/phone-41/proxy-recovery",
    cid=41,
    reset_and_verify=reset_browser_proxy_and_verify,
    max_resets=3,
)

for request in scheduled_requests:
    recovery.check_ready()
    result = await capture_normal_product_page(request)
    save_original_result(result)
    await recovery.after_capture(
        result.attempt_id,
        login_required=result.outcome == "login_required",
    )
```

The reset callback selects fresh provider credentials, invokes the reload once,
and verifies the upstream session, Safari's exit, non-browser direct routing,
and unchanged container/guest identities. It must return these exact boolean
proof fields: `session_changed`, `upstream_verified`, `browser_verified`,
`non_browser_direct_verified`, and `identities_preserved`. These checks belong in
the adapter because provider session syntax and phone deployment differ. A new
provider session does not itself guarantee a different IP or product success.

Use a separate persistent directory and opaque attempt IDs for each phone. The
policy permits three resets by default, configurable from one to ten. The budget
belongs to a persistent record for a bounded worker job, survives Python restarts,
and never repeats a reset for the same attempt. A missing verification,
callback failure, cancellation or interrupted process blocks further work until
its existing evidence is inspected. Exhausting the budget also stops the phone;
a successful intervening capture does not reset that limit. Concurrent callers
cannot reset the same phone together.

The async callback has a 120-second deadline by default, configurable with
`timeout_seconds`. Its network and native operations should also have finite
deadlines. A timeout remains uncertain and stops further work; it does not prove
an in-flight operation was cancelled.

The original login remains a failed capture. Recovery does not retry that
capture, clear cookies or change the requested product URL. The next scheduled
request still opens the normal page with native input and verification. Include
reset time and CPU use in batch cost measurements, and record every failed
attempt and proxy change when comparing performance.

The public suite passes 788 tests, with nine additional deployment-adapter tests.
A native worker test confirmed that a live Shopee login response triggers a
session change; that run stopped when Safari and the relay probe disagreed.
A subsequent controlled test used an explicitly labeled recorded login response
to trigger a real, verified reset in 25.8 seconds. All four following normal
product pages produced validated JSON and matching host/native screenshots,
with capture times of 42.1–49.7 seconds. Non-browser traffic stayed direct and
the phone and relay identities were preserved. The phone shut down cleanly.

The fixture is excluded from live success counts. This is functional recovery
validation, not a four-phone success-rate or CPU qualification. Proxy changes
can still lead to another login response; retain those failures in measurements.

## Optional Safari UDP guard

Build and start the guard before launching Safari when browser UDP must not use
the direct route:

```sh
scripts/build_safari_udp_guard.sh /absolute/external/safari-udp-guard-build
python3 scripts/safari_udp_guard.py start /absolute/external/safari-udp-guard-build \
  --cid 3 --output /absolute/external/safari-udp-001
```

This native helper installs one ephemeral NetworkExtension policy matching the
installed Safari executable's UUID and UDP protocol. It blocks matching traffic;
it does not send UDP through an HTTP proxy. Features that require browser UDP may
therefore be unavailable. It changes no system routes or other apps' policies.
The policy uses private iOS interfaces and needs native verification on each
supported firmware.

The helper owns the policy for its process lifetime, ending at explicit stop or
guest shutdown. It is not installed as an automatically restarted service and
must be started again after a guest reboot. Check its recorded process and boot
identity before a capture:

```sh
python3 scripts/safari_udp_guard.py status /absolute/external/safari-udp-001/deploy.json --cid 3
```

After browser work finishes, remove the policy with its ownership record:

```sh
python3 scripts/safari_udp_guard.py stop /absolute/external/safari-udp-001/deploy.json \
  --cid 3 --output /absolute/external/safari-udp-stop-001.json
```

Stopping restores Safari's ordinary UDP behavior. The stop command verifies
native policy removal, rather than treating an acknowledgment as completion.
Control listens only on guest VSOCK port 1345 and accepts the host peer; it adds
no host TCP listener. A different process, guest boot or existing deployment is
never silently replaced. An uncertain start is reconciled using its exclusive
native log and live process identity, without launching another helper.

Cold startup allows one administrative launch up to 120 seconds, with a
135-second transport budget. After an acknowledged spawn, refused or timed-out
health reads may repeat within the original 30-second readiness window. Those
reads never repeat the launch or install a second policy.

## Inspect or remove the setting

Use `--mode read` with a new evidence directory to inspect without changing
preferences. With Safari stopped, `--mode direct` removes these two owned keys.
It does not change Compose networking. Remove the guest setting before returning
that bundle to device-wide proxy mode or to a configuration without the relay.
Stop the optional UDP guard as well when leaving browser-only mode.
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

Without the UDP guard, three distinct normal Shopee product URLs were opened
through Safari. All three product-data requests returned HTTP 200 responses containing Shopee's
login-required error, rather than validated product JSON. A later native
screenshot of the final page showed the login-required screen. This verifies
reachability through the browser proxy; it does not establish successful product-data capture or a success rate.

Guarded testing on an initialized profile captured two live HTTP 200 product
responses with matching product IDs and prices. Inspector reported network
responses over HTTP/2 through the HTTP relay, and both native and host-display
screenshots showed the corresponding products. The proxy exit and initial
browser state differed from the earlier trials, so this does not establish that
the guard alone changed Shopee's decision or establish a success rate.

The packaged helper was also tested on a separate child of the prepared seed,
with the proxy settings and guard applied before Safari launched. Native HTTPS
and UDP requests retained the direct exit while Safari used the proxy. A duplicate
guard start was refused without replacing the running process. A normal product
navigation then returned validated live product JSON and a matching visible page,
bringing the guarded total to three successful captures across two products and
two phones. The home page had shown a login-required screen in this run; product
success does not imply that every Shopee navigation succeeds.

Native stop verification confirmed removal of the owned policy before clean
guest shutdown. All successful captures used normal visible product pages,
native input and read-only Inspector access, with no injected page scripts or
direct product-API navigation.

The targeted proxy, UDP-guard lifecycle and instance-isolation suite passed 68
tests. Both Compose modes were checked: the browser relay adds no TUN device, published port or
network-administration capability; existing registrations retain device-wide
routing by default.
