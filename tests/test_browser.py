import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call

from tungtungtungbrowser import Browser, GuestError, Screenshot
from tungtungtungbrowser.guest import Guest


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

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == self.fail:
            raise GuestError('Native operation failed')
        if method == 'screen.unlock':
            return {'locked': self.locked}
        if method == 'apps.foreground':
            return {'bundle_id': self.foreground, 'verified': self.verified, 'pid': 301}
        if method == 'input.touch_sequence':
            if self.focus_on_tap:
                self.editing = True
            return {'events': 2, 'normalized': False, 'finger_down': False, 'duration_ms': 50}
        if method == 'input.paste' and not self.drop_text:
            self.address_value = params['text']
        if method == 'input.key':
            return {'key': params['name'], 'dispatched': True}
        if method == 'ui.tree':
            raise AssertionError('Implicit daemon UI-tree query is forbidden')
        return {'accepted': True}

    def focused_field(self, pid):
        self.calls.append(('native.focus', {'pid': pid}))
        if self.fail == 'native.focus':
            raise GuestError('Native focus query failed')
        if not self.address_visible:
            return None
        return {'source': 'ax_first_responder', 'pid': pid, 'identifier': 'URL',
                'value': self.address_value, 'traits': 146031247360 if self.editing else 146029150208,
                'frame': self.address_frame}

    def tap(self, x, y):
        return Guest.tap(self, x, y)

    def open(self, url):
        return self.call('apps.open_url', url=url, bundle_id='com.apple.mobilesafari')


def controlled_browser(*, guest, **options):
    if isinstance(guest, NativeFixture):
        options.setdefault('address_field_reader', guest.focused_field)
    return Browser(guest=guest, **options)


class BrowserContractTest(unittest.TestCase):
    def test_blank_placeholder_requires_independent_matching_native_editing_field(self):
        for expected, change, accepted in [
                ('about:blank', {}, True),
                ('https://other.invalid/', {}, False),
                ('about:blank', {'placeholder': ''}, False),
                ('about:blank', {'placeholder': 'different'}, False),
                ('about:blank', {'value': 'different'}, False),
                ('about:blank', {'pid': 302}, False),
                ('about:blank', {'identifier': 'page-input'}, False),
                ('about:blank', {'traits': 0}, False)]:
            with self.subTest(expected=expected, change=change):
                guest = NativeFixture(); guest.address_value = 'Buscar ou digitar site'
                reader = Mock(side_effect=guest.focused_field)
                reader.supports_placeholder_read = True
                reader.read_placeholder.side_effect = lambda pid: {
                    **guest.focused_field(pid), 'placeholder': 'Buscar ou digitar site', **change}
                browser = controlled_browser(guest=guest, address_field_reader=reader,
                                             input_settle=0, address_focus='shortcut')
                if accepted:
                    browser.goto('https://next.invalid/', reuse_tab=True, expected_pid=301, expected_url=expected)
                    self.assertEqual(guest.calls[-1], ('input.key', {'name': 'return'}))
                else:
                    with self.assertRaises(GuestError):
                        browser.goto('https://next.invalid/', reuse_tab=True, expected_pid=301, expected_url=expected)
                    self.assertNotIn('input.paste', [m for m, _ in guest.calls])
                self.assertEqual(reader.read_placeholder.call_count, int(expected == 'about:blank'))

    def test_new_tab_requires_exact_foreground_process_and_one_acknowledged_key(self):
        guest = NativeFixture()
        result = controlled_browser(guest=guest).new_tab(expected_pid=301)
        self.assertEqual(guest.calls, [('screen.unlock', {'timeout': 60}),
                                      ('apps.foreground', {}), ('input.key', {'name': 'cmd+t'})])
        self.assertTrue(result['requested'])
        self.assertFalse(result['tab_created_verified'])
        for guest, pid in [(NativeFixture(foreground='com.example.notes'), 301),
                           (NativeFixture(verified=False), 301), (NativeFixture(), 302)]:
            with self.assertRaises(GuestError):
                controlled_browser(guest=guest).new_tab(expected_pid=pid)
            self.assertFalse(any(method.startswith('input.') for method, _ in guest.calls))

    def test_new_tab_lost_or_invalid_acknowledgement_is_not_replayed(self):
        for reply in [None, {'key': 'cmd+t', 'dispatched': False},
                      {'key': 'cmd+w', 'dispatched': True}, GuestError('Lost reply')]:
            guest = Mock()
            guest.call.side_effect = [{'locked': False},
                {'bundle_id': 'com.apple.mobilesafari', 'verified': True, 'pid': 301}, reply]
            with self.assertRaises(GuestError):
                controlled_browser(guest=guest).new_tab(expected_pid=301)
            self.assertEqual(guest.call.call_count, 3)

    def test_blank_expected_address_allows_only_empty_or_exact_blank_state(self):
        for value in ['', 'about:blank', ' ', 'about:blank#other', 'https://other.invalid/']:
            guest = NativeFixture()
            guest.address_value = value
            browser = controlled_browser(guest=guest, input_settle=0)
            if value in ('', 'about:blank'):
                browser.goto('https://next.invalid/', reuse_tab=True,
                             expected_pid=301, expected_url='about:blank')
                self.assertEqual(guest.calls[-1], ('input.key', {'name': 'return'}))
            else:
                with self.assertRaises(GuestError):
                    browser.goto('https://next.invalid/', reuse_tab=True,
                                 expected_pid=301, expected_url='about:blank')
                self.assertNotIn('input.paste', [method for method, _ in guest.calls])
        with self.assertRaises(ValueError):
            controlled_browser(guest=NativeFixture()).goto('about:blank')

    def test_launch_preserves_native_foreground_result_without_navigation_or_polling(self):
        for native in [{'pid': 301, 'frontmost_verified': True},
                       {'pid': 0, 'frontmost_verified': False, 'warning': 'Foreground unknown'}]:
            with self.subTest(native=native):
                guest = Mock()
                guest.call.side_effect = [{'locked': False}, native]
                result = controlled_browser(guest=guest).launch()
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
                    controlled_browser(guest=guest).launch()
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
                    controlled_browser(guest=guest).launch()
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
                    controlled_browser(guest=guest).home()
                guest.call.assert_called_once_with('input.button', name='home')

    def test_default_navigation_explicitly_targets_safari_without_input(self):
        guest = NativeFixture()
        result = controlled_browser(guest=guest).goto('https://example.com/')
        self.assertEqual(result['method'], 'native_open_url')
        self.assertFalse(result['navigation_verified'])
        self.assertEqual(guest.calls, [
            ('screen.unlock', {'timeout': 60}),
            ('apps.open_url', {'url': 'https://example.com/', 'bundle_id': 'com.apple.mobilesafari'})])

    def test_reuse_navigation_uses_native_input_and_does_not_claim_page_success(self):
        guest = NativeFixture()
        browser = controlled_browser(guest=guest, input_settle=0)
        result = browser.goto('https://example.com/', reuse_tab=True)
        self.assertEqual(result['method'], 'native_address_bar')
        self.assertFalse(result['navigation_verified'])
        self.assertIn(('input.paste', {'text': 'https://example.com/'}), guest.calls)
        self.assertNotIn('apps.open_url', [method for method, _ in guest.calls])

    def test_expected_tab_url_and_pid_guard_navigation_before_text(self):
        for expected_url, expected_pid in [('https://other.invalid/', 301),
                                           ('https://example.org/', 302)]:
            guest = NativeFixture()
            with self.assertRaises(GuestError):
                controlled_browser(guest=guest, input_settle=0).goto('https://next.invalid/', reuse_tab=True,
                    expected_url=expected_url, expected_pid=expected_pid)
            self.assertNotIn('input.paste', [m for m, _ in guest.calls])
            self.assertNotIn('apps.open_url', [m for m, _ in guest.calls])
        guest = NativeFixture()
        controlled_browser(guest=guest, input_settle=0).goto('https://next.invalid/', reuse_tab=True,
            expected_url='https://example.org/', expected_pid=301)
        self.assertEqual(guest.calls[-1], ('input.key', {'name': 'return'}))

    def test_guarded_navigation_never_opens_another_tab_on_foreground_mismatch(self):
        guest = NativeFixture(foreground='com.example.notes')
        with self.assertRaises(GuestError):
            controlled_browser(guest=guest).goto('https://next.invalid/', reuse_tab=True,
                                    expected_url='https://example.org/')
        self.assertFalse(any(m.startswith('input.') or m == 'apps.open_url' for m, _ in guest.calls))

    def test_active_address_may_omit_only_scheme_but_typed_url_still_requires_exact_readback(self):
        expected = 'http://fixture.invalid:8767/path?q=1#owned'
        for value in [expected, 'fixture.invalid:8767/path?q=1#owned',
                      'fixture.invalid', 'fixture.invalid:8767/path?q=1',
                      'fixture.invalid:8767/path?q=2#owned', 'https://fixture.invalid:8767/path?q=1#owned']:
            guest = NativeFixture()
            guest.address_value = value
            browser = controlled_browser(guest=guest, input_settle=0)
            if value in (expected, expected[7:]):
                browser.close_tab(expected_url=expected, expected_pid=301)
                self.assertEqual(guest.calls[-1], ('input.key', {'name': 'cmd+w'}))
            else:
                with self.assertRaises(GuestError):
                    browser.close_tab(expected_url=expected, expected_pid=301)
                self.assertNotIn(('input.key', {'name': 'cmd+w'}), guest.calls)
        guest = NativeFixture()
        guest.address_value = expected[7:]
        guest.drop_text = True
        with self.assertRaises(GuestError):
            controlled_browser(guest=guest, input_settle=0).goto(expected, reuse_tab=True,
                expected_url=expected, expected_pid=301)
        self.assertIn(('input.paste', {'text': expected}), guest.calls)
        self.assertNotIn(('input.key', {'name': 'return'}), guest.calls)

    def test_tab_close_verifies_url_sends_one_key_and_does_not_claim_disappearance(self):
        guest = NativeFixture()
        result = controlled_browser(guest=guest, input_settle=0).close_tab(
            expected_url='https://example.org/', expected_pid=301)
        self.assertFalse(result['tab_closed_verified'])
        self.assertEqual(guest.calls.count(('input.key', {'name': 'cmd+w'})), 1)
        for expected_url, expected_pid in [('https://other.invalid/', 301),
                                           ('https://example.org/', 302)]:
            guest = NativeFixture()
            with self.assertRaises(GuestError):
                controlled_browser(guest=guest, input_settle=0).close_tab(
                    expected_url=expected_url, expected_pid=expected_pid)
            self.assertNotIn(('input.key', {'name': 'cmd+w'}), guest.calls)

    def test_root_address_can_omit_slash_without_shortening_paths_or_changing_scheme(self):
        cases = [('https://fixture.invalid/', 'https://fixture.invalid', True),
                 ('https://fixture.invalid/', 'fixture.invalid', True),
                 ('https://fixture.invalid/', 'http://fixture.invalid', False),
                 ('https://fixture.invalid/path/', 'https://fixture.invalid/path', False),
                 ('https://fixture.invalid/?q=1', 'https://fixture.invalid?q=1', False)]
        for expected, value, accepted in cases:
            guest = NativeFixture()
            guest.address_value = value
            browser = controlled_browser(guest=guest, input_settle=0)
            if accepted:
                browser.close_tab(expected_url=expected, expected_pid=301)
            else:
                with self.assertRaises(GuestError):
                    browser.close_tab(expected_url=expected, expected_pid=301)
            self.assertEqual(('input.key', {'name': 'cmd+w'}) in guest.calls, accepted)

    def test_existing_unicode_address_matches_utf8_without_decoding_url_syntax(self):
        cases = [
            ('https://fixture.invalid/caf%C3%A9?q=%E6%97%A5#%F0%9F%98%80',
             'fixture.invalid/café?q=日#😀', True),
            ('https://fixture.invalid/café', 'https://fixture.invalid/caf%c3%a9', True),
            ('https://fixture.invalid/caf%C3%A9%2Fother', 'https://fixture.invalid/café/other', False),
            ('https://fixture.invalid/caf%C3%A9%3Fq=1', 'https://fixture.invalid/café?q=1', False),
            ('https://fixture.invalid/caf%C3%A9%23other', 'https://fixture.invalid/café#other', False),
            ('https://fixture.invalid/caf%25C3%25A9', 'https://fixture.invalid/café', False),
            ('https://fixture.invalid/caf%C3%A9', 'http://fixture.invalid/café', False),
            ('https://fixture.invalid/caf%C3%A9', 'https://fixture.invalid/cafe\u0301', False),
            ('https://fixture.invalid/caf%C3%A9?q=1', 'https://fixture.invalid/café?q=2', False),
            ('https://fixture.invalid/caf%C3%A9#owned', 'https://fixture.invalid/café', False),
            ('https://caf%C3%A9.invalid/path', 'https://café.invalid/path', False),
        ]
        for expected, value, accepted in cases:
            with self.subTest(expected=expected, value=value):
                guest = NativeFixture()
                guest.address_value = value
                browser = controlled_browser(guest=guest, input_settle=0)
                if accepted:
                    browser.close_tab(expected_url=expected, expected_pid=301)
                else:
                    with self.assertRaises(GuestError):
                        browser.close_tab(expected_url=expected, expected_pid=301)
                self.assertEqual(guest.calls.count(('input.key', {'name': 'cmd+w'})), int(accepted))

    def test_unicode_normalization_does_not_relax_pasted_url_readback(self):
        guest = NativeFixture()
        guest.address_value = 'https://fixture.invalid/caf%C3%A9'
        guest.drop_text = True
        with self.assertRaisesRegex(GuestError, 'requested URL'):
            controlled_browser(guest=guest, input_settle=0).goto(
                'https://fixture.invalid/café', reuse_tab=True,
                expected_url=guest.address_value, expected_pid=301)
        self.assertIn(('input.paste', {'text': 'https://fixture.invalid/café'}), guest.calls)
        self.assertNotIn(('input.key', {'name': 'return'}), guest.calls)

    def test_tab_close_uncertain_key_result_is_not_repeated(self):
        guest = NativeFixture()
        original = guest.call
        def call_once(method, **params):
            result = original(method, **params)
            if method == 'input.key' and params['name'] == 'cmd+w':
                raise GuestError('reply lost after input')
            return result
        guest.call = call_once
        with self.assertRaises(GuestError):
            controlled_browser(guest=guest, input_settle=0).close_tab(
                expected_url='https://example.org/', expected_pid=301)
        self.assertEqual(guest.calls.count(('input.key', {'name': 'cmd+w'})), 1)

    def test_other_or_unverified_foreground_never_receives_url_typing(self):
        for foreground, verified in [('com.example.notes', True), ('com.apple.mobilesafari', False)]:
            with self.subTest(foreground=foreground, verified=verified):
                guest = NativeFixture(foreground, verified)
                result = controlled_browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
                self.assertEqual(result['method'], 'native_open_url')
                self.assertNotIn('input.paste', [method for method, _ in guest.calls])
                self.assertEqual(guest.calls[-1][1]['bundle_id'], 'com.apple.mobilesafari')

    def test_missing_address_field_never_receives_text(self):
        guest = NativeFixture()
        guest.address_visible = False
        with self.assertRaisesRegex(GuestError, 'address field'):
            controlled_browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
        self.assertNotIn('input.paste', [method for method, _ in guest.calls])

    def test_invalid_safari_pid_stops_before_address_bar_input(self):
        for pid in [None, True, False, 0, 1, -2, '301', 301.0, 2147483648]:
            with self.subTest(pid=pid):
                guest = Mock()
                guest.call.side_effect = [
                    {'locked': False},
                    {'bundle_id': 'com.apple.mobilesafari', 'verified': True, 'pid': pid}]
                with self.assertRaisesRegex(GuestError, 'process identity'):
                    controlled_browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
                self.assertEqual(guest.call.call_args_list, [
                    call('screen.unlock', timeout=60), call('apps.foreground')])
                guest.open.assert_not_called()

    def test_malformed_foreground_reply_does_not_fall_back_to_new_url_open(self):
        for reply in [None, [], {}, {'verified': 'true', 'bundle_id': 'com.apple.mobilesafari'},
                      {'verified': True, 'bundle_id': None}]:
            with self.subTest(reply=reply):
                guest = Mock()
                guest.call.side_effect = [{'locked': False}, reply]
                with self.assertRaisesRegex(GuestError, 'foreground response'):
                    controlled_browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
                self.assertEqual(guest.call.call_count, 2)
                guest.open.assert_not_called()

    def test_missing_reader_blocks_navigation_and_close_before_address_bar_input(self):
        for operation in ('goto', 'close'):
            guest = NativeFixture()
            browser = Browser(guest=guest, input_settle=0)
            with self.assertRaisesRegex(GuestError, 'address_field_reader'):
                if operation == 'goto':
                    browser.goto('https://example.com/', reuse_tab=True)
                else:
                    browser.close_tab(expected_url='https://example.org/', expected_pid=301)
            self.assertFalse(any(name.startswith('input.') or name == 'ui.tree' for name, _ in guest.calls))

    def test_missing_or_foreign_focused_field_never_falls_back_to_daemon_tree(self):
        for value in (None, {'source': 'ax_first_responder', 'pid': 301, 'identifier': 'other'},
                      {'source': 'ax_first_responder', 'pid': 302, 'identifier': 'URL'},
                      GuestError('Native read failed')):
            guest = NativeFixture()
            reader = Mock(side_effect=[value])
            with self.assertRaises(GuestError):
                Browser(guest=guest, input_settle=0, address_field_reader=reader).goto(
                    'https://example.com/', reuse_tab=True)
            self.assertEqual(reader.call_count, 1)
            self.assertNotIn('ui.tree', [name for name, _ in guest.calls])
            self.assertNotIn('input.paste', [name for name, _ in guest.calls])
            self.assertNotIn('input.touch_sequence', [name for name, _ in guest.calls])

    def test_dropped_input_is_not_submitted_as_a_wrong_url(self):
        guest = NativeFixture()
        guest.drop_text = True
        with self.assertRaisesRegex(GuestError, 'requested URL'):
            controlled_browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
        self.assertNotIn(('input.key', {'name': 'return'}), guest.calls)

    def test_visible_but_unfocused_address_field_is_focused_before_typing(self):
        guest = NativeFixture()
        guest.editing = False
        result = controlled_browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
        self.assertEqual(result['method'], 'native_address_bar')
        self.assertEqual([params for method, params in guest.calls if method == 'input.touch_sequence'],
                         [{'normalized': False, 'events': [
                             {'phase': 'down', 'x': 152.5, 'y': 794.0, 'delay_ms': 50},
                             {'phase': 'up', 'x': 152.5, 'y': 794.0, 'delay_ms': 0}]}])
        names = [method for method, _ in guest.calls]
        self.assertLess(names.index('input.touch_sequence'), names.index('input.paste'))
        self.assertIn(('input.key', {'name': 'cmd+a'}), guest.calls)

    def test_unconfirmed_or_failed_focus_never_sends_text_or_repeats_tap(self):
        for failure in [None, 'input.touch_sequence']:
            with self.subTest(failure=failure):
                guest = NativeFixture()
                guest.editing = guest.focus_on_tap = False
                guest.fail = failure
                with self.assertRaises(GuestError):
                    controlled_browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
                names = [method for method, _ in guest.calls]
                self.assertEqual(names.count('input.touch_sequence'), 1)
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
                    controlled_browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
                self.assertNotIn('input.touch_sequence', [method for method, _ in guest.calls])
                self.assertNotIn('input.paste', [method for method, _ in guest.calls])

    def test_invalid_url_has_no_device_side_effect(self):
        guest = NativeFixture()
        with self.assertRaises(ValueError):
            controlled_browser(guest=guest).goto('javascript:alert(1)')
        self.assertEqual(guest.calls, [])

    def test_locked_screen_prevents_navigation(self):
        guest = NativeFixture(locked=True)
        with self.assertRaisesRegex(GuestError, 'locked'):
            controlled_browser(guest=guest).goto('https://example.com/')
        self.assertEqual([method for method, _ in guest.calls], ['screen.unlock'])

    def test_failed_text_entry_does_not_submit(self):
        guest = NativeFixture()
        guest.fail = 'input.paste'
        with self.assertRaises(GuestError):
            controlled_browser(guest=guest, input_settle=0).goto('https://example.com/', reuse_tab=True)
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
