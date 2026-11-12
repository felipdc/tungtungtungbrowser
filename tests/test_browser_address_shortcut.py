import unittest
from unittest.mock import Mock

from tungtungtungbrowser import Browser, GuestError
from test_browser import NativeFixture


class AddressShortcutTests(unittest.TestCase):
    def browser(self, guest, reader=None):
        reader = reader if reader is not None else Mock(side_effect=guest.focused_field)
        reader.supports_address_resolution = True
        reader.discover_address_field = Mock(side_effect=AssertionError('Unexpected discovery'))
        return Browser(guest=guest, input_settle=0, address_field_reader=reader,
                       address_focus='shortcut')

    def test_shortcut_navigation_and_close_verify_focus_without_a_touch(self):
        guest = NativeFixture()
        browser = self.browser(guest)
        browser.goto('https://next.invalid/', reuse_tab=True, expected_pid=301,
                     expected_url=guest.address_value)
        browser.close_tab(expected_url='https://next.invalid/', expected_pid=301)
        self.assertEqual([p['name'] for m, p in guest.calls if m == 'input.key'],
                         ['cmd+l', 'cmd+a', 'return', 'cmd+l', 'cmd+w'])
        self.assertFalse(any(m in ('input.touch_sequence', 'screen.screenshot') for m, _ in guest.calls))
        self.assertEqual(browser.address_field_reader.call_count, 3)

    def test_absent_nonediting_foreign_and_failed_reads_do_not_send_text_or_close(self):
        valid = NativeFixture().focused_field(301)
        for result in [None, {**valid, 'traits': 0}, {**valid, 'pid': 302},
                       {**valid, 'identifier': 'Other'}, GuestError('Read failed')]:
            for action in ['goto', 'close']:
                with self.subTest(result=result, action=action):
                    guest = NativeFixture()
                    reader = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
                    browser = self.browser(guest, reader)
                    with self.assertRaises(GuestError):
                        if action == 'goto':
                            browser.goto('https://next.invalid/', reuse_tab=True)
                        else:
                            browser.close_tab(expected_url=guest.address_value, expected_pid=301)
                    self.assertEqual([(m, p) for m, p in guest.calls if m.startswith('input.')],
                                     [('input.key', {'name': 'cmd+l'})])
                    reader.assert_called_once_with(301)

    def test_uncertain_shortcut_stops_before_readback_without_fallback(self):
        for ack in [None, {}, {'key': 'cmd+l', 'dispatched': False},
                    {'key': 'cmd+w', 'dispatched': True}, GuestError('Lost reply')]:
            guest = Mock()
            guest.call.side_effect = [{'locked': False},
                {'verified': True, 'bundle_id': 'com.apple.mobilesafari', 'pid': 301}, ack]
            reader = Mock()
            with self.assertRaises(GuestError):
                self.browser(guest, reader).goto('https://next.invalid/', reuse_tab=True)
            reader.assert_not_called()
            self.assertEqual(guest.call.call_count, 3)
            guest.tap.assert_not_called()

    def test_missing_reader_and_invalid_mode_send_no_focus_input(self):
        guest = NativeFixture()
        with self.assertRaisesRegex(GuestError, 'address_field_reader'):
            Browser(guest=guest, address_focus='shortcut').goto('https://next.invalid/', reuse_tab=True)
        self.assertFalse(any(m.startswith('input.') for m, _ in guest.calls))
        for value in [None, True, 0, '', 'touch']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                Browser(guest=NativeFixture(), address_focus=value)


if __name__ == '__main__':
    unittest.main()
