"""Small synchronous API for native Safari input and screen capture."""
import base64
import binascii
from dataclasses import dataclass, field
import math
from pathlib import Path
import re
import threading
import time
from urllib.parse import quote, urlsplit

from .guest import Guest, GuestError, contact_hold_ms, web_url
from .display import capture_display

SAFARI = 'com.apple.mobilesafari'
_CONFIGURED_TOUCH_HOLD = object()
# Native accessibility kAXIsEditingTrait. See docs/native-control-audit.md.
_AX_IS_EDITING = 1 << 21


def _expected_address(url):
    """An existing HTTP(S) document or Safari's explicit blank-page state."""
    return url if url == 'about:blank' else web_url(url)


def _unicode_address_key(value):
    """Match UTF-8 display forms without decoding delimiters or the authority."""
    scheme = re.match(r'https?://', value, re.IGNORECASE)
    address = value[scheme.end():] if scheme else value
    boundary = re.search(r'[/?#]', address)
    if boundary is None:
        return value
    offset = len(value) - len(address) + boundary.start()
    suffix = re.sub(r'[^\x00-\x7f]+', lambda match: quote(match[0], safe=''), value[offset:])
    # Only UTF-8's non-ASCII bytes participate. Keep ASCII escapes, including
    # %2F, %3F, %23 and %25, distinct from literal URL syntax.
    suffix = re.sub(r'%[89a-fA-F][0-9a-fA-F]', lambda match: match[0].upper(), suffix)
    return value[:offset] + suffix


def _native_address_key(url):
    """Comparison key for a full native address, including Safari's root form."""
    if url in ('', 'about:blank'):
        return 'about:blank'
    if isinstance(url, str) and url.lower().startswith(('http://', 'https://')):
        address = url.split('://', 1)[1]
        parsed = urlsplit(url)
        if parsed.path == '/' and not parsed.query and not parsed.fragment:
            address = address[:-1]
        return _unicode_address_key(address)
    return _unicode_address_key(url) if isinstance(url, str) else url


def _matches_active_url(value, url):
    if url == 'about:blank':
        return isinstance(value, str) and value in ('', 'about:blank')
    forms = {url, url.split('://', 1)[1]}
    parsed = urlsplit(url)
    if parsed.path == '/' and not parsed.query and not parsed.fragment:
        forms.update({url[:-1], url.split('://', 1)[1][:-1]})
    return isinstance(value, str) and _unicode_address_key(value) in {
        _unicode_address_key(form) for form in forms}


@dataclass(frozen=True)
class Screenshot:
    jpeg: bytes = field(repr=False)
    width: int
    height: int
    coordinate_scale: float

    @classmethod
    def from_native(cls, data):
        if not isinstance(data, dict) or data.get('mime_type') != 'image/jpeg':
            raise GuestError('Native screenshot did not report image/jpeg')
        try:
            jpeg = base64.b64decode(data['data'], validate=True)
            width, height = data['width'], data['height']
            scale = data.get('coordinate_scale', 1)
            if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (width, height)):
                raise ValueError('Invalid dimensions')
            if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
                raise ValueError('Invalid coordinate scale')
        except (KeyError, TypeError, ValueError, binascii.Error) as exc:
            raise GuestError('Native screenshot has invalid image data or geometry') from exc
        if not jpeg.startswith(b'\xff\xd8') or not jpeg.endswith(b'\xff\xd9'):
            raise GuestError('Native screenshot has invalid JPEG framing')
        return cls(jpeg, width, height, scale)

    def save(self, path, *, overwrite=False):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('wb' if overwrite else 'xb') as output:
            output.write(self.jpeg)
        return path

    def metadata(self):
        return {'mime_type': 'image/jpeg', 'bytes': len(self.jpeg),
                'width': self.width, 'height': self.height,
                'coordinate_scale': self.coordinate_scale}


class Browser:
    """Control one running iPhone through its native VSOCK daemon.

    Keep one controlling Browser per guest. Operations on this object are
    serialized; navigation requests never assert that page content is ready.
    """
    def __init__(self, *, cid=3, timeout=120, guest=None, input_settle=2,
                 qmp_socket=None, display_timeout=15, touch_helper_sha256=None,
                 address_field_reader=None, touch_hold_ms=50):
        if not isinstance(input_settle, (int, float)) or not math.isfinite(input_settle) or not 0 <= input_settle <= 10:
            raise ValueError('input_settle must be between 0 and 10 seconds')
        if guest is not None and touch_helper_sha256 is not None:
            raise ValueError('Configure the touch helper on the supplied Guest instead')
        self.guest = guest if guest is not None else Guest(cid, timeout, touch_helper_sha256=touch_helper_sha256)
        self.input_settle = input_settle
        self.touch_hold_ms = contact_hold_ms(touch_hold_ms)
        self.qmp_socket = qmp_socket
        self.display_timeout = display_timeout
        if address_field_reader is not None and not callable(address_field_reader):
            raise ValueError('address_field_reader must be callable')
        self.address_field_reader = address_field_reader
        self._lock = threading.RLock()

    def health(self):
        with self._lock:
            return self.guest.health()

    def device(self):
        with self._lock:
            return self.guest.call('device.snapshot')

    def screen_state(self):
        """Read native lock/display notifications; does not establish readiness."""
        with self._lock:
            return self.guest.screen_state()

    def clock_state(self):
        """Read guest time and automatic mode without changing either."""
        with self._lock:
            return self.guest.clock_state()

    def sync_clock(self):
        """Offer host time through native timed, retaining its automatic/manual mode."""
        with self._lock:
            return self.guest.sync_clock()

    def home(self):
        """Send one native Home-button event; rendered Home is not verified."""
        with self._lock:
            result = self.guest.call('input.button', name='home')
            if not isinstance(result, dict) or result.get('button') != 'home':
                raise GuestError('Native Home button was not acknowledged')
            return {**result, 'home_screen_verified': False}

    def unlock(self):
        with self._lock:
            result = self.guest.call('screen.unlock', timeout=60)
            if not isinstance(result, dict) or result.get('locked') is not False:
                raise GuestError('Screen remains locked')
            return result

    def launch(self):
        """Unlock and request native Safari launch once, without navigating.

        Preserve the native foreground result, including an unverified launch.
        The caller must inspect the display separately; a transport failure
        never triggers another launch or unlock request.
        """
        with self._lock:
            self.unlock()
            result = self.guest.call('apps.launch', bundle_id=SAFARI)
            if (not isinstance(result, dict) or type(result.get('pid')) is not int
                    or result['pid'] < 0 or type(result.get('frontmost_verified')) is not bool):
                raise GuestError('Invalid native Safari launch response')
            return {**result, 'browser': SAFARI, 'requested': True,
                    'visual_readiness_verified': False, 'navigation_verified': False}

    def new_tab(self, *, expected_pid):
        """Request one native blank tab; its creation must be verified separately."""
        if type(expected_pid) is not int or not 1 < expected_pid <= 2147483647:
            raise ValueError('Creating a tab requires a valid expected process ID')
        with self._lock:
            self.unlock()
            foreground = self.guest.call('apps.foreground')
            if (not isinstance(foreground, dict) or foreground.get('verified') is not True
                    or foreground.get('bundle_id') != SAFARI
                    or type(foreground.get('pid')) is not int or foreground['pid'] != expected_pid):
                raise GuestError('Expected Safari process is not in the foreground; new tab not requested')
            result = self.guest.call('input.key', name='cmd+t')
            if (not isinstance(result, dict) or result.get('key') != 'cmd+t'
                    or result.get('dispatched') is not True):
                raise GuestError('Native new-tab input was not acknowledged; outcome is unknown')
            return {'requested': True, 'method': 'native_new_tab', 'tab_created_verified': False}

    def goto(self, url, *, reuse_tab=False, expected_url=None, expected_pid=None):
        """Request navigation through native Safari URL opening.

        Experimental tab reuse uses native HID events and address-field
        readback. A different foreground app uses explicit Safari URL opening
        instead of receiving text. Neither path asserts that content loaded.
        """
        url = web_url(url)
        if expected_url is not None:
            _expected_address(expected_url)
            if not reuse_tab:
                raise ValueError('An expected URL requires tab reuse')
        if expected_pid is not None:
            if type(expected_pid) is not int or not 1 < expected_pid <= 2147483647 or not reuse_tab:
                raise ValueError('An expected PID requires tab reuse and a valid process ID')
        with self._lock:
            self.unlock()
            foreground = self.guest.call('apps.foreground') if reuse_tab else None
            if reuse_tab and (not isinstance(foreground, dict)
                              or type(foreground.get('verified')) is not bool
                              or not isinstance(foreground.get('bundle_id'), str)):
                raise GuestError('Invalid native foreground response; navigation not submitted')
            if (foreground and foreground.get('verified') is True
                    and foreground.get('bundle_id') == SAFARI):
                pid = foreground.get('pid')
                if type(pid) is not int or not 1 < pid <= 2147483647:
                    raise GuestError('Cannot verify Safari process identity; native input not sent')
                if expected_pid is not None and pid != expected_pid:
                    raise GuestError('Safari process changed; navigation not submitted')
                field = self._focus_address_field(foreground)
                if expected_url is not None and not _matches_active_url(field.get('value'), expected_url):
                    raise GuestError('Active Safari tab does not contain the expected URL; navigation not submitted')
                self.guest.call('input.key', name='cmd+a')
                time.sleep(self.input_settle)
                self.guest.call('input.paste', text=url)
                time.sleep(self.input_settle)
                if self._address_field(foreground).get('value') != url:
                    raise GuestError('Safari address field does not contain the requested URL; navigation not submitted')
                self.guest.call('input.key', name='return')
                method = 'native_address_bar'
            else:
                if expected_url is not None or expected_pid is not None:
                    raise GuestError('Expected Safari tab is not in the foreground; navigation not submitted')
                self.guest.open(url)
                method = 'native_open_url'
            return {'url': url, 'browser': SAFARI, 'requested': True,
                    'method': method, 'navigation_verified': False}

    def _focus_address_field(self, foreground):
        if getattr(self.address_field_reader, 'supports_address_resolution', False) is True:
            return self._resolve_address_field(foreground)
        if self.address_field_reader is None:
            raise GuestError('Native address-field readback requires address_field_reader; configure NativeFocusReader')
        self.guest.call('input.key', name='cmd+l')
        time.sleep(self.input_settle)
        field = self._address_field(foreground, require_editing=False)
        if not field['traits'] & _AX_IS_EDITING:
            # Command-L can reveal the field without making it editable. Only
            # this verified native field supplies geometry for one focus tap.
            frame = field.get('frame', {})
            if not isinstance(frame, dict):
                raise GuestError('Safari address field has invalid focus geometry; text not sent')
            values = [frame.get(key) for key in ('x', 'y', 'width', 'height')]
            if (not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                        and math.isfinite(v) for v in values)
                    or min(values[:2]) < 0 or min(values[2:]) <= 0):
                raise GuestError('Safari address field has invalid focus geometry; text not sent')
            x, y, width, height = values
            center = (x + width / 2, y + height / 2)
            if not all(math.isfinite(v) for v in center):
                raise GuestError('Safari address field has invalid focus geometry; text not sent')
            self.tap(*center)
            time.sleep(self.input_settle)
            field = self._address_field(foreground)
        return field

    def _resolve_address_field(self, foreground):
        """Resolve the current native address control before sending input.

        A complete native discovery supplies geometry, not editing focus. Send
        at most one tap after verifying bounds and the foreground process, then
        require a fresh first-responder read before returning to the caller.
        """
        resolver = getattr(self.address_field_reader, 'resolve_address_field', None)
        if not callable(resolver):
            raise GuestError('Native address resolver is not configured')
        discover = getattr(self.address_field_reader, 'discover_address_field', None)
        field = discover(foreground['pid']) if callable(discover) else None
        if field is None:
            # A completed discovery can omit Safari's collapsed address bar.
            # Reveal it once, then require entirely fresh native focus evidence.
            ack = self.guest.call('input.key', name='cmd+l')
            if not isinstance(ack, dict) or ack.get('key') != 'cmd+l' or ack.get('dispatched') is not True:
                raise GuestError('Native address shortcut not acknowledged; outcome is unknown')
            time.sleep(self.input_settle)
            field = resolver(foreground['pid'])
        if (not isinstance(field, dict) or type(field.get('pid')) is not int
                or field['pid'] != foreground['pid'] or type(field.get('traits')) is not int
                or field['traits'] < 0):
            raise GuestError('Combined query did not establish owned native URL field')
        if field.get('source') == 'ax_first_responder':
            if field.get('identifier') != 'URL':
                raise GuestError('Native first responder is not the URL field')
            if field['traits'] & _AX_IS_EDITING:
                return field
        elif field.get('source') == 'ax_address_field':
            if field.get('identifier') not in ('URL', 'TabBarItemTitle') or field.get('enabled') is not True:
                raise GuestError('Discovered native field is not an enabled URL control')
        else:
            raise GuestError('Unknown native field source')
        frame = field.get('frame')
        values = [frame.get(k) for k in ('x', 'y', 'width', 'height')] if isinstance(frame, dict) else []
        if (len(values) != 4 or not all(type(v) in (int, float) and math.isfinite(v) for v in values)
                or min(values[:2]) < 0 or min(values[2:]) <= 0):
            raise GuestError('Invalid native URL field bounds; focus tap not sent')
        x, y, width, height = values
        screen = Screenshot.from_native(self.guest.call('screen.screenshot'))
        if (x + width > screen.width / screen.coordinate_scale
                or y + height > screen.height / screen.coordinate_scale):
            raise GuestError('Native URL field outside screen bounds; focus tap not sent')
        current = self.guest.call('apps.foreground')
        if (not isinstance(current, dict) or current.get('verified') is not True
                or current.get('bundle_id') != SAFARI or type(current.get('pid')) is not int
                or current['pid'] != foreground['pid']):
            raise GuestError('Safari foreground changed; focus tap not sent')
        self.tap(x + width / 2, y + height / 2)
        time.sleep(self.input_settle)
        return self._address_field(current)

    def close_tab(self, *, expected_url, expected_pid):
        """Send Command-W once after verifying the active tab's editing address.

        Safari may omit the HTTP(S) scheme or a bare root's trailing slash.
        The caller must establish ownership and uniqueness across those forms,
        then verify disappearance. Input is never repeated after a failure.
        """
        _expected_address(expected_url)
        if type(expected_pid) is not int or not 1 < expected_pid <= 2147483647:
            raise ValueError('Closing a tab requires a valid expected process ID')
        with self._lock:
            foreground = self.guest.call('apps.foreground')
            if (not isinstance(foreground, dict) or foreground.get('verified') is not True
                    or foreground.get('bundle_id') != SAFARI
                    or type(foreground.get('pid')) is not int or foreground['pid'] != expected_pid):
                raise GuestError('Expected Safari tab is not in the foreground; tab not closed')
            if not _matches_active_url(self._focus_address_field(foreground).get('value'), expected_url):
                raise GuestError('Active Safari tab does not contain the expected URL; tab not closed')
            result = self.guest.call('input.key', name='cmd+w')
            if not isinstance(result, dict) or result.get('key') != 'cmd+w' or result.get('dispatched') is not True:
                raise GuestError('Native tab close was not acknowledged; outcome is unknown')
            return {'requested': True, 'method': 'native_close_tab', 'tab_closed_verified': False}

    def _address_field(self, foreground, *, require_editing=True):
        if self.address_field_reader is None:
            raise GuestError('Native address-field readback requires address_field_reader; configure NativeFocusReader')
        field = self.address_field_reader(foreground['pid'])
        if field is not None:
            if (not isinstance(field, dict) or field.get('source') != 'ax_first_responder'
                    or type(field.get('pid')) is not int or field['pid'] != foreground['pid']):
                raise GuestError('Cannot verify Safari focused-field ownership')
            if field.get('identifier') == 'URL':
                if type(field.get('traits')) is not int or field['traits'] < 0:
                    raise GuestError('Safari focused-field editing state is unavailable')
                if require_editing and not field['traits'] & _AX_IS_EDITING:
                    raise GuestError('Safari address field is not editing; navigation not submitted')
                return field
        # The daemon flat-tree path can leave system accessibility/automation
        # switches enabled. Do not use it as an implicit fallback for missing
        # focus, missing configuration, or an unsuccessful native helper read.
        raise GuestError('Safari address field is not focused; navigation not submitted')

    def screenshot(self, path=None, *, overwrite=False):
        """Capture native JPEG, or host PNG when qmp_socket is configured."""
        with self._lock:
            result = (capture_display(self.qmp_socket, timeout=self.display_timeout)
                      if self.qmp_socket is not None else
                      Screenshot.from_native(self.guest.call('screen.screenshot')))
            if path is not None:
                result.save(path, overwrite=overwrite)
            return result

    def tap(self, x, y, *, hold_ms=_CONFIGURED_TOUCH_HOLD):
        """Send one balanced native contact; a longer hold can open context menus.

        Omitting hold_ms uses this Browser's touch_hold_ms (default 50). An explicit
        integer 0–1000 overrides it. Zero skips the voluntary hold; dispatch
        can still delay release. Delivery and gesture recognition are unverified.
        """
        if hold_ms is _CONFIGURED_TOUCH_HOLD:
            hold_ms = self.touch_hold_ms
        contact_hold_ms(hold_ms)
        with self._lock:
            # Preserve the existing call contract for custom tap-only guests.
            if hold_ms == 50:
                return self.guest.tap(x, y)
            return self.guest.tap(x, y, hold_ms=hold_ms)

    def type_text(self, text):
        if not isinstance(text, str) or not 0 < len(text.encode('utf-8')) <= 65536:
            raise ValueError('text must contain 1–65536 UTF-8 bytes')
        with self._lock:
            return self.guest.call('input.paste', text=text)

    def press(self, key):
        if not isinstance(key, str) or not key:
            raise ValueError('key must be a nonempty native key name')
        with self._lock:
            return self.guest.call('input.key', name=key)

    def swipe(self, x1, y1, x2, y2, *, seconds=0.5):
        values = (x1, y1, x2, y2, seconds)
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values):
            raise ValueError('Swipe coordinates and duration must be finite numbers')
        if min(x1, y1, x2, y2) < 0 or not 0.1 <= seconds <= 10:
            raise ValueError('Swipe needs nonnegative points and 0.1–10 seconds')
        with self._lock:
            return self.guest.call('input.swipe', x1=x1, y1=y1, x2=x2, y2=y2, seconds=seconds)
