"""Small synchronous API for native Safari input and screen capture."""
import base64
import binascii
from dataclasses import dataclass, field
import math
from pathlib import Path
import threading
import time

from .guest import Guest, GuestError, web_url
from .display import capture_display

SAFARI = 'com.apple.mobilesafari'
# Native accessibility kAXIsEditingTrait. See docs/native-control-audit.md.
_AX_IS_EDITING = 1 << 21


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
                 qmp_socket=None, display_timeout=15):
        if not isinstance(input_settle, (int, float)) or not math.isfinite(input_settle) or not 0 <= input_settle <= 10:
            raise ValueError('input_settle must be between 0 and 10 seconds')
        self.guest = guest if guest is not None else Guest(cid, timeout)
        self.input_settle = input_settle
        self.qmp_socket = qmp_socket
        self.display_timeout = display_timeout
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

    def goto(self, url, *, reuse_tab=False):
        """Request navigation through native Safari URL opening.

        Experimental tab reuse uses native HID events and address-field
        readback. A different foreground app uses explicit Safari URL opening
        instead of receiving text. Neither path asserts that content loaded.
        """
        url = web_url(url)
        with self._lock:
            self.unlock()
            foreground = self.guest.call('apps.foreground') if reuse_tab else None
            if (foreground and foreground.get('verified') is True
                    and foreground.get('bundle_id') == SAFARI):
                self.guest.call('input.key', name='cmd+l')
                time.sleep(self.input_settle)
                field = self._address_field(foreground, require_editing=False)
                if not field['traits'] & _AX_IS_EDITING:
                    # Command-L can reveal the field without making it editable.
                    # Its center coordinates become zero in some editing states;
                    # use the verified field's frame for this one focus attempt.
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
                    self.guest.tap(*center)
                    time.sleep(self.input_settle)
                    self._address_field(foreground)
                self.guest.call('input.key', name='cmd+a')
                time.sleep(self.input_settle)
                self.guest.call('input.paste', text=url)
                time.sleep(self.input_settle)
                if self._address_field(foreground).get('value') != url:
                    raise GuestError('Safari address field does not contain the requested URL; navigation not submitted')
                self.guest.call('input.key', name='return')
                method = 'native_address_bar'
            else:
                self.guest.open(url)
                method = 'native_open_url'
            return {'url': url, 'browser': SAFARI, 'requested': True,
                    'method': method, 'navigation_verified': False}

    def _address_field(self, foreground, *, require_editing=True):
        for attempt in range(2):
            tree = self.guest.call('ui.tree', max_elements=30, visible_only=True, limit=30)
            if tree.get('source') != 'ax' or tree.get('pid') != foreground.get('pid'):
                raise GuestError('Cannot verify Safari address-field ownership')
            fields = [element for element in tree.get('elements', [])
                      if element.get('identifier') == 'URL'
                      and element.get('visible') and element.get('enabled')]
            if len(fields) == 1:
                break
            # A completed but truncated native query can omit field attributes.
            # Make at most one more read; never infer identity from its value,
            # retry a failed transport, or repeat the preceding native input.
            if not fields and tree.get('truncated') is True and attempt == 0:
                time.sleep(self.input_settle)
                continue
            raise GuestError('Safari address field is not active; navigation not submitted')
        field = fields[0]
        traits = field.get('traits')
        if type(traits) is not int or traits < 0:
            raise GuestError('Safari address-field editing state is unavailable; navigation not submitted')
        if require_editing and not traits & _AX_IS_EDITING:
            raise GuestError('Safari address field is not editing; navigation not submitted')
        return field

    def screenshot(self, path=None, *, overwrite=False):
        """Capture native JPEG, or host PNG when qmp_socket is configured."""
        with self._lock:
            result = (capture_display(self.qmp_socket, timeout=self.display_timeout)
                      if self.qmp_socket is not None else
                      Screenshot.from_native(self.guest.call('screen.screenshot')))
            if path is not None:
                result.save(path, overwrite=overwrite)
            return result

    def tap(self, x, y):
        with self._lock:
            return self.guest.tap(x, y)

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
