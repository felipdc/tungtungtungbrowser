import unittest
from unittest.mock import Mock, patch

from tungtungtungbrowser import Browser, GuestError, NativeFocusReader
from test_browser import NativeFixture
from test_native_focus import SHA, TOKEN, native_result
from test_owned_session import fixture


def unavailable():
    return dict(native_result(), error=-25216, element=None)


class NativeFocusWaitTests(unittest.TestCase):
    def reader(self, results):
        query = Mock(side_effect=results)
        return query, NativeFocusReader(Mock(), sha256=SHA, deployment_id=TOKEN, pid_query=query)

    def test_three_completed_timeouts_can_be_followed_by_one_fresh_read(self):
        query, reader = self.reader([unavailable()] * 3 + [native_result()])
        with patch('tungtungtungbrowser.native_focus.time.sleep'):
            self.assertEqual(reader.wait_for_focus(301)['identifier'], 'URL')
        self.assertEqual(query.call_count, 4)
        self.assertEqual([r['status'] for r in reader.last_read_attempts], ['failed'] * 3 + ['read'])
        self.assertEqual(reader.last_read_wait['status'], 'read')
        # A later ordinary call retains its two-read default.
        query.reset_mock(side_effect=True)
        query.side_effect = [unavailable()] * 3
        with patch('tungtungtungbrowser.native_focus.time.sleep'), self.assertRaises(GuestError):
            reader(301)
        self.assertEqual(query.call_count, 2)
        self.assertIsNone(reader.last_read_wait)

    def test_ten_completed_reads_is_a_hard_limit_even_if_deadline_remains(self):
        query, reader = self.reader([unavailable()] * 11)
        with patch('tungtungtungbrowser.native_focus.time.sleep'), self.assertRaises(GuestError):
            reader.wait_for_focus(301)
        self.assertEqual(query.call_count, 10)
        self.assertEqual(reader.last_read_wait['status'], 'failed')

    def test_uncertain_unrestored_foreign_or_absent_results_are_not_retried(self):
        foreign = native_result()
        foreign['element']['pid'] = 302
        cases = [GuestError('transport uncertain'), dict(unavailable(), guard_restored=False),
                 dict(unavailable(), requested_pid=302), foreign,
                 dict(unavailable(), error=-25204), dict(unavailable(), error=0)]
        for result in cases:
            with self.subTest(result=result):
                query, reader = self.reader([result])
                if isinstance(result, dict) and result['error'] == 0 and result['element'] is None:
                    self.assertIsNone(reader.wait_for_focus(301))
                else:
                    with self.assertRaises(GuestError):
                        reader.wait_for_focus(301)
                query.assert_called_once_with(301)

    def test_deadline_discards_late_success_or_failure_without_next_read(self):
        for result in (native_result(), unavailable()):
            with self.subTest(result=result):
                query, reader = self.reader([])
                clock = [0.0]
                def complete(_):
                    clock[0] = 16.0
                    return result
                query.side_effect = complete
                with patch('tungtungtungbrowser.native_focus.time.monotonic', side_effect=lambda: clock[0]), \
                        patch('tungtungtungbrowser.native_focus.time.sleep'), \
                        self.assertRaisesRegex(GuestError, 'deadline exceeded'):
                    reader.wait_for_focus(301, timeout=15)
                query.assert_called_once()
                self.assertEqual(reader.last_read_wait['status'], 'failed')

    def test_invalid_budgets_do_not_query(self):
        query, reader = self.reader([])
        for timeout in (0, -1, 31, True, None, '15', float('inf'), float('nan')):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                reader.wait_for_focus(301, timeout=timeout)
        query.assert_not_called()

    def test_navigation_wait_repeats_reads_but_each_input_occurs_once(self):
        guest = NativeFixture()
        query, reader = self.reader([])
        remaining = [3]
        def observe(pid):
            if remaining[0]:
                remaining[0] -= 1
                return unavailable()
            result = native_result()
            result['element']['attributes']['value']['value'] = guest.address_value
            return result
        query.side_effect = observe
        browser = Browser(guest=guest, input_settle=0, address_focus='shortcut', address_field_reader=reader)
        with patch('tungtungtungbrowser.native_focus.time.sleep'):
            result = browser.goto('https://next.invalid/', reuse_tab=True,
                                  expected_url=guest.address_value, expected_pid=301, focus_read_timeout=15)
        self.assertEqual(result['method'], 'native_address_bar')
        inputs = [c for c in guest.calls if c[0].startswith('input.')]
        self.assertEqual(inputs, [('input.key', {'name': 'cmd+l'}), ('input.key', {'name': 'cmd+a'}),
                                  ('input.paste', {'text': 'https://next.invalid/'}),
                                  ('input.key', {'name': 'return'})])
        self.assertEqual(query.call_count, 5)

    def test_exhaustion_before_or_after_paste_never_submits_navigation(self):
        for after_paste in (False, True):
            guest = NativeFixture()
            valid = native_result()
            valid['element']['attributes']['value']['value'] = guest.address_value
            query, reader = self.reader(([valid] if after_paste else []) + [unavailable()] * 11)
            browser = Browser(guest=guest, input_settle=0, address_focus='shortcut', address_field_reader=reader)
            with patch('tungtungtungbrowser.native_focus.time.sleep'), self.assertRaises(GuestError):
                browser.goto('https://next.invalid/', reuse_tab=True, focus_read_timeout=15)
            self.assertEqual(sum(c == ('input.key', {'name': 'cmd+l'}) for c in guest.calls), 1)
            self.assertNotIn(('input.key', {'name': 'return'}), guest.calls)
            self.assertEqual(sum(c[0] == 'input.paste' for c in guest.calls), int(after_paste))

    def test_unsupported_or_invalid_wait_is_rejected_before_input(self):
        for timeout, reuse, shortcut, capable in [(True, True, True, True), (31, True, True, True),
                (15, False, True, True), (15, True, False, True), (15, True, True, False)]:
            guest = NativeFixture()
            _, reader = self.reader([])
            browser = Browser(guest=guest, address_focus='shortcut' if shortcut else 'auto',
                              address_field_reader=reader if capable else guest.focused_field)
            with self.assertRaises(ValueError):
                browser.goto('https://next.invalid/', reuse_tab=reuse, focus_read_timeout=timeout)
            self.assertEqual(guest.calls, [])


class InspectorFocusWaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_is_explicit_for_one_navigation_and_keeps_tab_checks(self):
        session, native, guest = fixture()
        session.browser.address_focus = 'shortcut'
        reader = Mock(side_effect=guest.focused_field)
        reader.wait_for_focus.side_effect = lambda pid, timeout: guest.focused_field(pid)
        session.browser.address_field_reader = reader
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            async with session:
                await session.goto('https://next.invalid/', focus_read_timeout=15)
                self.assertEqual(reader.wait_for_focus.call_count, 2)
                self.assertEqual(reader.wait_for_focus.call_args.kwargs, {'timeout': 15})
                await session.goto('https://last.invalid/')
                self.assertEqual(reader.wait_for_focus.call_count, 2)
                self.assertEqual(guest.address_value, 'https://last.invalid/')

    async def test_invalid_wait_never_starts_inspector_or_native_work(self):
        session, native, guest = fixture()
        for timeout in (True, -1, 31, float('nan'), '15'):
            with self.assertRaises(ValueError):
                await session.goto('https://next.invalid/', focus_read_timeout=timeout)
        self.assertEqual(guest.calls, [])
        self.assertEqual(native.commands, [])
