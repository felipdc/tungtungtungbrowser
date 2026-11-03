import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from tungtungtungbrowser import Browser, GuestError, Screenshot


class NativeFixture:
    def __init__(self, foreground='com.apple.mobilesafari', verified=True, locked=False):
        self.foreground = foreground
        self.verified = verified
        self.locked = locked
        self.calls = []
        self.fail = None
        self.address_visible = True
        self.address_value = "https://example.org/"
        self.drop_text = False

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == self.fail:
            raise GuestError('Native operation failed')
        if method == 'screen.unlock':
            return {'locked': self.locked}
        if method == 'apps.foreground':
            return {'bundle_id': self.foreground, 'verified': self.verified, 'pid': 301}
        if method == 'input.paste' and not self.drop_text:
            self.address_value = params['text']
        if method == 'ui.tree':
            return {'source': 'ax', 'pid': 301, 'elements': [
                {'identifier': 'URL', 'visible': True, 'enabled': True, 'value': self.address_value}
            ] if self.address_visible else []}
        return {'accepted': True}

    def open(self, url):
        return self.call('apps.open_url', url=url, bundle_id='com.apple.mobilesafari')


class BrowserContractTest(unittest.TestCase):
    def test_home_failure_or_malformed_acknowledgement_never_repeats_input(self):
        for result in [None, {}, {'button': 'wake'}, GuestError('transport timed out')]:
            with self.subTest(result=result):
                guest = Mock()
                if isinstance(result, Exception):
                    guest.call.side_effect = result
                else:
                    guest.call.return_value = result
                with self.assertRaises(GuestError):
                    Browser(guest=guest).home()
                guest.call.assert_called_once_with('input.button', name='home')

    def test_default_navigation_explicitly_targets_safari_without_input(self):
        guest = NativeFixture()
        result = Browser(guest=guest).goto('https://example.com/')
        self.assertEqual(result['method'], 'native_open_url')
        self.assertFalse(result['navigation_verified'])
        self.assertEqual(guest.calls, [
            ('screen.unlock', {'timeout': 60}),
            ('apps.open_url', {'url': 'https://example.com/', 'bundle_id': 'com.apple.mobilesafari'})])

    def test_reuse_navigation_uses_native_input_and_does_not_claim_page_success(self):
        guest = NativeFixture()
        browser = Browser(guest=guest, input_settle=0)
        result = browser.goto('https://example.com/', reuse_tab=True)
        self.assertEqual(result['method'], 'native_address_bar')
        self.assertFalse(result['navigation_verified'])
        self.assertIn(('input.paste', {'text': 'https://example.com/'}), guest.calls)
        self.assertNotIn('apps.open_url', [method for method, _ in guest.calls])

    def test_other_or_unverified_foreground_never_receives_url_typing(self):
        for foreground, verified in [('com.example.notes', True), ('com.apple.mobilesafari', False)]:
            with self.subTest(foreground=foreground, verified=verified):
                guest = NativeFixture(foreground, verified)
                result = Browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
                self.assertEqual(result['method'], 'native_open_url')
                self.assertNotIn('input.paste', [method for method, _ in guest.calls])
                self.assertEqual(guest.calls[-1][1]['bundle_id'], 'com.apple.mobilesafari')

    def test_missing_address_field_never_receives_text(self):
        guest = NativeFixture()
        guest.address_visible = False
        with self.assertRaisesRegex(GuestError, 'address field'):
            Browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
        self.assertNotIn('input.paste', [method for method, _ in guest.calls])

    def test_dropped_input_is_not_submitted_as_a_wrong_url(self):
        guest = NativeFixture()
        guest.drop_text = True
        with self.assertRaisesRegex(GuestError, 'requested URL'):
            Browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
        self.assertNotIn(('input.key', {'name': 'return'}), guest.calls)

    def test_invalid_url_has_no_device_side_effect(self):
        guest = NativeFixture()
        with self.assertRaises(ValueError):
            Browser(guest=guest).goto('javascript:alert(1)')
        self.assertEqual(guest.calls, [])

    def test_locked_screen_prevents_navigation(self):
        guest = NativeFixture(locked=True)
        with self.assertRaisesRegex(GuestError, 'locked'):
            Browser(guest=guest).goto('https://example.com/')
        self.assertEqual([method for method, _ in guest.calls], ['screen.unlock'])

    def test_failed_text_entry_does_not_submit(self):
        guest = NativeFixture()
        guest.fail = 'input.paste'
        with self.assertRaises(GuestError):
            Browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
        self.assertNotIn(('input.key', {'name': 'return'}), guest.calls)

    def test_screenshot_rejects_invalid_geometry_and_preserves_existing_file(self):
        data = {'mime_type': 'image/jpeg', 'width': 430, 'height': 932,
                'data': base64.b64encode(b'\xff\xd8fixture\xff\xd9').decode()}
        with self.assertRaises(GuestError):
            Screenshot.from_native(data | {'width': -1})
        with self.assertRaises(GuestError):
            Screenshot.from_native(data | {'data': 'corrupt!'})
        shot = Screenshot.from_native(data)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'screen.jpg'
            shot.save(target)
            with self.assertRaises(FileExistsError):
                shot.save(target)
            self.assertEqual(target.read_bytes(), shot.jpeg)


if __name__ == '__main__':
    unittest.main()
