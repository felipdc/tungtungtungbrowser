import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
import unittest


class LeaseControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        root = Path(cls.build.name)
        source = Path(__file__).resolve().parents[1]
        cls.binary, cls.guard = root / 'controller', root / 'guard'
        flags = ['cc', '-D_POSIX_C_SOURCE=200809L', '-std=c11', '-O2', '-Wall', '-Wextra',
                 '-Werror', '-Wno-unused-function', '-Wno-misleading-indentation', '-D_FORTIFY_SOURCE=3']
        subprocess.run(flags + ['-DTTT_LEASE_LIFETIME_MS=10000',
                       str(source / 'tests/native_navigation_guard_harness.c'), '-o', str(cls.guard)],
                       check=True, capture_output=True)
        subprocess.run(flags + ['-I', str(source / 'native'), '-Dsocket=ttt_test_socket',
                       '-Dbind=ttt_test_bind', '-Daccept=ttt_test_accept', '-Dsetsockopt=ttt_test_setsockopt',
                       '-c', str(source / 'native/resident/lease/controller.c'), '-o', str(root / 'controller.o')],
                       check=True, capture_output=True)
        subprocess.run(flags + [str(root / 'controller.o'), str(source / 'tests/native_lease_controller_transport.c'),
                       '-ldl', '-o', str(cls.binary)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls): cls.build.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ('application', 'automation'): (self.root / name).write_text('0')
        self.process = subprocess.Popen([str(self.binary), 'serve', str(self.guard)], cwd=self.root,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        start_new_session=True)
        self.addCleanup(self.finish)
        self.enabled = json.loads(self.process.stdout.readline())
        self.listening = json.loads(self.process.stdout.readline())
        self.assertEqual(self.listening['status'], 'listening')
        self.owner = f"{self.listening['pid']} {self.listening['boot_uuid']}"

    def finish(self):
        if self.process.poll() is None: self.process.kill()
        self.process.communicate(timeout=4)
        end = time.monotonic() + 2
        while time.monotonic() < end and self.observed() != ('0', '0'): time.sleep(.01)

    def observed(self):
        return tuple((self.root / name).read_text() for name in ('application', 'automation'))

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); sock.settimeout(4)
        sock.connect(str(self.root / 'controller.sock'))
        return sock

    def send(self, command, *, split=False):
        with self.connect() as sock:
            data = command.encode('ascii') + b'\n'
            if split:
                for byte in data:
                    sock.sendall(bytes([byte])); time.sleep(.001)
            else: sock.sendall(data)
            response = b''
            while not response.endswith(b'\n'):
                chunk = sock.recv(4096)
                if not chunk: raise AssertionError('Missing controller response')
                response += chunk
            return json.loads(response)

    def test_fragmented_owned_transitions_and_health_use_fresh_guard_state(self):
        self.assertTrue(self.send('health')['active'])
        paused = self.send('pause ' + self.owner, split=True)
        self.assertEqual(paused['status'], 'scope'); self.assertFalse(paused['active'])
        self.assertEqual(self.observed(), ('0', '0'))
        self.assertFalse(self.send('health', split=True)['active'])
        active = self.send('resume ' + self.owner, split=True)
        self.assertTrue(active['active']); self.assertEqual(self.observed(), ('1', '1'))
        self.assertEqual(self.send('stop ' + self.owner)['status'], 'stopping')
        closed = json.loads(self.process.stdout.readline())
        self.process.communicate(timeout=3)
        self.assertEqual(self.process.returncode, 0); self.assertTrue(closed['restored'])
        self.assertEqual(self.observed(), ('0', '0'))

    def test_wrong_owner_and_invalid_commands_never_mutate_or_stop_owned_lease(self):
        writes = (self.root / 'writes').read_bytes()
        for command in ('pause', 'resume 999 ' + self.listening['boot_uuid'],
                        'pause ' + self.owner.replace('12345678', '87654321'),
                        'stop 999 ' + self.listening['boot_uuid'], 'health\r', 'x' * 127):
            self.assertEqual(self.send(command)['status'], 'rejected')
            self.assertEqual((self.root / 'writes').read_bytes(), writes)
        self.assertTrue(self.send('health')['active'])

    def test_duplicate_pause_is_terminal_with_verified_restoration(self):
        self.send('pause ' + self.owner)
        writes = (self.root / 'writes').read_bytes()
        failed = self.send('pause ' + self.owner)
        self.assertEqual(failed['status'], 'failed'); self.assertEqual(failed['guard_status'], 6)
        closed = json.loads(self.process.stdout.readline())
        self.process.communicate(timeout=3)
        self.assertEqual(self.process.returncode, 14)
        self.assertTrue(closed['restored']); self.assertTrue(closed['guard_failed'])
        self.assertEqual((self.root / 'writes').read_bytes(), writes)

    def test_stop_signal_during_fragmented_request_cannot_be_cleared_by_health(self):
        with self.connect() as sock:
            sock.sendall(b'hea'); time.sleep(.03)
            os.kill(self.process.pid, signal.SIGTERM)
            sock.sendall(b'lth\n')
        closed = json.loads(self.process.stdout.readline())
        self.process.communicate(timeout=3)
        self.assertTrue(closed['restored']); self.assertEqual(self.process.returncode, 0)


if __name__ == '__main__': unittest.main()
