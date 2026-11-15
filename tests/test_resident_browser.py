import copy
import json
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from scripts import resident_browser as lifecycle
from tungtungtungbrowser.guest import Guest, GuestError
from tungtungtungbrowser.native_focus import NativeFocusReader
from tungtungtungbrowser.resident import (PORTS, PROTOCOLS, ResidentGuest, ResidentSession,
                                        exchange, identity)
from test_native_focus import native_result
from test_native_keyboard import receipt, envelope

BOOT = '12345678-1234-4234-8234-123456789abc'
SHA = 'a' * 64


def owner(kind, status='ready'):
    return {'protocol': 2 if status == 'spawned' else PROTOCOLS[kind], 'status': status,
            'pid': PORTS[kind] + 100, 'boot_uuid': BOOT, 'port': PORTS[kind],
            **({'spawn_error': 0, 'actions_destroy_error': 0, 'attributes_destroy_error': 0}
               if status == 'spawned' else {})}


def deployment(kind):
    return {'version': 1, 'status': 'complete', 'kind': kind, 'cid': 7,
            'health': owner(kind), 'spawn': owner(kind, 'spawned'), 'spawn_requested': True,
            'launch_requested_unix': time.time(),
            'binaries': ({'guardian': SHA, 'controller': SHA} if kind == 'lease' else {'server': SHA}),
            'log_path': '/private/var/root/ttt-resident-' + kind + '-' + 'b' * 32 + '.log'}


def foreground():
    return {'protocol': 2, 'status': 'observed', 'helper_pid': owner('control')['pid'],
            'boot_uuid': BOOT, 'pid': 301, 'process_start': 1234, 'bundle_id': 'com.apple.mobilesafari',
            'verified': True, 'strict_ambiguity': True, 'source': 'runningboard_unique_focal_process',
            'focal_process_count': 1, 'candidate_count': 3, 'screen_state_valid': True,
            'locked': 0, 'screen_off': 0}


class ResidentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'session.json').write_text(json.dumps({
            'version': 1, 'name': 'browser-resident', 'status': 'complete', 'cid': 7}))
        for kind in PORTS:
            (self.root / kind).mkdir()
            (self.root / kind / 'deploy.json').write_text(json.dumps(deployment(kind)))
        self.session = ResidentSession(self.root, cid=7)
        self.base = Guest(cid=7)
        self.base.call = Mock(return_value={'ordinary': True})
        self.guest = ResidentGuest(self.base, self.session)

    def test_configuration_never_contacts_guest_and_rejects_other_cid(self):
        with patch('tungtungtungbrowser.resident.exchange', side_effect=AssertionError('unexpected')):
            ResidentSession(self.root, cid=7)
            with self.assertRaises(ValueError): ResidentSession(self.root, cid=8)
            with self.assertRaises(ValueError): ResidentGuest(Guest(cid=8), self.session)

    def test_invalid_binary_evidence_is_rejected_before_any_input(self):
        path = self.root / 'control/deploy.json'
        with patch('tungtungtungbrowser.resident.exchange') as query:
            for hashes in (None, {}, {'server': 'bad'}, {'server': True}):
                path.write_text(json.dumps(deployment('control') | {'binaries': hashes}))
                with self.assertRaisesRegex(ValueError, 'binary hashes'):
                    ResidentSession(self.root, cid=7)
            query.assert_not_called()

    def test_foreground_is_fresh_and_partial_or_ambiguous_claims_fail(self):
        with patch('tungtungtungbrowser.resident.exchange', return_value=foreground()) as query:
            self.assertTrue(self.guest.call('apps.foreground')['verified'])
            self.assertTrue(self.guest.call('apps.foreground')['verified'])
            self.assertEqual(query.call_count, 2)
            for changes in ({'focal_process_count': 2}, {'locked': 1}, {'screen_state_valid': False},
                            {'process_start': 0}, {'helper_pid': 999}, {'boot_uuid': 'another-boot'},
                            {'verified': 1}, {'strict_ambiguity': False}, {'pid': True}):
                query.return_value = foreground() | changes
                with self.subTest(changes=changes), self.assertRaises(GuestError):
                    self.guest.call('apps.foreground')
        self.base.call.assert_not_called()

    def test_key_names_owner_in_request_and_balanced_events_are_validated(self):
        answer = {'protocol': 2, 'status': 'key', 'helper_pid': owner('control')['pid'],
                  'boot_uuid': BOOT, 'exit_code': 0, 'result': receipt('cmd+w')}
        with patch('tungtungtungbrowser.resident.exchange', return_value=answer) as query:
            result = self.guest.call('input.key', name='cmd+w')
            self.assertEqual(result['method'], 'native_resident_keyboard')
            query.assert_called_once_with(7, 'control', f"key {owner('control')['pid']} {BOOT} cmd+w")
            bad = copy.deepcopy(answer); bad['result']['events'][-1]['down'] = 1
            query.return_value = bad
            with self.assertRaises(GuestError): self.guest.call('input.key', name='cmd+w')
        self.base.call.assert_not_called()

    def test_uncertain_key_has_no_replay_and_no_daemon_fallback(self):
        with patch('tungtungtungbrowser.resident.exchange', side_effect=TimeoutError()) as query:
            with self.assertRaises(TimeoutError): self.guest.call('input.key', name='return')
            query.assert_called_once()
        self.base.call.assert_not_called()

    def test_invalid_key_and_expired_or_closing_session_send_nothing(self):
        with patch('tungtungtungbrowser.resident.exchange') as query:
            for params in ({}, {'name': 'cmd+shift+w'}, {'name': 'return', 'extra': 1}):
                with self.assertRaises(ValueError): self.guest.call('input.key', **params)
            self.session.accept_until = time.time() - 1
            with self.assertRaises(GuestError): self.guest.call('input.key', name='return')
            self.session.accept_until = time.time() + 100
            (self.root / 'stop.json').write_text('{}')
            with self.assertRaises(GuestError): self.guest.call('input.key', name='return')
            query.assert_not_called()

    def test_paste_stays_on_original_transport(self):
        self.assertEqual(self.guest.call('input.paste', text='https://fixture.invalid/'), {'ordinary': True})
        self.base.call.assert_called_once_with('input.paste', text='https://fixture.invalid/')

    def test_resident_focus_keeps_public_validation_and_read_retry_bound(self):
        reader = NativeFocusReader(self.guest, sha256=SHA, deployment_id='b' * 32,
                                   max_read_attempts=3, pid_query=self.session.focus)
        def wrap(result):
            return owner('focus') | {'status': 'query', 'exit_code': 0, 'result': result}
        with patch('tungtungtungbrowser.resident.exchange', return_value=wrap(native_result())) as query:
            self.assertEqual(reader(301)['source'], 'ax_first_responder')
            query.assert_called_once_with(7, 'focus', 'pid 301')
            bad = native_result(); bad['element']['pid'] = 302
            query.reset_mock(); query.return_value = wrap(bad)
            with self.assertRaises(GuestError): reader(301)
            query.assert_called_once()  # A foreign owner is not a retryable read.
            absent = native_result() | {'error': -25216, 'element': None}
            query.reset_mock(); query.return_value = wrap(absent)
            with self.assertRaises(GuestError): reader(301)
            self.assertEqual(query.call_count, 3)
            bad = native_result() | {'guard_restored': False}
            query.reset_mock(); query.return_value = wrap(bad)
            with self.assertRaises(GuestError): reader(301)
            query.assert_called_once()
        self.base.call.assert_not_called()


class ExchangeTests(unittest.TestCase):
    def socket(self, chunks):
        sock = Mock(); sock.recv.side_effect = chunks
        context = Mock(); context.__enter__ = Mock(return_value=sock); context.__exit__ = Mock(return_value=False)
        return sock, context

    def test_nested_newline_is_not_mistaken_for_complete_response(self):
        sock, context = self.socket([b'{"protocol":2,"result":{"value":1}\n', b',"exit_code":0}\n'])
        with patch('socket.socket', return_value=context):
            self.assertEqual(exchange(7, 'focus', 'pid 301')['result'], {'value': 1})
        sock.sendall.assert_called_once_with(b'pid 301\n')

    def test_reset_after_send_is_never_reclassified_as_no_listener(self):
        sock, context = self.socket([ConnectionResetError()])
        with patch('socket.socket', return_value=context), self.assertRaises(ConnectionResetError):
            exchange(7, 'control', 'health')
        sock.sendall.assert_called_once()
        sock.connect.side_effect = ConnectionResetError()
        with patch('socket.socket', return_value=context), self.assertRaises(ConnectionRefusedError):
            exchange(7, 'control', 'health')
        self.assertEqual(sock.sendall.call_count, 1)

    def test_invalid_protocol_identity_and_oversized_response_fail(self):
        for changes in ({'pid': True}, {'protocol': True}, {'port': 1346}, {'boot_uuid': 'bad'}):
            with self.assertRaises(GuestError): identity(owner('focus') | changes, 'focus')
        sock, context = self.socket([b'x' * 4096] * 16)
        with patch('socket.socket', return_value=context), self.assertRaisesRegex(GuestError, 'Oversized'):
            exchange(7, 'focus', 'health')


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.guest = Mock(cid=7)

    def test_all_builds_verified_before_first_guest_operation(self):
        with patch.object(lifecycle, 'read_build', side_effect=ValueError('source mismatch')):
            with self.assertRaises(ValueError): lifecycle.start('/build', self.root/'session', cid=7, guest=self.guest)
        self.guest._rpc.assert_not_called(); self.assertFalse((self.root/'session').exists())

    def test_existing_listener_prevents_upload_and_is_not_stopped(self):
        with patch.object(lifecycle, 'read_build', return_value=({}, {})), \
                patch.object(lifecycle, 'exchange', return_value=owner('lease')) as query:
            with self.assertRaises(GuestError): lifecycle.start('/build', self.root/'session', cid=7, guest=self.guest)
        self.guest._rpc.assert_not_called(); query.assert_called_once_with(7, 'lease', 'health', timeout=5)

    def setup_upload(self, *, uncertain=False):
        data = b'fixture binary'
        def rpc(method, **params):
            if method == 'files.write': return {'bytes': len(data)}
            if method == 'files.chmod': return {}
            if method == 'files.read':
                import base64
                return {'truncated': False, 'content': base64.b64encode(data).decode()}
            if method == 'script.eval':
                if uncertain: raise TimeoutError('lost spawn acknowledgment')
                return envelope(owner('focus', 'spawned'))
            raise AssertionError(method)
        self.guest._rpc.side_effect = rpc
        return data

    def test_uncertain_launch_reconciles_owned_log_without_second_spawn(self):
        data = self.setup_upload(uncertain=True)
        with patch.object(lifecycle, 'exchange', side_effect=[ConnectionRefusedError(), owner('focus')]), \
                patch.object(lifecycle, 'log_rows', return_value=[owner('focus', 'listening')]):
            result = lifecycle.start_helper(self.guest, 'focus', {'server': data}, {'server': SHA}, self.root/'focus')
        self.assertTrue(result['spawn_reconciled']); self.assertEqual(result['status'], 'complete')
        self.assertEqual(sum(c.args[0] == 'script.eval' for c in self.guest._rpc.call_args_list), 1)

    def test_uncertain_launch_without_identity_never_replays(self):
        data = self.setup_upload(uncertain=True)
        with patch.object(lifecycle, 'exchange', side_effect=ConnectionRefusedError()), \
                patch.object(lifecycle, 'log_rows', return_value=[]):
            with self.assertRaises(GuestError):
                lifecycle.start_helper(self.guest, 'focus', {'server': data}, {'server': SHA}, self.root/'focus')
        self.assertEqual(sum(c.args[0] == 'script.eval' for c in self.guest._rpc.call_args_list), 1)
        self.assertEqual(json.loads((self.root/'focus/deploy.json').read_text())['status'], 'failed_no_retry')

    def test_lost_stop_ack_uses_closure_log_and_does_not_repeat_stop(self):
        directory = self.root/'focus'; directory.mkdir()
        (directory/'deploy.json').write_text(json.dumps(deployment('focus')))
        closed = owner('focus', 'closed') | {'guard_pool': {'remaining': 0, 'failed': 0}}
        with patch.object(lifecycle, 'exchange', side_effect=[owner('focus'), TimeoutError()]) as query, \
                patch.object(lifecycle, 'log_rows', side_effect=[[], [closed]]), patch.object(lifecycle.time, 'sleep'):
            result = lifecycle.stop_helper(self.guest, directory, 'focus')
            self.assertEqual(result['status'], 'listener_closed')
            lifecycle.stop_helper(self.guest, directory, 'focus')
        self.assertEqual(query.call_count, 2)
        self.assertTrue(query.call_args.args[2].startswith('stop '))

    def test_lease_must_restore_its_original_baseline(self):
        state = deployment('lease') | {'enabled': {'before': {'application': 0, 'automation': 1}}}
        closed = owner('lease', 'closed') | {'restored': True, 'before': state['enabled']['before'],
                                           'after': {'application': 1, 'automation': 1}}
        with self.assertRaises(GuestError): lifecycle.closed_record([closed], state, 'lease')
        closed['after'] = closed['before']
        self.assertEqual(lifecycle.closed_record([closed], state, 'lease'), closed)

    def test_stop_refuses_different_process_and_does_not_send_stop(self):
        directory = self.root/'control'; directory.mkdir()
        (directory/'deploy.json').write_text(json.dumps(deployment('control')))
        with patch.object(lifecycle, 'log_rows', return_value=[]), \
                patch.object(lifecycle, 'exchange', return_value=owner('control') | {'pid': 999}) as query:
            with self.assertRaises(GuestError): lifecycle.stop_helper(self.guest, directory, 'control')
        query.assert_called_once_with(7, 'control', 'health', timeout=5)


if __name__ == '__main__':
    unittest.main()
