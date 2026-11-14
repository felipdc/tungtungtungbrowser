import base64
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts import safari_udp_guard as guard
from tungtungtungbrowser.guest import GuestError, GuestTransportError


HEALTH = {'protocol': 2, 'status': 'ready', 'pid': 123, 'port': guard.PORT,
          'boot_uuid': '00000000-0000-0000-0000-000000000001'}
POLICY = {'status': 'safari_udp_policy_applied', 'policy_id': 1, 'protocol': 17,
          'priority': 4, 'other_applications': 'unchanged',
          'application_uuid': '00000000-0000-0000-0000-000000000002'}
HELPER = b'helper'
DIGEST = hashlib.sha256(HELPER).hexdigest()


class Guest:
    cid = 61

    def __init__(self, *, uncertain=False, corrupt=False, closed=None):
        self.calls = []
        self.uncertain, self.corrupt, self.closed = uncertain, corrupt, closed

    def _rpc(self, method, **params):
        self.calls.append((method, params))
        if method == 'files.write':
            self.data = base64.b64decode(params['content'])
            return {'bytes': len(self.data)}
        if method == 'files.read':
            if params.get('binary'):
                data = b'wrong' if self.corrupt else self.data
                return {'content': base64.b64encode(data).decode(), 'truncated': False}
            rows = [POLICY, HEALTH | {'status': 'listening'}]
            if self.closed is not None:
                rows.append({'status': 'owned_policy_session_closed', 'removed': self.closed, 'applied': True})
            return {'content': '\n'.join(json.dumps(row) for row in rows), 'truncated': False}
        if method == 'files.chmod':
            return {}
        if method == 'script.eval':
            if self.uncertain:
                raise GuestTransportError('receive_headers', TimeoutError('uncertain launch'))
            return {'ok': True, 'crashed': False, 'result': json.dumps({
                'status': 0, 'signal': None,
                'stdout': json.dumps(HEALTH | {'status': 'spawned', 'spawn_error': 0})})}
        raise AssertionError(method)


class SafariUDPGuardTests(unittest.TestCase):
    def test_connect_stage_reset_is_refusal_without_sending_a_request(self):
        sock = MagicMock()
        sock.__enter__.return_value = sock
        sock.connect.side_effect = ConnectionResetError('unopened guest port')
        with patch.object(guard.socket, 'socket', return_value=sock):
            with self.assertRaises(ConnectionRefusedError):
                guard.exchange(61, 'health')
        sock.sendall.assert_not_called()

    def test_reset_after_connection_does_not_authorize_deployment(self):
        sock = MagicMock()
        sock.__enter__.return_value = sock
        sock.recv.side_effect = ConnectionResetError('peer reset after request')
        with patch.object(guard.socket, 'socket', return_value=sock):
            with self.assertRaises(ConnectionResetError):
                guard.exchange(61, 'health')
        sock.sendall.assert_called_once_with(b'health\n')

    def install(self, directory, guest=None):
        guest = guest or Guest()
        with patch.object(guard, 'exchange', side_effect=[ConnectionRefusedError(), HEALTH]):
            result = guard.install(guest, HELPER, DIGEST, Path(directory) / 'deployment')
        return guest, result

    def test_install_requires_native_policy_and_matching_live_process(self):
        with tempfile.TemporaryDirectory() as directory:
            guest, result = self.install(directory)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(result['native_policy'], POLICY)
            self.assertFalse(result['native_input_sent'])
            self.assertEqual(sum(method == 'script.eval' for method, _ in guest.calls), 1)
            record = guard.read_deployment(Path(directory) / 'deployment/deploy.json', guest.cid)
            self.assertEqual(record['health'], HEALTH)

    def test_foreign_guard_refuses_before_any_guest_write(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(guard, 'exchange', return_value=HEALTH):
            guest = Guest()
            with self.assertRaisesRegex(GuestError, 'already exists'):
                guard.install(guest, HELPER, DIGEST, Path(directory) / 'deployment')
            self.assertEqual(guest.calls, [])

    def test_unavailable_guest_does_not_look_like_an_unused_port(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(guard, 'exchange', side_effect=OSError('no guest')):
            guest = Guest()
            with self.assertRaises(OSError):
                guard.install(guest, HELPER, DIGEST, Path(directory) / 'deployment')
            self.assertEqual(guest.calls, [])

    def test_corrupt_readback_prevents_launch(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(guard, 'exchange', side_effect=ConnectionRefusedError):
            guest = Guest(corrupt=True)
            with self.assertRaisesRegex(GuestError, 'readback'):
                guard.install(guest, HELPER, DIGEST, Path(directory) / 'deployment')
            self.assertNotIn('script.eval', [method for method, _ in guest.calls])

    def test_uncertain_launch_is_reconciled_from_owned_log_without_respawn(self):
        with tempfile.TemporaryDirectory() as directory:
            guest, result = self.install(directory, Guest(uncertain=True))
            self.assertTrue(result['spawn_reconciled_from_owned_log'])
            self.assertIn('launch_error', result)
            self.assertEqual(sum(method == 'script.eval' for method, _ in guest.calls), 1)

    def test_replaced_process_is_rejected_without_second_spawn(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(guard, 'exchange', side_effect=[
                ConnectionRefusedError(), HEALTH | {'pid': 124}]):
            guest = Guest()
            with self.assertRaisesRegex(GuestError, 'another process'):
                guard.install(guest, HELPER, DIGEST, Path(directory) / 'deployment')
            report = json.loads((Path(directory) / 'deployment/deploy.json').read_text())
            self.assertEqual(report['status'], 'failed_no_retry')
            self.assertEqual(sum(method == 'script.eval' for method, _ in guest.calls), 1)

    def test_stop_checks_identity_before_sending_owned_stop_and_confirms_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            _, record = self.install(directory)
            with patch.object(guard, 'exchange', side_effect=[HEALTH, HEALTH | {'status': 'stopping'}]) as control:
                result = guard.stop(Guest(closed=True), record, Path(directory) / 'stop.json')
            self.assertEqual(result['status'], 'owned_policy_session_closed')
            self.assertEqual(control.call_args_list[1].args, (61, f"stop 123 {HEALTH['boot_uuid']}"))

    def test_stop_refuses_another_boot_and_never_signals_the_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            _, record = self.install(directory)
            with patch.object(guard, 'exchange', return_value=HEALTH | {
                    'boot_uuid': '00000000-0000-0000-0000-000000000003'}) as control:
                with self.assertRaisesRegex(GuestError, 'another process'):
                    guard.stop(Guest(), record, Path(directory) / 'stop.json')
            self.assertEqual(control.call_count, 1)
            self.assertFalse(json.loads((Path(directory) / 'stop.json').read_text())['stop_requested'])

    def test_acknowledgment_is_insufficient_without_successful_policy_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            _, record = self.install(directory)
            with patch.object(guard, 'exchange', side_effect=[HEALTH, HEALTH | {'status': 'stopping'}]):
                with self.assertRaisesRegex(GuestError, 'removal was not verified'):
                    guard.stop(Guest(closed=False), record, Path(directory) / 'stop.json')
            self.assertEqual(json.loads((Path(directory) / 'stop.json').read_text())['status'], 'failed_no_retry')

    def test_existing_evidence_and_wrong_cid_prevent_another_action(self):
        with tempfile.TemporaryDirectory() as directory:
            guest, record = self.install(directory)
            with patch.object(guard, 'exchange') as control:
                with self.assertRaises(FileExistsError):
                    guard.install(guest, HELPER, DIGEST, Path(directory) / 'deployment')
                with self.assertRaises(ValueError):
                    guard.read_deployment(Path(directory) / 'deployment/deploy.json', 62)
                output = Path(directory) / 'stop.json'
                output.touch()
                with self.assertRaises(FileExistsError):
                    guard.stop(guest, record, output)
                control.assert_not_called()

    def test_build_hashes_include_sources_and_reject_changed_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            data = struct.pack('<8I', 0xFEEDFACF, 0x100000C, 0, 2, 0, 0, 0, 0)
            (directory / 'safari-udp-guard').write_bytes(data)
            manifest = {'protocol': 1, 'binary': 'safari-udp-guard',
                        'binary_sha256': hashlib.sha256(data).hexdigest(),
                        'sources': {name: hashlib.sha256((guard.ROOT / 'native' / name).read_bytes()).hexdigest()
                                    for name in guard.SOURCES}}
            (directory / 'build.json').write_text(json.dumps(manifest))
            self.assertEqual(guard.read_build(directory)[0], data)
            (directory / 'safari-udp-guard').write_bytes(data + b'changed')
            with self.assertRaises(ValueError):
                guard.read_build(directory)


if __name__ == '__main__':
    unittest.main()
