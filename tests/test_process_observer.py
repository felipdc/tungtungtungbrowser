import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from tungtungtungbrowser.guest import GuestTransportError
from tungtungtungbrowser.process_observer import ProcessObserver, ProcessObserverError, process_identity

ROOT = Path(__file__).resolve().parents[1]
BOOT = '12345678-1234-1234-1234-123456789ABC'
SPEC = importlib.util.spec_from_file_location('install_process_observer', ROOT / 'scripts/install_process_observer.py')
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def observed(pid=42, executable='/Applications/MobileSafari.app/MobileSafari'):
    return {'protocol': 1, 'status': 'observed', 'stage': 'complete', 'requested_pid': pid,
            'pid': pid, 'start_seconds': 100, 'start_microseconds': 123,
            'kernel_state': 3, 'kernel_flags': 0, 'elapsed_nanoseconds': 10, 'errno': 0,
            'boot_uuid': BOOT, 'foreground_verified': False, 'executable': executable}


def healthy():
    return {'protocol': 1, 'status': 'ready', 'pid': 42, 'boot_uuid': BOOT, 'port': 1342, 'read_only': True}


def wire_json(value):
    return json.dumps(value).encode() + b'\n'


class Wire:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.sent = []
        self.closed = False

    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True
    def settimeout(self, value): self.timeout = value
    def connect(self, address): self.address = address
    def sendall(self, value): self.sent.append(value)

    def recv(self, n):
        chunk = next(self.chunks, b'')
        if isinstance(chunk, Exception): raise chunk
        return chunk


class ProcessObserverTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'C compiler is required')
    def test_native_identity_races_kernel_errors_zombies_and_pid_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = str(Path(directory) / 'check')
            subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                            '-Wno-misleading-indentation', str(ROOT / 'native/check_process_observer.c'),
                            '-o', executable], check=True, capture_output=True, text=True)
            subprocess.run([executable], check=True, capture_output=True, text=True, timeout=5)

    def test_fragmented_health_and_socket_closed(self):
        data = wire_json(healthy())
        wire = Wire([data[:10], data[10:], b''])
        result = ProcessObserver(cid=7, connection_factory=lambda: wire).health()
        self.assertEqual(result, healthy())
        self.assertEqual(wire.address, (7, 1342))
        self.assertEqual(wire.sent, [b'health\n'])
        self.assertTrue(wire.closed)

    def test_malformed_frames_preserve_bytes_and_do_not_repeat(self):
        for data in (b'{}', b'{}\n{}\n', b'{"protocol":1,"protocol":1}\n',
                     b'{"protocol":true}\n', b'{"protocol":1,"extra":NaN}\n', b'x' * 32769):
            wire = Wire([data])
            with self.subTest(data=data[:30]), self.assertRaises(ProcessObserverError) as caught:
                ProcessObserver(connection_factory=lambda: wire).health()
            self.assertEqual(caught.exception.partial, data)
            self.assertEqual(wire.sent, [b'health\n'])
            self.assertTrue(wire.closed)

    def test_timeout_preserves_partial_response_without_reconnection(self):
        wire = Wire([b'{"protocol":1', TimeoutError('slow')])
        factory = Mock(return_value=wire)
        with self.assertRaises(ProcessObserverError) as caught:
            ProcessObserver(connection_factory=factory).observe(42)
        self.assertIsInstance(caught.exception.__cause__, TimeoutError)
        self.assertEqual(caught.exception.partial, b'{"protocol":1')
        self.assertEqual(caught.exception.phase, 'read_response')
        factory.assert_called_once_with()
        self.assertEqual(wire.sent, [b'pid 42\n'])

    def test_invalid_arguments_send_nothing(self):
        factory = Mock()
        for pid in (0, -1, 2147483648, True, '42'):
            with self.assertRaises(ValueError): ProcessObserver(connection_factory=factory).observe(pid)
        for cid in (0, 2, True, 0xffffffff):
            with self.assertRaises(ValueError): ProcessObserver(cid=cid)
        for timeout in (0, 31, float('nan'), True):
            with self.assertRaises(ValueError): ProcessObserver(timeout=timeout)
        factory.assert_not_called()

    def test_identity_rejects_reuse_wrong_boot_executable_and_native_failure(self):
        r = observed()
        self.assertEqual(process_identity(r, pid=42, boot_uuid=BOOT)['start_microseconds'], 123)
        for key, value in [('status', 'absent'), ('protocol', True), ('pid', 43), ('pid', True),
                           ('boot_uuid', '00000000-0000-0000-0000-000000000000'),
                           ('start_microseconds', 1000000), ('kernel_flags', 4),
                           ('kernel_state', 5), ('executable', 'relative'), ('foreground_verified', True)]:
            with self.subTest(key=key), self.assertRaises(ProcessObserverError):
                process_identity(r | {key: value}, pid=42, boot_uuid=BOOT)
        with self.assertRaises(ProcessObserverError):
            process_identity(r, pid=42, boot_uuid=BOOT, executable='/different')

    def test_absent_is_retained_without_inventing_live_identity(self):
        result = observed() | {'status': 'absent', 'stage': 'identity_before', 'errno': 3, 'pid': 0,
                               'start_seconds': 0, 'start_microseconds': 0, 'kernel_state': 0,
                               'executable': ''}
        wire = Wire([wire_json(result)])
        self.assertEqual(ProcessObserver(connection_factory=lambda: wire).observe(42), result)
        with self.assertRaises(ProcessObserverError): process_identity(result, pid=42, boot_uuid=BOOT)

    def test_stop_checks_identity_and_never_retries_stop(self):
        first = Wire([wire_json(healthy())])
        second = Wire([TimeoutError('ack lost')])
        factory = Mock(side_effect=[first, second])
        with self.assertRaises(ProcessObserverError):
            ProcessObserver(connection_factory=factory).stop(pid=42, boot_uuid=BOOT)
        self.assertEqual(second.sent, [b'stop\n'])
        self.assertEqual(factory.call_count, 2)
        wire = Wire([wire_json(healthy() | {'pid': 43})])
        with self.assertRaises(ProcessObserverError):
            ProcessObserver(connection_factory=lambda: wire).stop(pid=42, boot_uuid=BOOT)
        self.assertEqual(wire.sent, [b'health\n'])


class ObserverInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.data = struct.pack('<8I', 0xFEEDFACF, 0x100000C, 0, 2, 0, 0, 0, 0)
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.files = {}
        self.calls = []
        self.fail = None
        def rpc(method, **params):
            self.calls.append((method, params))
            if method == self.fail: raise GuestTransportError('receive_headers', TimeoutError('original'))
            if method == 'files.write':
                data = base64.b64decode(params['content']);self.files[params['path']] = data
                return {'path': params['path'], 'bytes': len(data)}
            if method == 'files.read':
                data = self.files[params['path']]
                return {'path': params['path'], 'encoding': 'base64', 'size': len(data), 'truncated': False,
                        'content': base64.b64encode(data).decode()}
            if method == 'files.rename':
                self.files[params['to']] = self.files.pop(params['from'])
                return {'source': params['from'], 'destination': params['to']}
            return {}
        self.guest = Mock(_rpc=rpc)
        path = '/private/var/root/ttt-process-observer-v1-' + self.sha
        self.observer = Mock()
        self.observer.health.return_value = healthy()
        self.observer.observe.side_effect = [observed(executable=path),
            observed(2147483647) | {'status': 'absent', 'errno': 3}]

    def test_verified_deployment_and_no_ui_commands(self):
        out = self.base / 'evidence'
        report = installer.install(self.guest, self.observer, self.data, self.sha, out)
        self.assertEqual(report['status'], 'complete')
        self.assertFalse(report['native_ui_input_sent'])
        methods = [method for method, _ in self.calls]
        self.assertEqual(methods.count('services.load'), 1)
        self.assertEqual(set(methods), {'files.write', 'files.read', 'files.chmod', 'files.rename', 'services.load'})
        self.assertTrue((out / 'service.plist').exists())
        self.observer.observe.assert_any_call(2147483647)

    def test_original_load_timeout_is_preserved_without_second_load(self):
        self.fail = 'services.load'
        out = self.base / 'failed'
        with self.assertRaises(GuestTransportError):
            installer.install(self.guest, self.observer, self.data, self.sha, out)
        self.assertEqual([m for m, _ in self.calls].count('services.load'), 1)
        self.observer.health.assert_not_called()
        self.assertEqual(json.loads((out / 'deploy.json').read_text())['status'], 'failed')

    def test_build_sources_architecture_and_digest(self):
        directory = self.base / 'build';directory.mkdir()
        (directory / 'process-observer').write_bytes(self.data)
        manifest = {'protocol': 1, 'binary': 'process-observer', 'binary_sha256': self.sha,
                    'sources': {n: hashlib.sha256((ROOT / 'native' / n).read_bytes()).hexdigest() for n in installer.SOURCES}}
        path = directory / 'build.json';path.write_text(json.dumps(manifest))
        self.assertEqual(installer.read_build(directory), (self.data, self.sha))
        path.write_text(json.dumps(manifest | {'sources': {}}))
        with self.assertRaises(ValueError): installer.read_build(directory)
        path.write_text(json.dumps(manifest));(directory / 'process-observer').write_bytes(b'x' + self.data[1:])
        with self.assertRaises(ValueError): installer.read_build(directory)
