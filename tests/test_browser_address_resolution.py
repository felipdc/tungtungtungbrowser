import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tungtungtungbrowser import Browser
from test_browser import NativeFixture
from tungtungtungbrowser import GuestError

FG = {'pid': 301, 'bundle_id': 'com.apple.mobilesafari', 'verified': True}


class Reader:
    supports_address_resolution = True
    last_resolution = None
    last_read_attempts = []
    def __init__(self, guest, field):
        self.guest, self.field, self.resolved, self.fresh = guest, field, 0, 0
    def resolve_address_field(self, pid):
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
        self.screen = Mock(width=1179, height=2556, coordinate_scale=3)
        self.screen.metadata.return_value = {'width': 1179, 'height': 2556}
        p = patch('tungtungtungbrowser.browser.Screenshot.from_native', return_value=self.screen)
        p.start(); self.addCleanup(p.stop)
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
        self.assertEqual(self.inputs(), ['cmd+l'])
        self.assertEqual(self.taps(), [])
        self.assertEqual((self.reader.resolved, self.reader.fresh), (1,0))
    def test_discovered_or_nonediting_field_gets_one_tap_and_fresh_read(self):
        for field in [self.discovered(), {**self.field, 'traits': 0}]:
            self.guest.calls.clear(); b = self.browser(field)
            self.assertEqual(b._focus_address_field(FG)['source'], 'ax_first_responder')
            self.assertEqual(self.inputs(), ['cmd+l'])
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

    def test_uncertain_shortcut_stops_before_read_or_tap(self):
        original = self.guest.call
        for ack in [None, {'key': 'cmd+l', 'dispatched': False}, GuestError('Lost reply')]:
            def call(method, **params):
                if method == 'input.key':
                    if isinstance(ack, Exception): raise ack
                    return ack
                return original(method, **params)
            self.guest.call = call
            b = self.browser(self.discovered())
            with self.assertRaises(GuestError): b._focus_address_field(FG)
            self.assertEqual(self.reader.resolved, 0)
            self.assertEqual(self.taps(), [])
    def test_bad_resolution_cannot_cause_tap_or_navigation_input(self):
        fields = [None, GuestError('Failed guard'), {**self.field, 'pid': 302},
                  {**self.field, 'source': 'other'}, {**self.field, 'identifier': 'Other'},
                  {**self.discovered(), 'enabled': False},
                  {**self.discovered(), 'frame': {'x': 1, 'y': 900, 'width': 10, 'height': 10}}]
        for field in fields:
            with self.subTest(field=field):
                self.guest.calls.clear(); b = self.browser(field)
                with self.assertRaises(GuestError): b._focus_address_field(FG)
                self.assertEqual(self.inputs(), ['cmd+l']); self.assertEqual(self.taps(), [])
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
        self.assertEqual(self.inputs(), ['cmd+l'])
        self.assertEqual((self.reader.resolved, self.reader.fresh), (1,1))
    def test_cleanup_requires_exact_expected_url_before_first_close_key(self):
        b = self.browser(self.discovered())
        with self.assertRaises(GuestError):
            b.close_tab(expected_url='https://different.invalid/', expected_pid=301)
        self.assertEqual(self.inputs(), ['cmd+l'])
        self.guest.calls.clear()
        b.close_tab(expected_url=self.field['value'], expected_pid=301)
        self.assertEqual(self.inputs(), ['cmd+l', 'cmd+w'])
