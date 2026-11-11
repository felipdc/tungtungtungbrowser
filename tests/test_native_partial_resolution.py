import unittest
from unittest.mock import Mock, patch

from tungtungtungbrowser import Browser, GuestError, NativeFocusReader
from test_browser import NativeFixture
from test_native_focus import SHA, TOKEN, answer
from test_native_focus_resolution_reader import resolved
from test_native_address_discovery import address


def partial(visited=1):
    result = resolved(address=True)
    scan = {'complete': False, 'elements': 28, 'visited': visited,
            'matches': 0, 'foreign': 0}
    result.update(error=-25216, element=None, scan=scan)
    result['resolution']['attempts'] = [{'error': -25216, 'seconds': .5, **scan}]
    return result


class PartialResolutionTests(unittest.TestCase):
    def reader(self, responses):
        guest = Mock()
        guest._rpc.side_effect = [answer(response) for response in responses]
        return NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN), guest

    def test_partial_evidence_is_discarded_before_fresh_owned_result(self):
        for visited in (0, 1, 27):
            for address in (False, True):
                with self.subTest(visited=visited, address=address):
                    reader, guest = self.reader([partial(visited), resolved(address=address)])
                    with patch('tungtungtungbrowser.native_focus.time.sleep'):
                        field = reader.resolve_address_field(301)
                    self.assertEqual(field['pid'], 301)
                    self.assertEqual(guest._rpc.call_count, 2)
                    self.assertEqual([r['status'] for r in reader.last_resolution_attempts], ['failed', 'read'])
                    self.assertTrue(reader.last_resolution_attempts[0]['retry_permitted'])
                    for call in guest._rpc.call_args_list:
                        self.assertEqual(call.args, ('script.eval',))
                        self.assertIn('["resolve", "301"]', call.kwargs['source'])

    def test_exhaustion_and_opt_out_never_accept_partial_results(self):
        for attempts in (1, 2):
            reader, guest = self.reader([partial()] * attempts)
            with patch('tungtungtungbrowser.native_focus.time.sleep'), self.assertRaises(GuestError):
                reader.resolve_address_field(301, max_attempts=attempts)
            self.assertEqual(guest._rpc.call_count, attempts)
            self.assertTrue(all(r['status'] == 'failed' for r in reader.last_resolution_attempts))

    def test_ambiguity_foreign_nodes_bad_bounds_or_unverified_result_are_terminal(self):
        cases = []
        for key, value in [('matches', 1), ('foreign', 1), ('visited', 28),
                           ('elements', 513), ('visited', True), ('complete', True)]:
            result = partial()
            result['scan'][key] = value
            result['resolution']['attempts'][0][key] = value
            cases.append(result)
        for key, value in [('requested_pid', 302), ('guard_restored', False),
                           ('disabled_trait', True), ('disabled_trait', 0),
                           ('disabled_trait', 3), ('disabled_trait', 2**64),
                           ('element', resolved()['element'])]:
            result = partial(); result[key] = value; cases.append(result)
        result = partial(); result['resolution']['total_seconds'] = 10; cases.append(result)
        result = partial(); result['after'] = {'application': 1, 'automation': 1}; cases.append(result)
        for result in cases:
            with self.subTest(result=result):
                reader, guest = self.reader([result])
                with self.assertRaises(GuestError): reader.resolve_address_field(301)
                guest._rpc.assert_called_once()
                self.assertFalse(reader.last_resolution['retry_permitted'])

    def test_browser_sends_one_shortcut_and_no_input_from_partial_evidence(self):
        foreground = {'pid': 301, 'bundle_id': 'com.apple.mobilesafari', 'verified': True}
        for success in (False, True):
            ui = NativeFixture()
            reader, guest = self.reader([address(absent=True), partial(), resolved() if success else partial()])
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
