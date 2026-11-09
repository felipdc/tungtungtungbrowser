import copy
import unittest
from unittest.mock import Mock, patch

from tungtungtungbrowser import Browser, GuestError, NativeFocusReader
from test_browser import NativeFixture
from test_native_focus import SHA, TOKEN, answer
from test_native_focus_resolution_reader import resolved


def missing_names(error=-25212):
    result = resolved()
    result['scan'] = {'complete': False, 'elements': 0, 'visited': 0, 'matches': 0, 'foreign': 0}
    result['resolution']['total_seconds'] = result['resolution']['first_seconds']
    for key in ('label', 'identifier'):
        result['element']['attributes'][key] = {'error': error, 'value': None}
    return result


class MissingFocusNamesTests(unittest.TestCase):
    name_error = -25212

    def missing_names(self):
        return missing_names(self.name_error)

    def reader(self, results):
        guest = Mock()
        guest._rpc.side_effect = [answer(r) for r in results]
        reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
        reader.supports_address_resolution = True
        return guest, reader

    def test_one_fresh_query_discards_every_partial_attribute(self):
        partial = self.missing_names()
        partial['element']['attributes']['value']['value'] = 'stale.invalid'
        partial['element']['attributes']['frame']['value']['x'] = 999
        guest, reader = self.reader([partial, resolved()])
        with patch('tungtungtungbrowser.native_focus.time.sleep'):
            field = reader.resolve_address_field(301)
        self.assertNotEqual(field['value'], 'stale.invalid')
        self.assertNotEqual(field['frame']['x'], 999)
        self.assertEqual([r['status'] for r in reader.last_resolution_attempts], ['failed', 'read'])
        self.assertEqual(guest._rpc.call_count, 2)
        guest.call.assert_not_called()

    def test_exhaustion_or_disabled_recovery_never_returns_partial_field(self):
        for count in (1, 2):
            guest, reader = self.reader([self.missing_names()] * count)
            with patch('tungtungtungbrowser.native_focus.time.sleep'), self.assertRaises(GuestError):
                reader.resolve_address_field(301, max_attempts=count)
            self.assertEqual(guest._rpc.call_count, count)

    def test_browser_never_replays_shortcut_or_uses_partial_value(self):
        for success in (False, True):
            ui = NativeFixture()
            guest, reader = self.reader([self.missing_names(), resolved() if success else self.missing_names()])
            browser = Browser(guest=ui, address_field_reader=reader, input_settle=0)
            foreground = {'pid': 301, 'bundle_id': 'com.apple.mobilesafari', 'verified': True}
            with patch('tungtungtungbrowser.native_focus.time.sleep'):
                if success:
                    self.assertEqual(browser._focus_address_field(foreground)['pid'], 301)
                else:
                    with self.assertRaises(GuestError): browser._focus_address_field(foreground)
            self.assertEqual([p['name'] for m, p in ui.calls if m == 'input.key'], ['cmd+l'])
            self.assertFalse(any(m in ('input.paste', 'input.touch_sequence') for m, _ in ui.calls))
            self.assertEqual(guest._rpc.call_count, 2)

    def test_other_errors_bad_ownership_restoration_and_malformed_values_are_terminal(self):
        changes = [
            (('requested_pid',), 302), (('element', 'pid'), 302),
            (('element', 'pid_error'), -25216), (('guard_restored',), False),
            (('after', 'automation'), 0), (('resolution', 'total_seconds'), 10),
            (('resolution', 'first_present'), False), (('scan', 'elements'), 1),
            (('element', 'attributes', 'label', 'error'), -25216),
            (('element', 'attributes', 'label', 'error'), -25205 if self.name_error == -25212 else -25212),
            (('element', 'attributes', 'label', 'error'), True),
            (('element', 'attributes', 'identifier', 'value'), 'URL'),
            (('element', 'attributes', 'value', 'error'), -25216),
            (('element', 'attributes', 'value', 'value'), ''),
            (('element', 'attributes', 'value', 'value'), 2),
            (('element', 'attributes', 'traits', 'value'), True),
            (('element', 'attributes', 'traits', 'value'), -1),
            (('element', 'attributes', 'traits', 'value'), 2**64),
            (('element', 'attributes', 'frame', 'value', 'width'), 0),
            (('element', 'attributes', 'frame', 'value', 'x'), float('nan')),
        ]
        for path, value in changes:
            result = copy.deepcopy(self.missing_names()); target = result
            for key in path[:-1]: target = target[key]
            target[path[-1]] = value
            guest, reader = self.reader([result, resolved()])
            with self.subTest(path=path, value=value), self.assertRaises(GuestError):
                reader.resolve_address_field(301)
            self.assertEqual(guest._rpc.call_count, 1)
            self.assertFalse(reader.last_resolution['retry_permitted'])

    def test_nonresolve_call_does_not_gain_another_read(self):
        guest, reader = self.reader([self.missing_names(), resolved()])
        with self.assertRaises(GuestError): reader(301)
        self.assertEqual(guest._rpc.call_count, 1)


class UnsupportedFocusNamesTests(MissingFocusNamesTests):
    name_error = -25205
