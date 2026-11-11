import unittest
from unittest.mock import Mock, patch

from tungtungtungbrowser import Browser, NativeFocusReader, GuestError
from test_browser import NativeFixture
from test_native_focus import SHA, TOKEN, answer
from test_native_focus_resolution_reader import resolved
from test_native_address_discovery import address


def failed_responder(code=-25215):
    result = resolved()
    result.update(error=code, element=None,
                  scan={'complete': False, 'elements': 0, 'visited': 0,
                        'matches': 0, 'foreign': 0})
    result['resolution'].update(first_error=code, first_present=False,
                                first_seconds=.45, total_seconds=.45)
    return result


class FirstResponderErrorTests(unittest.TestCase):
    def test_restored_first_error_requires_a_new_valid_query(self):
        for address in (False, True):
            with self.subTest(address=address):
                guest = Mock()
                guest._rpc.side_effect = [answer(failed_responder()), answer(resolved(address=address))]
                reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
                with patch('tungtungtungbrowser.native_focus.time.sleep'):
                    field = reader.resolve_address_field(301)
                self.assertEqual(field['source'], 'ax_address_field' if address else 'ax_first_responder')
                self.assertEqual([a['status'] for a in reader.last_resolution_attempts], ['failed', 'read'])
                self.assertTrue(reader.last_resolution_attempts[0]['retry_permitted'])
                self.assertEqual(guest._rpc.call_count, 2)
                for call in guest._rpc.call_args_list:
                    self.assertEqual(call.args, ('script.eval',))
                    self.assertIn('["resolve", "301"]', call.kwargs['source'])

    def test_exhaustion_opt_out_and_other_errors_preserve_native_error(self):
        for code, limit, count in [(-25215, 2, 2), (-25215, 1, 1), (-25204, 2, 1)]:
            with self.subTest(code=code, limit=limit):
                guest = Mock(); guest._rpc.return_value = answer(failed_responder(code))
                reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
                with patch('tungtungtungbrowser.native_focus.time.sleep'):
                    with self.assertRaisesRegex(GuestError, str(code)) as error:
                        reader.resolve_address_field(301, max_attempts=limit)
                self.assertEqual(error.exception.native_helper_response['error'], code)
                self.assertEqual(guest._rpc.call_count, count)

    def test_missing_restoration_malformed_source_and_expired_results_are_terminal(self):
        cases = []
        for key, value in [('requested_pid', 302), ('guard_restored', False),
                           ('element', resolved()['element']), ('error', -25216)]:
            result = failed_responder(); result[key] = value; cases.append(answer(result))
        for key, value in [('first_present', True), ('first_error', -25216),
                           ('first_seconds', .4), ('total_seconds', 10), ('attempts', [{}])]:
            result = failed_responder(); result['resolution'][key] = value; cases.append(answer(result))
        for key, value in [('complete', 0), ('elements', False), ('visited', 1), ('foreign', 1)]:
            result = failed_responder(); result['scan'][key] = value; cases.append(answer(result))
        result = failed_responder(); result['after'] = {}; cases.append(answer(result))
        cases.extend([GuestError('Missing transport reply'), {'ok': False}])
        for response in cases:
            with self.subTest(response=response):
                guest = Mock()
                if isinstance(response, Exception): guest._rpc.side_effect = response
                else: guest._rpc.return_value = response
                reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
                with self.assertRaises(GuestError): reader.resolve_address_field(301)
                guest._rpc.assert_called_once()
                self.assertFalse(reader.last_resolution['retry_permitted'])

    def test_second_invalid_query_cannot_use_discarded_evidence(self):
        second = resolved(address=True)
        second['element']['pid'] = 302
        guest = Mock(); guest._rpc.side_effect = [answer(failed_responder()), answer(second)]
        reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
        with patch('tungtungtungbrowser.native_focus.time.sleep'):
            with self.assertRaises(GuestError): reader.resolve_address_field(301)
        self.assertEqual(guest._rpc.call_count, 2)
        self.assertEqual([a['status'] for a in reader.last_resolution_attempts], ['failed', 'failed'])

    def test_browser_sends_one_shortcut_and_no_input_from_failed_evidence(self):
        foreground = {'pid': 301, 'bundle_id': 'com.apple.mobilesafari', 'verified': True}
        for success in (False, True):
            ui = NativeFixture(); guest = Mock()
            guest._rpc.side_effect = [answer(address(absent=True)), answer(failed_responder()),
                                     answer(resolved() if success else failed_responder())]
            reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
            reader.supports_address_resolution = True
            browser = Browser(guest=ui, address_field_reader=reader, input_settle=0)
            with patch('tungtungtungbrowser.native_focus.time.sleep'):
                if success:
                    self.assertEqual(browser._focus_address_field(foreground)['pid'], 301)
                else:
                    with self.assertRaises(GuestError): browser._focus_address_field(foreground)
            self.assertEqual(guest._rpc.call_count, 3)
            self.assertEqual([p['name'] for m, p in ui.calls if m == 'input.key'], ['cmd+l'])
            self.assertFalse(any(m in ('input.paste', 'input.touch_sequence') for m, _ in ui.calls))
