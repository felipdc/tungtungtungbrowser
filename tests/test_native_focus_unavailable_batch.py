import copy
import unittest
from unittest.mock import patch

from tungtungtungbrowser import Browser, GuestError
from test_browser import NativeFixture
from test_native_focus import answer
import test_native_focus_attributes as attribute_helpers
from test_native_focus_resolution_reader import resolved


def failed_batch():
    result = attribute_helpers.unavailable_attributes()
    result['element']['attributes']['frame'] = {'error': -25216, 'value': None}
    result['element']['attribute_batch'] = {'error': -25216, 'count': 5, 'seconds': .259158}
    return result


class UnavailableBatchTests(unittest.TestCase):
    reader = attribute_helpers.FocusAttributeTests.reader

    def test_fresh_complete_read_is_required_in_both_modes(self):
        for mode in ('__call__', 'resolve_address_field'):
            guest, reader = self.reader([answer(failed_batch()), answer(resolved())])
            with self.subTest(mode=mode), patch('tungtungtungbrowser.native_focus.time.sleep'):
                field = getattr(reader, mode)(301)
            self.assertEqual(field['identifier'], 'URL')
            self.assertEqual(guest._rpc.call_count, 2)
            guest.call.assert_not_called()

    def test_explicit_three_read_profile_handles_absence_then_missing_names(self):
        from test_native_resolution_recovery import absent
        from test_native_focus_missing_names import missing_names
        from tungtungtungbrowser import NativeFocusReader
        from test_native_focus import SHA, TOKEN
        from unittest.mock import Mock
        for succeeds in (True, False):
            guest = Mock()
            guest._rpc.side_effect = [answer(absent()), answer(missing_names(-25205)),
                                     answer(resolved() if succeeds else missing_names(-25205))]
            reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN, max_read_attempts=3)
            with patch('tungtungtungbrowser.native_focus.time.sleep'):
                if succeeds:
                    self.assertEqual(reader.resolve_address_field(301)['identifier'], 'URL')
                else:
                    with self.assertRaises(GuestError): reader.resolve_address_field(301)
            self.assertEqual(guest._rpc.call_count, 3)
            guest.call.assert_not_called()

    def test_exhaustion_keeps_the_existing_two_read_bound(self):
        for mode in ('__call__', 'resolve_address_field'):
            guest, reader = self.reader([answer(failed_batch())] * 3)
            with self.subTest(mode=mode), patch('tungtungtungbrowser.native_focus.time.sleep'), self.assertRaises(GuestError):
                getattr(reader, mode)(301)
            self.assertEqual(guest._rpc.call_count, 2)

    def test_incomplete_or_untrusted_batch_never_authorizes_another_read(self):
        changes = [
            (('requested_pid',), 302), (('element', 'pid'), 302),
            (('element', 'pid_error'), -25216), (('element', 'timeout_error'), -25216),
            (('guard_restored',), False), (('after', 'automation'), 0),
            (('element', 'attribute_batch', 'count'), True),
            (('element', 'attribute_batch', 'count'), 4),
            (('element', 'attribute_batch', 'error'), -25215),
            (('element', 'attribute_batch', 'seconds'), float('nan')),
            (('element', 'attribute_batch', 'seconds'), 20),
            (('element', 'attribute_batch', 'seconds'), True),
            (('element', 'attributes', 'label', 'error'), -25215),
            (('element', 'attributes', 'identifier', 'value'), 'URL'),
            (('element', 'attributes', 'frame', 'error'), 0),
            (('element', 'attribute_batch'), None),
        ]
        for path, value in changes:
            result = copy.deepcopy(failed_batch()); target = result
            for part in path[:-1]: target = target[part]
            target[path[-1]] = value
            for mode in ('__call__', 'resolve_address_field'):
                guest, reader = self.reader([answer(result), answer(resolved())])
                with self.subTest(path=path, mode=mode), self.assertRaises(GuestError):
                    getattr(reader, mode)(301)
                guest._rpc.assert_called_once()

    def test_shortcut_is_sent_once_and_failed_attributes_never_supply_input(self):
        foreground = {'pid': 301, 'bundle_id': 'com.apple.mobilesafari', 'verified': True}
        for succeeds in (False, True):
            ui = NativeFixture()
            guest, reader = self.reader([answer(failed_batch()), answer(resolved() if succeeds else failed_batch())])
            browser = Browser(guest=ui, address_field_reader=reader, address_focus='shortcut', input_settle=0)
            with patch('tungtungtungbrowser.native_focus.time.sleep'):
                if succeeds:
                    self.assertEqual(browser._focus_address_field(foreground)['identifier'], 'URL')
                else:
                    with self.assertRaises(GuestError): browser._focus_address_field(foreground)
            self.assertEqual([p['name'] for m, p in ui.calls if m == 'input.key'], ['cmd+l'])
            self.assertFalse(any(m in ('input.paste', 'input.touch_sequence') for m, _ in ui.calls))
            self.assertEqual(guest._rpc.call_count, 2)
