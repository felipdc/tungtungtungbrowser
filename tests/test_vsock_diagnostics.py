"""Bound the diagnostic against a real local monitor connection."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

from tungtungtungbrowser.vsock_diagnostics import inspect_vsock


OWNER = {'cid': 42, 'container_id': 'original', 'host_pid': os.getpid(),
         'host_start_ticks': '123', 'host_boot_id': 'original-boot',
         'launch_started_unix': 1.0}


@contextmanager
def server(mode='normal'):
    state = {'commands': [], 'errors': []}
    with tempfile.TemporaryDirectory() as directory, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        path = Path(directory) / 'qmp.sock'
        listener.bind(str(path)); listener.listen(1); listener.settimeout(2)

        def serve():
            try:
                with listener.accept()[0] as connection:
                    connection.settimeout(2)
                    connection.sendall(b'{"QMP":{}}\n')
                    pending = b''
                    while True:
                        chunk = connection.recv(4096)
                        if not chunk:
                            return
                        pending += chunk
                        while b'\n' in pending:
                            line, pending = pending.split(b'\n', 1)
                            request = json.loads(line); state['commands'].append(request)
                            command = request['execute']; value = {}
                            if command == 'x-query-virtio':
                                value = [{'name': 'vhost-vsock', 'path': '/machine/vsock'}]
                                if mode == 'duplicate': value *= 2
                            elif command == 'qom-get': value = 43 if mode == 'wrong-cid' else 42
                            elif command == 'x-query-virtio-status': value = {'status': 15}
                            elif command == 'x-query-virtio-queue-status':
                                value = {'vring-num': 256, 'vring-desc': 0x1000,
                                         'vring-avail': 0x2000, 'vring-used': 0x3000}
                                if mode == 'uninitialized': value['vring-avail'] = 0
                                if mode == 'invalid-address': value['vring-avail'] = '0x2000;quit'
                                if mode == 'odd-address': value['vring-avail'] = 0x2001
                                if mode == 'overflow': value['vring-avail'] = 2**64 - 2
                            elif command == 'human-monitor-command':
                                address = int(request['arguments']['command-line'].split()[-1], 16)
                                value = f'{address:016x}: 0x0000 0x000b\r\n'
                                if mode == 'wrong-address': value = '000000000000ffff: 0x0000 0x000b'
                                if mode == 'events':
                                    while True:
                                        connection.sendall(b'{"event":"TEST"}\n'); time.sleep(.005)
                            reply = {'id': request['id'], 'return': value}
                            if mode == 'unsupported' and command == 'x-query-virtio-status':
                                reply = {'id': request['id'], 'error': {'class': 'CommandNotFound'}}
                            if mode == 'wrong-id': reply['id'] += 1
                            connection.sendall(b'{"event":"TEST"}\n' + json.dumps(reply).encode() + b'\n')
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as error:
                state['errors'].append(error)

        thread = threading.Thread(target=serve, daemon=True); thread.start()
        try:
            yield path, state
        finally:
            thread.join(3)
            if thread.is_alive(): raise AssertionError('Monitor did not close')
            if state['errors']: raise state['errors'][0]


class VsockDiagnosticTest(unittest.TestCase):
    def sample(self, path, **kwargs):
        return inspect_vsock(path, expected_cid=42, ownership=lambda: dict(OWNER), **kwargs)

    def test_original_endpoint_and_three_rings_use_only_fixed_queries(self):
        with server() as (path, state): result = self.sample(path)
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(len(state['commands']), 13)
        self.assertEqual(result['qmp_peer']['pid'], os.getpid())
        self.assertEqual([q['rings']['used']['index'] for q in result['queues']], [11]*3)
        allowed = {'qmp_capabilities', 'x-query-virtio', 'qom-get', 'x-query-virtio-status',
                   'x-query-virtio-queue-status', 'human-monitor-command'}
        self.assertEqual({c['execute'] for c in state['commands']}, allowed)
        reads = [c['arguments']['command-line'] for c in state['commands'] if c['execute']=='human-monitor-command']
        self.assertEqual(reads, ['xp /2hx 0x2000', 'xp /2hx 0x3000']*3)
        self.assertEqual(result['guest_rpcs'], 0)
        self.assertFalse(result['startup_authorized'])
        self.assertFalse(result['recovery'])

    def test_zero_queue_addresses_do_not_read_physical_zero(self):
        with server('uninitialized') as (path, state): result = self.sample(path)
        self.assertEqual(result['status'], 'observed')
        self.assertTrue(all(q['status']=='uninitialized_no_memory_read' for q in result['queues']))
        self.assertNotIn('human-monitor-command', [c['execute'] for c in state['commands']])

    def test_foreign_peer_sends_no_command(self):
        with server() as (path, state):
            result = inspect_vsock(path, expected_cid=42, ownership=lambda: OWNER | {'host_pid': os.getpid()+1})
        self.assertEqual(result['status'], 'failed_observation')
        self.assertEqual(state['commands'], [])

    def test_replaced_owner_stops_queries(self):
        calls = []
        def owner():
            calls.append(1)
            return OWNER if len(calls)<8 else OWNER | {'host_start_ticks': '124'}
        with server() as (path, state): result = inspect_vsock(path, expected_cid=42, ownership=owner)
        self.assertEqual(result['status'], 'failed_observation')
        self.assertNotIn('x-query-virtio-queue-status', [c['execute'] for c in state['commands']])

    def test_cid_mismatch_prevents_queue_reads(self):
        with server('wrong-cid') as (path, state): result = self.sample(path)
        self.assertEqual(result['status'], 'failed_observation')
        self.assertEqual(state['commands'][-1]['execute'], 'qom-get')

    def test_ambiguous_device_prevents_cid_or_memory_reads(self):
        with server('duplicate') as (path, state): result = self.sample(path)
        self.assertEqual(result['status'], 'failed_observation')
        self.assertEqual(state['commands'][-1]['execute'], 'x-query-virtio')

    def test_invalid_addresses_never_enter_monitor_command(self):
        for mode in ['invalid-address', 'odd-address', 'overflow']:
            with self.subTest(mode=mode), server(mode) as (path, state): result = self.sample(path)
            self.assertEqual(result['status'], 'failed_observation')
            self.assertNotIn('human-monitor-command', [c['execute'] for c in state['commands']])

    def test_wrong_address_read_is_retained_but_not_accepted(self):
        with server('wrong-address') as (path, state): result = self.sample(path)
        self.assertEqual(result['status'], 'failed_observation')
        self.assertEqual(result['queues'][0]['rings'], {})
        self.assertIn('ffff', result['commands'][-1]['result'])

    def test_unsupported_query_is_partial_evidence_without_retry(self):
        with server('unsupported') as (path, state): result = self.sample(path)
        self.assertEqual(result['status'], 'failed_observation')
        self.assertEqual(result['commands'][-1]['status'], 'failed')
        self.assertIn('CommandNotFound', result['commands'][-1]['error'])
        self.assertEqual(len(state['commands']), 4)

    def test_continuous_events_cannot_extend_deadline(self):
        with server('events') as (path, state): result = self.sample(path, timeout=.15)
        self.assertEqual(result['status'], 'failed_observation')
        self.assertLess(result['elapsed_seconds'], .8)
        self.assertEqual(len(state['commands']), 6)

    def test_mismatched_reply_stops_session(self):
        with server('wrong-id') as (path, state): result = self.sample(path)
        self.assertEqual(result['status'], 'failed_observation')
        self.assertEqual(len(state['commands']), 1)

    def test_invalid_options_are_rejected_before_connect(self):
        for timeout in [False, 0, 11, float('nan')]:
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.sample('/absent', timeout=timeout)


if __name__ == '__main__':
    unittest.main()
