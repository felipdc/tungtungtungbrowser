import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call

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
        self.editing = True
        self.focus_on_tap = True
        self.address_frame = {'x': 46, 'y': 770, 'width': 213, 'height': 48}
        self.address_trees = []

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
            if self.address_trees:
                tree = self.address_trees.pop(0)
                if isinstance(tree, Exception):
                    raise tree
                return tree
            return {'source': 'ax', 'pid': 301, 'elements': [
                {'identifier': 'URL', 'visible': True, 'enabled': True, 'value': self.address_value,
                 'traits': 146031247360 if self.editing else 146029150208,
                 'frame': self.address_frame}
            ] if self.address_visible else []}
        return {'accepted': True}

    def tap(self, x, y):
        result = self.call('input.tap', x=x, y=y)
        if self.focus_on_tap:
            self.editing = True
        return result

    def open(self, url):
        return self.call('apps.open_url', url=url, bundle_id='com.apple.mobilesafari')


class BrowserContractTest(unittest.TestCase):
    def test_launch_preserves_native_foreground_result_without_navigation_or_polling(self):
        for native in [{'pid': 301, 'frontmost_verified': True},
                       {'pid': 0, 'frontmost_verified': False, 'warning': 'Foreground unknown'}]:
            with self.subTest(native=native):
                guest = Mock()
                guest.call.side_effect = [{'locked': False}, native]
                result = Browser(guest=guest).launch()
                self.assertEqual(guest.call.call_args_list, [
                    call('screen.unlock', timeout=60),
                    call('apps.launch', bundle_id='com.apple.mobilesafari')])
                guest.open.assert_not_called()
                self.assertEqual({k: result[k] for k in native}, native)
                self.assertFalse(result['navigation_verified'])
                self.assertFalse(result['visual_readiness_verified'])

    def test_launch_does_not_continue_after_failed_unlock_or_repeat_uncertain_actions(self):
        for replies in [[{'locked': True}], [None], [GuestError('unlock timed out')],
                        [{'locked': False}, GuestError('launch connection reset')]]:
            with self.subTest(replies=replies):
                guest = Mock()
                guest.call.side_effect = replies
                with self.assertRaises(GuestError):
                    Browser(guest=guest).launch()
                self.assertEqual(guest.call.call_count, len(replies))
                guest.open.assert_not_called()

    def test_malformed_launch_reply_never_becomes_foreground_success(self):
        for reply in [None, {}, {'pid': True, 'frontmost_verified': True},
                      {'pid': -1, 'frontmost_verified': True},
                      {'pid': 301, 'frontmost_verified': 'true'}]:
            with self.subTest(reply=reply):
                guest = Mock()
                guest.call.side_effect = [{'locked': False}, reply]
                with self.assertRaisesRegex(GuestError, 'launch response'):
                    Browser(guest=guest).launch()
                self.assertEqual(guest.call.call_count, 2)

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

    def test_truncated_missing_identifier_gets_one_read_without_repeating_input(self):
        guest = NativeFixture()
        guest.address_trees = [{'source': 'ax', 'pid': 301, 'truncated': True,
                               'elements': [{'identifier': '', 'visible': True,
                                             'enabled': True, 'traits': 146031247360,
                                             'value': 'https://example.org/'}]}]
        result = Browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
        self.assertEqual(result['method'], 'native_address_bar')
        names = [method for method, _ in guest.calls]
        self.assertEqual(guest.calls.count(('input.key', {'name': 'cmd+l'})), 1)
        self.assertEqual(names.count('ui.tree'), 3)  # Two before typing; one URL readback.
        self.assertEqual(names.count('input.paste'), 1)
        self.assertNotIn('input.tap', names)
        self.assertEqual(names[:5], ['screen.unlock', 'apps.foreground', 'input.key',
                                     'ui.tree', 'ui.tree'])

    def test_incomplete_field_recheck_is_bounded_and_does_not_mask_transport_or_owner_failure(self):
        incomplete = {'source': 'ax', 'pid': 301, 'truncated': True, 'elements': []}
        for second in [incomplete, GuestError('read timed out'),
                       {'source': 'ax', 'pid': 999, 'truncated': True, 'elements': []}]:
            with self.subTest(second=second):
                guest = NativeFixture()
                guest.address_trees = [incomplete, second]
                with self.assertRaises(GuestError):
                    Browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
                names = [method for method, _ in guest.calls]
                self.assertEqual(names.count('ui.tree'), 2)
                self.assertEqual(guest.calls.count(('input.key', {'name': 'cmd+l'})), 1)
                self.assertNotIn('input.tap', names)
                self.assertNotIn('input.paste', names)
                self.assertNotIn(('input.key', {'name': 'return'}), guest.calls)

    def test_complete_missing_field_ambiguous_fields_and_wrong_owner_are_not_requeried(self):
        field = {'identifier': 'URL', 'visible': True, 'enabled': True,
                 'traits': 146031247360, 'value': 'https://example.org/'}
        for tree in [{'source': 'ax', 'pid': 301, 'truncated': False, 'elements': []},
                     {'source': 'ax', 'pid': 301, 'truncated': True, 'elements': [field, field]},
                     {'source': 'ax', 'pid': 999, 'truncated': True, 'elements': []}]:
            with self.subTest(tree=tree):
                guest = NativeFixture()
                guest.address_trees = [tree]
                with self.assertRaises(GuestError):
                    Browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
                names = [method for method, _ in guest.calls]
                self.assertEqual(names.count('ui.tree'), 1)
                self.assertNotIn('input.paste', names)

    def test_dropped_input_is_not_submitted_as_a_wrong_url(self):
        guest = NativeFixture()
        guest.drop_text = True
        with self.assertRaisesRegex(GuestError, 'requested URL'):
            Browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
        self.assertNotIn(('input.key', {'name': 'return'}), guest.calls)

    def test_visible_but_unfocused_address_field_is_focused_before_typing(self):
        guest = NativeFixture()
        guest.editing = False
        result = Browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
        self.assertEqual(result['method'], 'native_address_bar')
        self.assertEqual([params for method, params in guest.calls if method == 'input.tap'],
                         [{'x': 152.5, 'y': 794.0}])
        names = [method for method, _ in guest.calls]
        self.assertLess(names.index('input.tap'), names.index('input.paste'))
        self.assertIn(('input.key', {'name': 'cmd+a'}), guest.calls)

    def test_unconfirmed_or_failed_focus_never_sends_text_or_repeats_tap(self):
        for failure in [None, 'input.tap']:
            with self.subTest(failure=failure):
                guest = NativeFixture()
                guest.editing = guest.focus_on_tap = False
                guest.fail = failure
                with self.assertRaises(GuestError):
                    Browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
                names = [method for method, _ in guest.calls]
                self.assertEqual(names.count('input.tap'), 1)
                self.assertNotIn('input.paste', names)
                self.assertNotIn(('input.key', {'name': 'return'}), guest.calls)

    def test_invalid_focus_geometry_never_sends_a_tap_or_text(self):
        for frame in [None, [], {}, {'x': -1, 'y': 2, 'width': 3, 'height': 4},
                      {'x': 1, 'y': 2, 'width': 0, 'height': 4},
                      {'x': float('nan'), 'y': 2, 'width': 3, 'height': 4},
                      {'x': True, 'y': 2, 'width': 3, 'height': 4}]:
            with self.subTest(frame=frame):
                guest = NativeFixture()
                guest.editing = False
                guest.address_frame = frame
                with self.assertRaisesRegex(GuestError, 'focus geometry'):
                    Browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
                self.assertNotIn('input.tap', [method for method, _ in guest.calls])
                self.assertNotIn('input.paste', [method for method, _ in guest.calls])

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
