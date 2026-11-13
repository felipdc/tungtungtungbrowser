import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call

from tungtungtungbrowser import Browser
from test_browser import NativeFixture
from tungtungtungbrowser import GuestError
from tungtungtungbrowser.guest import GuestTransportError

FG = {'pid': 301, 'bundle_id': 'com.apple.mobilesafari', 'verified': True}


class Reader:
    supports_address_resolution = True
    last_resolution = None
    last_read_attempts = []
    def __init__(self, guest, field):
        self.guest, self.field, self.resolved, self.fresh = guest, field, 0, 0
    def resolve_address_field(self, pid):
        if self.field is not None:
            raise AssertionError("Present discovered control must not require a shortcut")
        return self.guest.focused_field(pid)
    def discover_address_field(self, pid):
        self.resolved += 1
        self.last_resolution = {'status': 'read'}
        if isinstance(self.field, Exception): raise self.field
        return self.field
    def __call__(self, pid):
        self.fresh += 1
        return self.guest.focused_field(pid)


class NativeAddressResolutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.guest = NativeFixture()
        self.field = self.guest.focused_field(301)
        self.guest.calls.clear()
    def browser(self, field):
        self.reader = Reader(self.guest, field)
        return Browser(guest=self.guest, address_field_reader=self.reader, input_settle=0)
    def inputs(self):
        return [p['name'] for m,p in self.guest.calls if m == 'input.key']
    def taps(self):
        return [p for m,p in self.guest.calls if m == 'input.touch_sequence']
    def discovered(self):
        return {**self.field, 'source': 'ax_address_field', 'enabled': True}
    def test_editing_first_responder_needs_no_tap_or_second_preparation_read(self):
        b = self.browser(self.field)
        self.assertEqual(b._focus_address_field(FG), self.field)
        self.assertEqual(self.inputs(), [])
        self.assertEqual(self.taps(), [])
        self.assertEqual((self.reader.resolved, self.reader.fresh), (1,0))
    def test_discovered_or_nonediting_field_gets_one_tap_and_fresh_read(self):
        for field in [self.discovered(), {**self.field, 'traits': 0}]:
            self.guest.calls.clear(); b = self.browser(field)
            self.assertEqual(b._focus_address_field(FG)['source'], 'ax_first_responder')
            self.assertEqual(self.inputs(), [])
            self.assertEqual(len(self.taps()), 1)
            self.assertEqual((self.reader.resolved, self.reader.fresh), (1,1))
    def test_configured_zero_reaches_address_focus_contact(self):
        from tungtungtungbrowser.guest import Guest
        self.guest.tap = lambda x, y, **options: Guest.tap(self.guest, x, y, **options)
        reader = Reader(self.guest, self.discovered())
        browser = Browser(guest=self.guest, address_field_reader=reader,
                          input_settle=0, touch_hold_ms=0)
        self.assertEqual(browser._focus_address_field(FG)['source'], 'ax_first_responder')
        self.assertEqual(len(self.taps()), 1)
        self.assertEqual(self.taps()[0]['events'][0]['delay_ms'], 0)
        self.assertEqual((reader.resolved, reader.fresh), (1, 1))

    def test_focus_bounds_do_not_require_a_screenshot(self):
        self.guest.fail = 'screen.screenshot'
        self.browser(self.discovered())._focus_address_field(FG)
        methods = [method for method, _ in self.guest.calls]
        self.assertEqual(methods.count('device.screen'), 1)
        self.assertNotIn('screen.screenshot', methods)
        self.assertEqual(len(self.taps()), 1)

    def test_oriented_screen_points_bound_the_focus_tap(self):
        for orientation, width, height in [(0, 393, 852), (90, 852, 393),
                                            (180, 393, 852), (270, 852, 393)]:
            with self.subTest(orientation=orientation):
                self.guest.calls.clear()
                self.guest.screen.update(width=width, height=height, orientation=orientation)
                frame = {'x': width - 30, 'y': height - 30, 'width': 20, 'height': 20}
                self.guest.address_frame = frame
                self.browser({**self.discovered(), 'frame': frame})._focus_address_field(FG)
                self.assertEqual(len(self.taps()), 1)
                self.assertEqual(self.taps()[0]['events'][0]['x'], width - 20)
                self.assertEqual(self.taps()[0]['events'][0]['y'], height - 20)

    def test_unusable_screen_state_stops_before_focus_input(self):
        screen = self.guest.screen.copy()
        invalid = [None, {}, *({**screen, key: value} for key, value in [
            ('width', True), ('width', 0), ('width', float('nan')),
            ('height', -1), ('height', float('inf')), ('scale', 0),
            ('scale', False), ('scale', float('nan')), ('orientation', True),
            ('orientation', 45), ('locked', True), ('locked', 0),
            ('screen_off', True), ('screen_off', None)])]
        for state in invalid:
            with self.subTest(state=state):
                self.guest.calls.clear(); self.guest.screen = state
                with self.assertRaisesRegex(GuestError, 'native screen geometry'):
                    self.browser(self.discovered())._focus_address_field(FG)
                self.assertEqual(self.guest.calls, [('device.screen', {})])
                self.assertEqual(self.reader.fresh, 0)

    def test_screen_transport_failure_is_not_replayed_or_followed_by_input(self):
        error = GuestTransportError('read_body', OSError('connection closed'))
        self.guest.call = Mock(side_effect=error)
        with self.assertRaises(GuestTransportError) as caught:
            self.browser(self.discovered())._focus_address_field(FG)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.guest.call.call_args_list, [call('device.screen')])
        self.assertEqual(self.reader.fresh, 0)

    def test_discovery_does_not_dispatch_a_shortcut(self):
        original = self.guest.call
        def call(method, **params):
            if method == 'input.key':
                raise AssertionError('Discovery must not send a keyboard shortcut')
            return original(method, **params)
        self.guest.call = call
        b = self.browser(self.discovered())
        self.assertEqual(b._focus_address_field(FG)['source'], 'ax_first_responder')
        self.assertEqual((self.reader.resolved, self.reader.fresh), (1, 1))
        self.assertEqual(len(self.taps()), 1)
    def test_bad_resolution_cannot_cause_tap_or_navigation_input(self):
        fields = [GuestError('Failed guard'), {**self.field, 'pid': 302},
                  {**self.field, 'source': 'other'}, {**self.field, 'identifier': 'Other'},
                  {**self.discovered(), 'enabled': False},
                  {**self.discovered(), 'frame': {'x': 1, 'y': 900, 'width': 10, 'height': 10}}]
        for field in fields:
            with self.subTest(field=field):
                self.guest.calls.clear(); b = self.browser(field)
                with self.assertRaises(GuestError): b._focus_address_field(FG)
                self.assertEqual(self.inputs(), []); self.assertEqual(self.taps(), [])
                self.assertEqual(self.reader.resolved, 1)
    def test_foreground_change_stops_before_tap(self):
        self.guest.foreground = 'com.apple.other'
        with self.assertRaises(GuestError): self.browser(self.discovered())._focus_address_field(FG)
        self.assertEqual(self.taps(), [])
    def test_post_tap_failure_is_not_replayed(self):
        self.guest.address_visible = False
        b = self.browser(self.discovered())
        with self.assertRaises(GuestError): b._focus_address_field(FG)
        self.assertEqual(len(self.taps()), 1)
        self.assertEqual(self.inputs(), [])
        self.assertEqual((self.reader.resolved, self.reader.fresh), (1,1))
    def test_cleanup_requires_exact_expected_url_before_first_close_key(self):
        b = self.browser(self.discovered())
        with self.assertRaises(GuestError):
            b.close_tab(expected_url='https://different.invalid/', expected_pid=301)
        self.assertEqual(self.inputs(), [])
        self.guest.calls.clear()
        b.close_tab(expected_url=self.field['value'], expected_pid=301)
        self.assertEqual(self.inputs(), ['cmd+w'])


class MissingAddressControlTests(unittest.TestCase):
    setUp = NativeAddressResolutionTests.setUp
    browser = NativeAddressResolutionTests.browser
    inputs = NativeAddressResolutionTests.inputs
    taps = NativeAddressResolutionTests.taps
    def test_complete_absence_reveals_once_then_requires_fresh_resolver(self):
        b = self.browser(None)
        self.assertEqual(b._focus_address_field(FG)['identifier'], 'URL')
        self.assertEqual(self.inputs(), ['cmd+l'])
        self.assertEqual(self.taps(), [])
    def test_missing_shortcut_ack_does_not_resolve_or_replay(self):
        b = self.browser(None)
        self.reader.resolve_address_field = Mock(side_effect=AssertionError('must not resolve after unknown input'))
        original = self.guest.call
        def call(method, **params):
            result = original(method, **params)
            return {} if method == 'input.key' else result
        self.guest.call = call
        with self.assertRaises(GuestError): b._focus_address_field(FG)
        self.assertEqual(self.inputs(), ['cmd+l'])
        self.reader.resolve_address_field.assert_not_called()
    def test_failed_post_shortcut_query_does_not_replay_or_tap(self):
        b = self.browser(None)
        self.reader.resolve_address_field = Mock(side_effect=GuestError('Unavailable'))
        with self.assertRaises(GuestError): b._focus_address_field(FG)
        self.assertEqual(self.inputs(), ['cmd+l'])
        self.assertEqual(self.taps(), [])
        self.reader.resolve_address_field.assert_called_once_with(301)
