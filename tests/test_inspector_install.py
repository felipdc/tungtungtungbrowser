import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest

from tungtungtungbrowser.guest import GuestError


SPEC = importlib.util.spec_from_file_location('install_inspector_bridge',
    Path(__file__).resolve().parents[1] / 'scripts/install_inspector_bridge.py')
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class DeploymentGuest:
    """Model persistent files and the native no-overwrite rename contract."""
    def __init__(self):
        self.files = {}
        self.calls = []
        self.running = None
        self.next_pid = 100
        self.corrupt_read = False

    def health(self):
        if self.running is None:
            raise ConnectionRefusedError()
        return {'protocol': 2, 'status': 'ready', 'port': 1343,
                'pid': self.running['pid']}

    def _rpc(self, method, **params):
        self.calls.append(method)
        if method == 'files.write':
            data = base64.b64decode(params['content'])
            self.files[params['path']] = data
            return {'bytes': len(data)}
        if method == 'files.read':
            data = self.files[params['path']]
            return {'truncated': False, 'size': len(data),
                    'content': base64.b64encode(b'corrupt' if self.corrupt_read else data).decode()}
        if method == 'files.chmod':
            return {}
        if method == 'files.rename':
            source, destination = params['from'], params['to']
            if destination in self.files:
                raise GuestError('destination exists')
            self.files[destination] = self.files.pop(source)
            return {'source': source, 'destination': destination}
        if method == 'services.load':
            service = plistlib.loads(self.files[params['paths'][0]])
            self.next_pid += 1
            self.running = {'label': service['Label'], 'running': True,
                            'program': service['ProgramArguments'][0], 'pid': self.next_pid}
            return {'services': [self.running]}
        raise AssertionError(method)


class InspectorInstallTests(unittest.TestCase):
    def test_failed_health_preserves_preexec_state_and_missing_log_without_restart(self):
        class StartingGuest(DeploymentGuest):
            def health(self):
                if self.running is None:
                    raise ConnectionRefusedError()
                raise TimeoutError('Bridge port never became ready')

            def _rpc(self, method, **params):
                if method in ('services.status', 'services.print'):
                    self.calls.append(method)
                    self.last_diagnostic_label = params['label']
                    if method == 'services.status':
                        return self.running.copy()
                    return {'description': 'state = xpcproxy\nexecs = 0\ninitialized = 0'}
                if method == 'files.read' and params['path'].endswith('.log'):
                    self.calls.append(method)
                    self.log_limit = params['limit']
                    raise FileNotFoundError('Log has not been opened')
                return super()._rpc(method, **params)

        guest = StartingGuest()
        binary = b'native bridge fixture'
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'deployment'
            with self.assertRaisesRegex(TimeoutError, 'port never became ready'):
                installer.install(guest, binary, hashlib.sha256(binary).hexdigest(),
                                  output, guest.health)
            report = json.loads((output / 'deploy.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['error'], 'Bridge port never became ready')
        evidence = report['failure_diagnostics']
        self.assertTrue(evidence['identity_verified'])
        self.assertIn('state = xpcproxy', evidence['launchd']['description'])
        self.assertIn('FileNotFoundError', evidence['log_error'])
        self.assertEqual(guest.last_diagnostic_label, report['label'])
        self.assertEqual(guest.log_limit, 16384)
        self.assertEqual(guest.calls.count('services.load'), 1)
        self.assertFalse(set(guest.calls) & {'services.start', 'services.stop', 'services.kickstart'})

    def test_diagnostic_failure_or_identity_change_preserves_original_error(self):
        for fail_status in (False, True):
            with self.subTest(fail_status=fail_status):
                class ChangedGuest(DeploymentGuest):
                    def health(self):
                        if self.running is None:
                            raise ConnectionRefusedError()
                        raise TimeoutError('Original health failure')

                    def _rpc(self, method, **params):
                        if method == 'services.status':
                            self.calls.append(method)
                            if fail_status:
                                raise GuestError('Cannot inspect service')
                            return self.running | {'program': '/another/process'}
                        return super()._rpc(method, **params)

                guest = ChangedGuest()
                binary = b'native bridge fixture'
                with tempfile.TemporaryDirectory() as directory:
                    output = Path(directory) / 'deployment'
                    with self.assertRaisesRegex(TimeoutError, 'Original health failure'):
                        installer.install(guest, binary, hashlib.sha256(binary).hexdigest(),
                                          output, guest.health)
                    report = json.loads((output / 'deploy.json').read_text())
                self.assertEqual(report['error'], 'Original health failure')
                self.assertNotIn('services.print', guest.calls)
                self.assertNotIn('log', report['failure_diagnostics'])

    def test_redeploy_same_binary_preserves_files_left_by_stopped_deployments(self):
        guest = DeploymentGuest()
        binary = b'native bridge fixture'
        digest = hashlib.sha256(binary).hexdigest()
        old_path = '/private/var/root/ttt-inspector-bridge-v1-' + digest
        guest.files[old_path] = binary
        with tempfile.TemporaryDirectory() as directory:
            first = installer.install(guest, binary, digest, Path(directory) / 'first', guest.health)
            previous_files = guest.files.copy()
            guest.running = None  # Guest reboot retains files but stops the service.
            second = installer.install(guest, binary, digest, Path(directory) / 'second', guest.health)
        self.assertEqual(first['status'], 'complete')
        self.assertEqual(second['status'], 'complete')
        self.assertNotEqual(first['path'], second['path'])
        self.assertNotEqual(first['label'], second['label'])
        self.assertNotEqual(first['path'], old_path)
        self.assertEqual(guest.files[second['path']], binary)
        for path, contents in previous_files.items():
            self.assertEqual(guest.files[path], contents)

    def test_running_bridge_prevents_any_guest_mutation(self):
        guest = DeploymentGuest()
        guest.running = {'pid': 100}
        binary = b'native bridge fixture'
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(GuestError, 'already running'):
                installer.install(guest, binary, hashlib.sha256(binary).hexdigest(),
                                  Path(directory) / 'deployment', guest.health)
        self.assertEqual(guest.calls, [])

    def test_bad_upload_readback_never_loads_service(self):
        guest = DeploymentGuest()
        guest.corrupt_read = True
        binary = b'native bridge fixture'
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(GuestError, 'readback'):
                installer.install(guest, binary, hashlib.sha256(binary).hexdigest(),
                                  Path(directory) / 'deployment', guest.health)
        self.assertEqual(guest.calls, ['files.write', 'files.read'])
        self.assertIsNone(guest.running)


if __name__ == '__main__':
    unittest.main()
