"""Explicit experimental API settings; loading a profile sends no native input."""
import json
from pathlib import Path

from .browser import Browser
from .guest import Guest
from .inspector import InspectorSession
from .inspector_transport import InspectorTransport
from .native_focus import NativeFocusReader
from .native_keyboard import helper_path as keyboard_path
from .native_touch import helper_path as touch_path


class BrowserProfile:
    """Load the records produced by scripts/install_browser_helpers.py.

    This is controller configuration, not isolated cookies or a VM launcher.
    The runtime, firmware prerequisites and live bridge are prepared separately.
    Existing Browser/InspectorSession defaults are unchanged.
    """
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        profile = self._read('profile.json')
        if (type(profile.get('version')) is not int or profile['version'] != 1
                or profile.get('name') != 'api-experimental'):
            raise ValueError('Unsupported browser profile')
        self.cid = profile.get('cid')
        if type(self.cid) is not int or not 3 <= self.cid < 2**32 - 1:
            raise ValueError('Invalid profile guest CID')
        self.touch = self._read('touch/install.json')
        self.keyboard = self._read('keyboard/install.json')
        self.focus_path = self.directory / 'focus/install.json'
        self._read('focus/install.json')
        self.bridge = self._read('bridge/deploy.json')
        for report, path in ((self.touch, touch_path), (self.keyboard, keyboard_path)):
            if report.get('guest_path') != path(report['helper_sha256']):
                raise ValueError('Helper path does not match its digest')

    def _read(self, name):
        path = self.directory / name
        if path.stat().st_size > 256 * 1024:
            raise ValueError('Profile record exceeds 256 KiB')
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or value.get('status') != 'complete':
            raise ValueError('Browser profile contains an incomplete record: ' + name)
        return value

    def browser(self, *, qmp_socket=None, guest=None, address_focus='auto', focus_read_attempts=2,
                address_discovery_timeout=0):
        """Create one controller; leave qmp_socket unset for native device JPEGs."""
        if guest is None:
            guest = Guest(cid=self.cid, timeout=120,
                          touch_helper_sha256=self.touch['helper_sha256'],
                          keyboard_helper_sha256=self.keyboard['helper_sha256'])
        else:
            # The caller owns a custom transport's endpoint; preserve helper selection.
            if (guest.cid != self.cid
                    or guest.touch_helper_sha256 != self.touch['helper_sha256']
                    or guest.keyboard_helper_sha256 != self.keyboard['helper_sha256']):
                raise ValueError('Supplied Guest differs from the deployment profile')
        return Browser(guest=guest, input_settle=5, touch_hold_ms=0, qmp_socket=qmp_socket,
                       address_focus=address_focus, address_discovery_timeout=address_discovery_timeout,
                       address_field_reader=NativeFocusReader.from_deployment(
                           guest, self.focus_path, max_read_attempts=focus_read_attempts))

    def session(self, *, browser, url=None, new_tab_url=None, new_tab=False, retain_tab=False):
        """Attach a tab or create one, optionally retaining it for later sessions."""
        if browser.guest.cid != self.cid:
            raise ValueError('Browser and deployment profile target different guest CIDs')
        return InspectorSession(browser=browser, url=url, new_tab_url=new_tab_url, new_tab=new_tab, retain_tab=retain_tab, timeout=300,
                                transport=InspectorTransport(cid=self.cid, timeout=240,
                                                             max_open_attempts=4))
