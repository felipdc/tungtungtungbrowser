import copy
import unittest
from unittest.mock import Mock, patch
from tungtungtungbrowser import NativeFocusReader, GuestError
from test_native_focus import SHA, TOKEN, answer, native_result


def address(*, absent=False, unavailable=False):
    return {**native_result(), 'lookup': 'address', 'disabled_trait': 256,
            'error': -25216 if unavailable else 0,
            'element': None if absent or unavailable else native_result()['element'],
            'scan': {'complete': not unavailable, 'elements': 0 if unavailable else 4,
                     'visited': 0 if unavailable else 4,
                     'matches': 0 if absent or unavailable else 1, 'foreign': 0}}


class DirectAddressDiscoveryTests(unittest.TestCase):
    def reader(self, replies):
        guest = Mock(); guest._rpc.side_effect = replies
        return guest, NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)

    @patch('tungtungtungbrowser.native_focus.time.sleep')
    def test_unavailable_can_get_one_fresh_address_query(self, sleep):
        partial = address(unavailable=True); partial['scan'].update(elements=20, visited=3)
        for first in [address(unavailable=True), partial]:
            with self.subTest(first=first):
                guest, reader = self.reader([answer(first), answer(address())])
                field = reader.discover_address_field(301)
                self.assertEqual(field['source'], 'ax_address_field')
                self.assertTrue(field['enabled'])
                self.assertEqual(guest._rpc.call_count, 2)
                self.assertTrue(reader.last_address_attempts[0]['retry_permitted'])
                for call in guest._rpc.call_args_list:
                    self.assertIn('["address", "301"]', call.kwargs['source'])
                    self.assertEqual(call.kwargs['timeout'], 30)
                guest.call.assert_not_called()

    @patch('tungtungtungbrowser.native_focus.time.sleep')
    def test_two_query_limit_preserves_failure_or_absence(self, sleep):
        for unavailable in (False, True):
            guest, reader = self.reader([answer(address(absent=True, unavailable=unavailable))] * 3)
            if unavailable:
                with self.assertRaises(GuestError): reader.discover_address_field(301)
            else: self.assertIsNone(reader.discover_address_field(301))
            self.assertEqual(guest._rpc.call_count, 2 if unavailable else 1)

    def test_complete_absence_stops_before_optional_partial_failure(self):
        absent = address(absent=True)
        absent['scan'].update(elements=6, visited=6)
        partial = address(unavailable=True)
        partial['scan'].update(elements=6, visited=1)
        guest, reader = self.reader([answer(absent), answer(partial)])
        with patch('tungtungtungbrowser.native_focus.time.sleep') as sleep:
            self.assertIsNone(reader.discover_address_field(301))
        self.assertEqual(guest._rpc.call_count, 1)

        self.assertEqual(reader.last_address_lookup['status'], 'absent')
        self.assertFalse(reader.last_address_lookup['retry_permitted'])
        sleep.assert_not_called()
        guest.call.assert_not_called()

    def test_complete_absence_uses_fresh_resolution_after_one_shortcut(self):
        from tungtungtungbrowser import Browser
        from test_browser import NativeFixture
        from test_native_focus_resolution_reader import resolved
        ui = NativeFixture()
        guest, reader = self.reader([answer(address(absent=True)), answer(resolved())])
        reader.supports_address_resolution = True
        browser = Browser(guest=ui, address_field_reader=reader, input_settle=0)
        field = browser._focus_address_field(
            {'pid': 301, 'bundle_id': 'com.apple.mobilesafari', 'verified': True})
        self.assertEqual(field['source'], 'ax_first_responder')
        self.assertEqual([p['name'] for m, p in ui.calls if m == 'input.key'], ['cmd+l'])
        self.assertFalse(any(m in ('input.paste', 'input.touch_sequence') for m, _ in ui.calls))
        self.assertEqual(guest._rpc.call_count, 2)
        self.assertIn('["address", "301"]', guest._rpc.call_args_list[0].kwargs['source'])
        self.assertIn('["resolve", "301"]', guest._rpc.call_args_list[1].kwargs['source'])

    def test_unrestored_malformed_foreign_ambiguous_and_expired_reads_are_terminal(self):
        bad = []
        for key, value in [('requested_pid', 302), ('guard_restored', False), ('error', -6),
                           ('lookup', 'resolve'), ('disabled_trait', True)]:
            result = address(unavailable=True); result[key] = value; bad.append(result)
        for key, value in [('elements', 513), ('visited', True), ('matches', 1), ('foreign', 1), ('complete', True)]:
            result = address(unavailable=True); result['scan'][key] = value; bad.append(result)
        result = address(unavailable=True); result['scan'].update(elements=4, visited=4); bad.append(result)
        result = address(unavailable=True); result['after']['application'] = 1; bad.append(result)
        result = address(); result['element']['pid'] = 302; bad.append(result)
        result = address(); result['element']['attributes']['identifier'].update(error=-25205, value=None); bad.append(result)
        for first in bad:
            with self.subTest(first=first):
                guest, reader = self.reader([answer(first), answer(address())])
                with self.assertRaises(GuestError): reader.discover_address_field(301)
                self.assertEqual(guest._rpc.call_count, 1)
                self.assertFalse(reader.last_address_lookup['retry_permitted'])

    def test_transport_or_helper_failure_is_not_repeated(self):
        for response in [GuestError('Missing reply'), {'ok': False}]:
            guest, reader = self.reader([response, answer(address())])
            with self.assertRaises(GuestError): reader.discover_address_field(301)
            self.assertEqual(guest._rpc.call_count, 1)

    def test_invalid_limits_or_pid_send_no_native_query(self):
        guest, reader = self.reader([])
        for limit in [0, 3, True, 1.0, None]:
            with self.assertRaises(ValueError): reader.discover_address_field(301, max_attempts=limit)
        for pid in [1, True, 301.0, None]:
            with self.assertRaises(ValueError): reader.discover_address_field(pid)
        guest._rpc.assert_not_called()

    def test_success_and_explicit_single_attempt_preserve_single_read_behavior(self):
        guest, reader = self.reader([answer(address())])
        self.assertEqual(reader.discover_address_field(301)['identifier'], 'URL')
        self.assertEqual(guest._rpc.call_count, 1)
        guest, reader = self.reader([answer(address(unavailable=True)), answer(address())])
        with self.assertRaises(GuestError): reader.discover_address_field(301, max_attempts=1)
        self.assertEqual(guest._rpc.call_count, 1)


class AddressDiscoveryWaitTests(unittest.TestCase):
    reader = DirectAddressDiscoveryTests.reader

    def test_completed_unavailability_can_settle_without_any_ui_input(self):
        partial = address(unavailable=True); partial['scan'].update(elements=21, visited=19)
        guest, reader = self.reader([answer(address(unavailable=True)), answer(partial), answer(address())])
        clock = [100.0]
        with patch('tungtungtungbrowser.native_focus.time.monotonic', side_effect=lambda: clock[0]), \
             patch('tungtungtungbrowser.native_focus.time.sleep', side_effect=lambda n: clock.__setitem__(0, clock[0] + n)):
            field = reader.wait_for_address_field(301, timeout=30)
        self.assertEqual(field['source'], 'ax_address_field')
        self.assertEqual([r['status'] for r in reader.last_address_attempts], ['failed', 'failed', 'read'])
        self.assertEqual(reader.last_address_wait, {'timeout_seconds': 30, 'status': 'read', 'seconds': 4})
        self.assertEqual(guest._rpc.call_count, 3)
        guest.call.assert_not_called()

    def test_deadline_keeps_failures_and_prevents_next_query(self):
        guest, reader = self.reader([answer(address(unavailable=True))] * 4)
        clock = [100.0]
        with patch('tungtungtungbrowser.native_focus.time.monotonic', side_effect=lambda: clock[0]), \
             patch('tungtungtungbrowser.native_focus.time.sleep', side_effect=lambda n: clock.__setitem__(0, clock[0] + n)):
            with self.assertRaisesRegex(GuestError, 'deadline exceeded'):
                reader.wait_for_address_field(301, timeout=3)
        self.assertEqual(guest._rpc.call_count, 2)
        self.assertEqual(len(reader.last_address_attempts), 2)
        self.assertEqual(reader.last_address_wait['status'], 'failed')

    def test_late_complete_field_is_rejected(self):
        clock = [100.0]
        guest, reader = self.reader([])
        def read(*args, **kwargs):
            clock[0] += 31
            return answer(address())
        guest._rpc.side_effect = read
        with patch('tungtungtungbrowser.native_focus.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(GuestError, 'deadline exceeded'):
                reader.wait_for_address_field(301)
        guest._rpc.assert_called_once()
        self.assertEqual(reader.last_address_attempts[0]['status'], 'read')
        self.assertEqual(reader.last_address_wait['status'], 'failed')

    def test_uncertain_or_unrestored_query_is_never_repeated(self):
        bad = address(unavailable=True); bad['guard_restored'] = False
        partial = address(unavailable=True); partial['scan'].update(elements=21, visited=19, matches=1)
        for reply in [GuestError('transport timed out'), {'ok': False}, answer(bad), answer(partial)]:
            with self.subTest(reply=reply):
                guest, reader = self.reader([reply, answer(address())])
                with self.assertRaises(GuestError): reader.wait_for_address_field(301)
                guest._rpc.assert_called_once()
                guest.call.assert_not_called()

    def test_complete_absence_still_returns_immediately(self):
        guest, reader = self.reader([answer(address(absent=True)), answer(address())])
        with patch('tungtungtungbrowser.native_focus.time.sleep') as sleep:
            self.assertIsNone(reader.wait_for_address_field(301))
        guest._rpc.assert_called_once(); sleep.assert_not_called()
        self.assertEqual(reader.last_address_wait['status'], 'absent')

    def test_invalid_deadline_or_pid_sends_no_query(self):
        guest, reader = self.reader([])
        for value in [0, -1, True, 121, None, float('inf'), float('nan')]:
            with self.assertRaises(ValueError): reader.wait_for_address_field(301, timeout=value)
        for pid in [1, True, 301.0, None]:
            with self.assertRaises(ValueError): reader.wait_for_address_field(pid)
        guest._rpc.assert_not_called()
