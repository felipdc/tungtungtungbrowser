"""vphoned HTTP over local VSOCK; deliberately no page scripting interface."""
import http.client
import json
import math
import socket
import uuid
from urllib.parse import urlsplit


class GuestError(RuntimeError):
    pass


class GuestTransportError(GuestError):
    """A failed exchange; later phases leave the native action's outcome unknown."""
    def __init__(self, phase, cause):
        self.phase = phase
        self.errno = getattr(cause, 'errno', None)
        # This describes this exchange only, not earlier steps of a Browser
        # operation. Sending can fail after some or all request bytes arrived.
        self.request_may_have_been_sent = phase != 'connect'
        super().__init__(f'vphoned transport failed during {phase}: {cause}')


class VsockHTTPConnection(http.client.HTTPConnection):
    def __init__(self, cid=3, timeout=30):
        if not 3 <= cid < 2**32 - 1:
            raise ValueError("Guest CID must be between 3 and 4294967294")
        super().__init__("localhost", port=1339, timeout=timeout)
        self.cid = cid

    def connect(self):
        try:
            self.sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            # vphoned's credit window is 512 KiB; Linux's default is smaller.
            # CPython converts integer VSOCK options to the kernel's uint64_t ABI.
            for option in (socket.SO_VM_SOCKETS_BUFFER_MAX_SIZE, socket.SO_VM_SOCKETS_BUFFER_SIZE):
                self.sock.setsockopt(socket.AF_VSOCK, option, 4 * 1024 * 1024)
            self.sock.connect((self.cid, self.port))
        except OSError as exc:
            self.close()
            raise GuestTransportError('connect', exc) from exc


ALLOWED_METHODS = frozenset({
    "device.snapshot", "device.info", "device.network", "device.screen",
    "screen.unlock", "screen.screenshot", "apps.launch", "apps.open_url", "apps.foreground",
    "input.tap", "input.swipe", "input.type", "input.paste", "input.key", "input.button",
    "ui.tree", "ui.ocr", "services.profile", "services.profile.apply", "time.timezone",
    "system.shutdown",
    "logs.syslog", "notify.state",
})

SCREEN_NOTIFICATIONS = {
    "startup": "com.apple.springboard.finishedstartup",
    "locked": "com.apple.springboard.lockstate",
    "screen_off": "com.apple.springboard.hasBlankedScreen",
}


def log_options(*, seconds=2, process=None, level="error", max_lines=500):
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 0.1 <= seconds <= 60:
        raise ValueError("Log duration must be between 0.1 and 60 seconds")
    if isinstance(max_lines, bool) or not isinstance(max_lines, int) or not 1 <= max_lines <= 5000:
        raise ValueError("Log entry limit must be an integer between 1 and 5000")
    if level not in {"all", "debug", "info", "default", "notice", "error", "fault"}:
        raise ValueError("Unsupported native log level")
    options = {"seconds": seconds, "level": level, "max_lines": max_lines}
    if process is not None:
        if not isinstance(process, str) or not process.strip() or len(process) > 256 or any(ord(c) < 32 for c in process):
            raise ValueError("Log process filter must be a nonempty name of at most 256 characters")
        options["process"] = process
    return options


def web_url(value):
    if not isinstance(value, str) or any(ord(c) <= 32 for c in value):
        raise ValueError("URL must not contain whitespace or control characters")
    url = urlsplit(value)
    if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password:
        raise ValueError("Only absolute HTTP(S) URLs without embedded credentials are supported")
    _ = url.port  # Reject malformed/out-of-range ports before sending anything to the guest.
    return value


class Guest:
    def __init__(self, cid=3, timeout=30, connection_factory=None):
        self.connection_factory = connection_factory or (lambda: VsockHTTPConnection(cid, timeout))

    def request(self, method, path, data=None):
        connection = self.connection_factory()
        phase = 'send_request'
        try:
            body = None if data is None else json.dumps(data, allow_nan=False).encode()
            connection.request(method, path, body=body, headers={
                "Host": "localhost", "Content-Type": "application/json", "Connection": "close"})
            phase = 'receive_headers'
            response = connection.getresponse()
            # Screenshots are base64 JPEG; bound accidental oversized responses.
            phase = 'read_body'
            payload = response.read(32 * 1024 * 1024 + 1)
            if len(payload) > 32 * 1024 * 1024:
                raise GuestError("Guest response exceeds 32 MiB")
            # A bounded HTTPResponse.read() can return a short body without
            # raising IncompleteRead. Check the remaining Content-Length before
            # parsing JSON, even if the bytes received form a complete object.
            if isinstance(response.length, int) and response.length > 0:
                raise http.client.IncompleteRead(payload, response.length)
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise GuestError("Expected a JSON object from vphoned")
            if response.status != 200:
                detail = value.get("error")
                if isinstance(detail, dict):
                    detail = detail.get("message")
                suffix = f": {detail[:2000]}" if isinstance(detail, str) else ""
                raise GuestError(f"vphoned HTTP status {response.status}{suffix}")
            return value
        except (OSError, http.client.HTTPException) as exc:
            raise GuestTransportError(phase, exc) from exc
        except ValueError as exc:
            raise GuestError(f"vphoned transport failed: {exc}") from exc
        finally:
            connection.close()

    def health(self):
        response = self.request("GET", "/v1/health")
        if response.get("status") != "ok":
            raise GuestError("vphoned did not report healthy")
        return response

    def call(self, method, **params):
        if method not in ALLOWED_METHODS:
            raise ValueError(f"Native browser controller does not support {method!r}")
        if method == "logs.syslog":
            params = log_options(**params)
        if method == "notify.state" and (set(params) != {"name"}
                or params["name"] not in SCREEN_NOTIFICATIONS.values()):
            raise ValueError("Only native screen-state notifications are supported")
        if method == "apps.open_url":
            web_url(params.get("url"))
            if params.get("bundle_id") != "com.apple.mobilesafari":
                raise ValueError("Navigation must explicitly target iOS Safari")
        request_id = str(uuid.uuid4())
        response = self.request("POST", "/v1/rpc", {
            "id": request_id, "method": method, "params": params})
        if response.get("id") != request_id or response.get("type") != "response":
            raise GuestError("Mismatched vphoned response envelope")
        if "error" in response:
            raise GuestError(f"Native operation failed: {response['error']}")
        if "result" not in response:
            raise GuestError("Native operation returned no result")
        return response["result"]

    def open(self, url):
        return self.call("apps.open_url", url=web_url(url), bundle_id="com.apple.mobilesafari")

    def shutdown(self):
        return self.call("system.shutdown", force=True)

    def screen_state(self):
        """Read native notifications without waking, unlocking or querying UI.

        These sequential observations are not an atomic snapshot. Lock and
        display state remain unknown until SpringBoard reports startup; even
        then, notifications do not establish visible or application readiness.
        """
        states = {}
        for key, name in SCREEN_NOTIFICATIONS.items():
            result = self.call("notify.state", name=name)
            if (not isinstance(result, dict) or result.get("name") != name
                    or type(result.get("state")) is not int
                    or not 0 <= result["state"] < 2**64
                    or (key != "startup" and result["state"] not in (0, 1))):
                raise GuestError("Invalid native screen-state notification")
            states[key] = result["state"]
        started = states["startup"] != 0
        return {"source": "darwin_notify", "springboard_started": started,
                "locked": bool(states["locked"]) if started else None,
                "screen_off": bool(states["screen_off"]) if started else None,
                "states": states, "visual_readiness_verified": False}

    def logs(self, *, seconds=2, process=None, level="error", max_lines=500):
        """Capture a bounded interval of native unified logs, not historical logs."""
        result = self.call("logs.syslog", seconds=seconds, process=process,
                           level=level, max_lines=max_lines)
        if (not isinstance(result, dict) or result.get("source") != "unified_log"
                or not isinstance(result.get("entries"), list)
                or not all(isinstance(entry, dict) for entry in result["entries"])
                or type(result.get("count")) is not int
                or result["count"] != len(result["entries"])
                or result["count"] > max_lines
                or type(result.get("truncated")) is not bool):
            raise GuestError("Invalid native log capture result")
        return result

    def tap(self, x, y):
        if not all(math.isfinite(v) and v >= 0 for v in (x, y)):
            raise ValueError("Tap coordinates must be finite, nonnegative screen points")
        return self.call("input.tap", x=x, y=y)
