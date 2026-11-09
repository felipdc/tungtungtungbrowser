import unittest
from unittest.mock import Mock, patch

from tungtungtungbrowser import Browser, GuestError, NativeFocusReader
from test_browser import NativeFixture
from test_native_focus import SHA, TOKEN, answer
from test_native_focus_resolution_reader import resolved


def unavailable_attributes():
    result = resolved()
    result['scan'] = {'complete': False, 'elements': 0, 'visited': 0, 'matches': 0, 'foreign': 0}
    result['resolution']['total_seconds'] = result['resolution']['first_seconds']
    for name in ('label', 'value', 'identifier', 'traits'):
        result['element']['attributes'][name] = {'error': -25216, 'value': None}
    return result


class FocusAttributeTests(unittest.TestCase):
    def reader(self, replies):
        guest = Mock()
        guest._rpc.side_effect = replies
        return guest, NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)

    def test_completed_attribute_unavailability_discards_frame_and_reads_again(self):
        failed = unavailable_attributes()
        failed['element']['attributes']['frame']['value']['x'] = 999
        guest, reader = self.reader([answer(failed), answer(resolved())])
        with patch('tungtungtungbrowser.native_focus.time.sleep'):
            field = reader.resolve_address_field(301)
        self.assertNotEqual(field['frame']['x'], 999)
        self.assertEqual([r['status'] for r in reader.last_resolution_attempts], ['failed', 'read'])
        self.assertTrue(reader.last_resolution_attempts[0]['retry_permitted'])
        self.assertEqual(guest._rpc.call_count, 2)
        for call in guest._rpc.call_args_list:
            self.assertEqual(call.args, ('script.eval',))
            self.assertIn('["resolve", "301"]', call.kwargs['source'])
        guest.call.assert_not_called()

    def test_exhaustion_and_single_query_remain_terminal(self):
        for limit in (1, 2):
            with self.subTest(limit=limit):
                guest, reader = self.reader([answer(unavailable_attributes()) for _ in range(limit)])
                with patch('tungtungtungbrowser.native_focus.time.sleep'), self.assertRaisesRegex(GuestError, 'attribute read failed'):
                    reader.resolve_address_field(301, max_attempts=limit)
                self.assertEqual(guest._rpc.call_count, limit)

    def test_browser_never_uses_the_failed_frame_or_replays_the_shortcut(self):
        foreground = {'pid': 301, 'bundle_id': 'com.apple.mobilesafari', 'verified': True}
        for success in (False, True):
            ui = NativeFixture()
            guest, reader = self.reader([answer(unavailable_attributes()),
                                         answer(resolved() if success else unavailable_attributes())])
            reader.supports_address_resolution = True
            browser = Browser(guest=ui, address_field_reader=reader, input_settle=0)
            with patch('tungtungtungbrowser.native_focus.time.sleep'):
                if success:
                    self.assertEqual(browser._focus_address_field(foreground)['pid'], 301)
                else:
                    with self.assertRaises(GuestError):
                        browser._focus_address_field(foreground)
            self.assertEqual(guest._rpc.call_count, 2)
            self.assertEqual([p['name'] for m, p in ui.calls if m == 'input.key'], ['cmd+l'])
            self.assertFalse(any(m in ('input.paste', 'input.touch_sequence') for m, _ in ui.calls))

    def test_foreign_unrestored_malformed_or_other_errors_never_allow_another_query(self):
        cases = []
        def change(path, value):
            result = unavailable_attributes()
            target = result
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            cases.append(result)
        for path, value in [
                (['requested_pid'], 302), (['element', 'pid'], 302),
                (['element', 'pid_error'], -25216), (['element', 'timeout_error'], -25216),
                (['guard_restored'], False), (['after', 'automation'], 0),
                (['resolution', 'total_seconds'], 10), (['resolution', 'first_present'], False),
                (['scan', 'elements'], 1), (['scan', 'visited'], False),
                (['element', 'attributes', 'label', 'error'], -25215),
                (['element', 'attributes', 'identifier', 'value'], 'URL'),
                (['element', 'attributes', 'traits', 'error'], True),
                (['element', 'attributes', 'label'], None),
                (['element', 'attributes', 'frame', 'error'], -25216),
                (['element', 'attributes', 'frame', 'value', 'width'], 0),
                (['element', 'attributes', 'frame', 'value', 'x'], float('nan')),
                (['element', 'attributes', 'frame', 'value', 'y'], True)]:
            change(path, value)
        for result in cases:
            with self.subTest(result=result):
                guest, reader = self.reader([answer(result), answer(resolved())])
                with self.assertRaises(GuestError):
                    reader.resolve_address_field(301)
                guest._rpc.assert_called_once()
                self.assertFalse(reader.last_resolution['retry_permitted'])

    def test_transport_process_and_nonresolve_reads_remain_terminal(self):
        failed = answer(unavailable_attributes())
        for reply in [GuestError('missing reply'), {**failed, 'ok': False}]:
            guest, reader = self.reader([reply, answer(resolved())])
            with self.assertRaises(GuestError):
                reader.resolve_address_field(301)
            guest._rpc.assert_called_once()
        guest, reader = self.reader([answer(unavailable_attributes()), answer(resolved())])
        with self.assertRaises(GuestError):
            reader(301)
        guest._rpc.assert_called_once()
