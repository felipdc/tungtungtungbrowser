import asyncio
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from scripts import resident_browser as lifecycle
from tungtungtungbrowser import Browser, GuestError
from tungtungtungbrowser.resident import PORTS, ResidentSession, lease_scope
from test_browser import NativeFixture
from test_inspector import session_fixture
from test_resident_browser import BOOT, deployment


def lease_deployment(original=(0, 0)):
    state = deployment('lease')
    state['enabled'] = {'before': dict(zip(('application', 'automation'), original))}
    state['health'].update(navigation_scope='guarded-v1', active=True, guardian_pid=900,
                           guard_status=0, before_application=original[0], before_automation=original[1],
                           after_application=1, after_automation=1)
    return state


def scope_reply(state, active):
    original = state['enabled']['before']
    return state['health'] | {'status': 'scope', 'active': active,
                             'after_application': 1 if active else original['application'],
                             'after_automation': 1 if active else original['automation']}


def install(root, *, version=2):
    lease = lease_deployment()
    for kind in PORTS:
        (root / kind).mkdir()
        state = lease if kind == 'lease' else deployment(kind)
        (root / kind / 'deploy.json').write_text(json.dumps(state))
    record = {'version': version, 'status': 'complete', 'cid': 7, 'name': 'browser-resident'}
    if version == 2:
        record.update(accessibility_scope='navigation', initial_pause=scope_reply(lease, False))
    (root / 'session.json').write_text(json.dumps(record))
    return ResidentSession(root, cid=7)


class NavigationScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session = install(self.root)
        self.lease = self.session.helpers['lease']

    def reply(self, cid, kind, command):
        self.assertEqual((cid, kind), (7, 'lease'))
        self.assertEqual(command.split(' ')[1:], [str(self.lease['health']['pid']), BOOT])
        return scope_reply(self.lease, command.startswith('resume '))

    def test_default_session_never_transitions(self):
        record = {'version': 1, 'status': 'complete', 'cid': 7, 'name': 'browser-resident'}
        (self.root / 'session.json').write_text(json.dumps(record))
        session = ResidentSession(self.root, cid=7)
        with patch('tungtungtungbrowser.resident.exchange') as query, session.native_input_scope():
            session.assert_active()
        query.assert_not_called()

    def test_transitions_once_around_full_native_address_verification_and_return(self):
        events = []
        guest = NativeFixture()
        call = guest.call
        def native(method, **params):
            self.assertEqual(events[0], 'resume')
            self.assertNotIn('pause', events)
            events.append(method)
            return call(method, **params)
        guest.call = native
        def exchange(cid, kind, command):
            events.append(command.split(' ')[0])
            return self.reply(cid, kind, command)
        browser = Browser(guest=guest, input_settle=0, address_field_reader=guest.focused_field,
                          native_input_scope=self.session.native_input_scope)
        with patch('tungtungtungbrowser.resident.exchange', side_effect=exchange):
            browser.goto('https://next.invalid/', reuse_tab=True, expected_pid=301,
                         expected_url='https://example.org/')
        self.assertEqual(events[0], 'resume'); self.assertEqual(events[-1], 'pause')
        self.assertEqual(guest.calls[-1], ('input.key', {'name': 'return'}))
        rows = [json.loads(line) for line in (self.root / 'lease/requests.jsonl').read_text().splitlines()]
        self.assertEqual([r['command'] for r in rows], ['resume', 'pause'])

    def test_invalid_navigation_and_tab_arguments_send_no_transition_or_input(self):
        guest = NativeFixture()
        browser = Browser(guest=guest, native_input_scope=self.session.native_input_scope)
        with patch('tungtungtungbrowser.resident.exchange') as query:
            for operation in (lambda: browser.goto('javascript:alert(1)'),
                              lambda: browser.new_tab(expected_pid=True),
                              lambda: browser.close_tab(expected_url='about:blank', expected_pid=0)):
                with self.assertRaises(ValueError): operation()
            query.assert_not_called()
        self.assertFalse(guest.calls)

    def test_new_and_close_tab_are_scoped(self):
        guest = NativeFixture()
        browser = Browser(guest=guest, input_settle=0, address_field_reader=guest.focused_field,
                          native_input_scope=self.session.native_input_scope)
        with patch('tungtungtungbrowser.resident.exchange', side_effect=self.reply) as query:
            browser.new_tab(expected_pid=301)
            browser.close_tab(expected_url='https://example.org/', expected_pid=301)
        self.assertEqual([c.args[2].split(' ')[0] for c in query.call_args_list],
                         ['resume', 'pause', 'resume', 'pause'])

    def test_ambiguous_resume_prevents_input_and_poison_persists_across_session_objects(self):
        peer = ResidentSession(self.root, cid=7)
        with patch('tungtungtungbrowser.resident.exchange', side_effect=TimeoutError('lost acknowledgement')) as query:
            with self.assertRaises(TimeoutError), self.session.native_input_scope():
                self.fail('Uncertain activation must not submit input')
            with self.assertRaises(GuestError): peer.assert_active()
            with self.assertRaises(GuestError): ResidentSession(self.root, cid=7)
        query.assert_called_once()
        self.assertTrue((self.root / 'scope-failed.json').exists())

    def test_native_failure_still_pauses_and_preserves_original_exception(self):
        original = GuestError('unknown Return outcome')
        with patch('tungtungtungbrowser.resident.exchange', side_effect=self.reply) as query:
            with self.assertRaises(GuestError) as caught, self.session.native_input_scope():
                raise original
        self.assertIs(caught.exception, original)
        self.assertEqual(query.call_count, 2)
        self.assertFalse((self.root / 'scope-failed.json').exists())

    def test_failed_pause_preserves_action_error_and_stops_future_input(self):
        original = GuestError('unknown Return outcome')
        with patch('tungtungtungbrowser.resident.exchange',
                   side_effect=[scope_reply(self.lease, True), TimeoutError('lost pause')]) as query:
            with self.assertRaises(GuestError) as caught, self.session.native_input_scope():
                raise original
        self.assertIs(caught.exception, original)
        self.assertIn('lost pause', '\n'.join(original.__notes__))
        self.assertEqual(query.call_count, 2)
        with self.assertRaises(GuestError): self.session.assert_active()

    def test_pause_failure_without_action_error_is_not_success(self):
        with patch('tungtungtungbrowser.resident.exchange',
                   side_effect=[scope_reply(self.lease, True), TimeoutError('lost pause')]) as query:
            with self.assertRaises(TimeoutError), self.session.native_input_scope(): pass
        self.assertEqual(query.call_count, 2)

    def test_expiry_or_stop_during_action_does_not_skip_restoration(self):
        with patch('tungtungtungbrowser.resident.exchange', side_effect=self.reply) as query:
            with self.session.native_input_scope():
                self.session.accept_until = time.time() - 1
                (self.root / 'stop.json').write_text('{}')
        self.assertEqual(query.call_count, 2)
        self.assertTrue(query.call_args.args[2].startswith('pause '))

    def test_overlapping_scope_cannot_resume_or_pause_another_action(self):
        peer = ResidentSession(self.root, cid=7)
        with patch('tungtungtungbrowser.resident.exchange', side_effect=self.reply) as query:
            with self.session.native_input_scope():
                for session in (self.session, peer):
                    with self.assertRaisesRegex(GuestError, 'Another native navigation'):
                        with session.native_input_scope(): self.fail('Overlapping input')
        self.assertEqual(query.call_count, 2)

    def test_partial_foreign_or_contradictory_scope_receipts_are_rejected(self):
        good = scope_reply(self.lease, False)
        for changes in ({'active': 0}, {'after_application': 1}, {'before_automation': True},
                        {'guardian_pid': 901}, {'pid': 999}, {'boot_uuid': 'another-boot'},
                        {'navigation_scope': None}, {'guard_status': False}, {'status': 'ready'}):
            with self.subTest(changes=changes), self.assertRaises(GuestError):
                lease_scope(good | changes, self.lease, active=False)
        for original in ((0, 0), (0, 1), (1, 0), (1, 1)):
            state = lease_deployment(original)
            lease_scope(scope_reply(state, False), state, active=False)
            lease_scope(scope_reply(state, True), state, active=True)


class LifecycleNavigationTests(unittest.TestCase):
    def test_missing_capability_or_uncertain_initial_pause_closes_helpers_without_retry(self):
        for capable in (False, True):
            with self.subTest(capable=capable), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / 'session'
                lease = lease_deployment() if capable else deployment('lease')
                def helper(guest, kind, data, hashes, directory):
                    return lease if kind == 'lease' else deployment(kind)
                with patch.object(lifecycle, 'read_build', return_value=(
                        {'binaries': dict.fromkeys(PORTS, {})}, dict.fromkeys(PORTS, {}))), \
                        patch.object(lifecycle, 'start_helper', side_effect=helper), \
                        patch.object(lifecycle, 'stop', return_value={'status': 'closed'}) as close, \
                        patch.object(lifecycle, 'exchange', side_effect=[ConnectionRefusedError()] * 3 + [TimeoutError('lost pause')]) as query:
                    with self.assertRaises((GuestError, TimeoutError)):
                        lifecycle.start('/build', root, cid=7, guest=Mock(cid=7), accessibility_scope='navigation')
                self.assertEqual(query.call_count, 4 if capable else 3)
                close.assert_called_once()
                record = json.loads((root / 'session.json').read_text())
                self.assertEqual(record['status'], 'failed_no_retry')
                self.assertNotIn('initial_pause', record)

    def test_start_records_verified_pause_before_exposing_complete_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'session'
            lease = lease_deployment()
            def start_helper(guest, kind, data, hashes, directory):
                directory.mkdir()
                state = lease if kind == 'lease' else deployment(kind)
                (directory / 'deploy.json').write_text(json.dumps(state))
                return state
            with patch.object(lifecycle, 'read_build', return_value=(
                    {'binaries': dict.fromkeys(PORTS, {})}, dict.fromkeys(PORTS, {}))), \
                    patch.object(lifecycle, 'start_helper', side_effect=start_helper), \
                    patch.object(lifecycle, 'exchange', side_effect=[ConnectionRefusedError()] * 3 + [scope_reply(lease, False)]) as query:
                result = lifecycle.start('/build', root, cid=7, guest=Mock(cid=7), accessibility_scope='navigation')
            self.assertEqual(result['version'], 2); self.assertEqual(result['status'], 'complete')
            self.assertTrue(result['pause_requested']); self.assertFalse(result['initial_pause']['active'])
            self.assertEqual(query.call_count, 4)
            self.assertTrue(ResidentSession(root, cid=7).navigation_scoped)


class CancellationNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_inspector_wait_retains_scope_until_actual_native_work_ends(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = install(Path(temporary))
            entered, release = threading.Event(), threading.Event()
            paused = threading.Event()
            guest = NativeFixture()
            original_call = guest.call
            def native(method, **params):
                if method == 'input.paste':
                    entered.set()
                    if not release.wait(4): raise TimeoutError('Test did not release native input')
                self.assertFalse(paused.is_set())
                return original_call(method, **params)
            guest.call = native
            browser = Browser(guest=guest, input_settle=0, address_field_reader=guest.focused_field,
                              native_input_scope=session.native_input_scope)
            inspector = session_fixture(browser=browser)
            def exchange(cid, kind, command):
                active = command.startswith('resume ')
                if not active: paused.set()
                return scope_reply(session.helpers['lease'], active)
            with patch('tungtungtungbrowser.resident.exchange', side_effect=exchange) as query:
                task = asyncio.create_task(inspector._native(browser.goto, 'https://next.invalid/', reuse_tab=True))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    task.cancel(); await asyncio.sleep(0)
                    self.assertFalse(paused.is_set()); self.assertFalse(task.done())
                    self.assertEqual(query.call_count, 1)
                finally:
                    release.set()
                with self.assertRaises(asyncio.CancelledError): await task
                self.assertTrue(paused.is_set()); self.assertEqual(query.call_count, 2)
                self.assertEqual(guest.calls[-1], ('input.key', {'name': 'return'}))
                self.assertFalse(inspector._native_tasks)


if __name__ == '__main__': unittest.main()
