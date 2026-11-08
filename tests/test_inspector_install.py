import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from tungtungtungbrowser.guest import GuestError, GuestHTTPError, GuestTransportError


SPEC = importlib.util.spec_from_file_location('install_inspector_bridge',
    Path(__file__).resolve().parents[1] / 'scripts/install_inspector_bridge.py')
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InspectorHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_reset_is_a_refusal_before_any_control_request(self):
        reset = ConnectionResetError(104, 'Connection reset by peer')
        with patch.object(installer, 'vsock_connection', AsyncMock(side_effect=reset)), \
                patch.object(installer.InspectorTransport, '_control', AsyncMock()) as control:
            with self.assertRaises(ConnectionRefusedError) as caught:
                await installer.health(3)
        self.assertIs(caught.exception.__cause__, reset)
        control.assert_not_awaited()

    async def test_connected_reset_is_not_an_absent_listener(self):
        reset = ConnectionResetError(104, 'Reset while reading health')
        transport = installer.InspectorTransport(cid=3)
        with patch.object(installer, 'InspectorTransport', return_value=transport), \
                patch.object(installer, 'vsock_connection', AsyncMock(return_value=object())), \
                patch.object(installer.asyncio, 'open_connection', AsyncMock(return_value=(object(), object()))), \
                patch.object(transport, '_control', AsyncMock(side_effect=reset)), \
                patch.object(transport, 'close', AsyncMock()) as close:
            with self.assertRaises(ConnectionResetError) as caught:
                await installer.health(3)
        self.assertIs(caught.exception, reset)
        close.assert_awaited_once()

    async def test_other_connect_failures_remain_errors(self):
        for error in (TimeoutError('connect timed out'), PermissionError('denied'),
                      OSError(101, 'network unreachable')):
            with self.subTest(error=error), \
                    patch.object(installer, 'vsock_connection', AsyncMock(side_effect=error)):
                with self.assertRaises(type(error)) as caught:
                    await installer.health(3)
                self.assertIs(caught.exception, error)


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
            contents = b'corrupt' if self.corrupt_read else data
            binary = params.get('binary', False)
            return {'truncated': False, 'size': len(data), 'path': params['path'],
                    'encoding': 'base64' if binary else 'utf8',
                    'content': base64.b64encode(contents).decode() if binary else contents.decode()}
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
    def test_cli_allows_daemon_timeout_reply_and_preserves_it_without_relaunch(self):
        class DeadlineGuest(ProcessGuest):
            def __init__(self, *, cid, timeout):
                super().__init__()
                self.cid = cid
                self.http_timeout = timeout

            def _rpc(self, method, **params):
                if method != 'script.eval':
                    return super()._rpc(method, **params)
                self.calls.append(method)
                # The daemon's terminal error follows its script deadline and
                # process cleanup. An equal HTTP timeout loses that response.
                if self.http_timeout <= params['timeout'] + 1:
                    raise TimeoutError('HTTP deadline expired before daemon cleanup')
                raise GuestHTTPError(400, installer.SCRIPT_TIMEOUT_MESSAGE)

        binary = b'native bridge fixture'
        guests = []

        def make_guest(**kwargs):
            guest = DeadlineGuest(**kwargs)
            guests.append(guest)
            return guest

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'deployment'
            argv = ['install_inspector_bridge.py', directory, '--output', str(output),
                    '--cid', '7', '--launch-mode', 'process']
            with patch.object(installer.sys, 'argv', argv), \
                    patch.object(installer, 'read_build', return_value=(
                        binary, hashlib.sha256(binary).hexdigest())), \
                    patch.object(installer, 'Guest', side_effect=make_guest), \
                    patch.object(installer, 'health', AsyncMock(side_effect=ConnectionRefusedError)), \
                    patch.object(installer.argparse.ArgumentParser, 'exit', side_effect=SystemExit) as exit:
                with self.assertRaises(SystemExit):
                    installer.main()
            report = json.loads((output / 'deploy.json').read_text())
        self.assertEqual(len(guests), 1)
        self.assertEqual(guests[0].cid, 7)
        self.assertEqual(guests[0].calls.count('script.eval'), 1)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('within 30 seconds and was stopped', report['error'])
        self.assertIn(report['error'], exit.call_args.args[1])
        self.assertTrue(report['process_spawn_requested'])
        self.assertIn('failure_diagnostics', report)

    def test_process_launch_verifies_pid_and_boot_without_loading_a_service(self):
        guest = ProcessGuest()
        binary = b'native bridge fixture'
        with tempfile.TemporaryDirectory() as directory:
            result = installer.install(guest, binary, hashlib.sha256(binary).hexdigest(),
                                       Path(directory) / 'deployment', guest.health,
                                       launch_mode='process')
            self.assertFalse((Path(directory) / 'deployment/service.plist').exists())
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['health']['pid'], result['spawn']['pid'])
        self.assertEqual(result['health']['boot_uuid'], result['spawn']['boot_uuid'])
        self.assertEqual(guest.calls.count('script.eval'), 1)
        self.assertFalse(any(name.startswith('services.') for name in guest.calls))
        self.assertIn(json.dumps(["spawn", result['log']]), guest.source)

    def test_terminal_launcher_timeout_reconciles_the_existing_child_without_another_spawn(self):
        guest = TerminalLaunchGuest()
        binary = b'native bridge fixture'
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'deployment'
            result = installer.install(guest, binary, hashlib.sha256(binary).hexdigest(),
                                       output, guest.health, launch_mode='process')
            self.assertEqual(result, json.loads((output / 'deploy.json').read_text()))
            self.assertFalse((output / 'service.plist').exists())
        self.assertEqual(result['status'], 'complete')
        self.assertNotIn('spawn', result)  # No acknowledgement was received.
        self.assertEqual(result['launch_error'], str(guest.launch_error))
        self.assertEqual(result['reconciliation']['status'], 'verified')
        self.assertFalse(result['reconciliation']['launch_repeated'])
        self.assertEqual(result['health']['pid'], result['reconciliation']['listener']['pid'])
        self.assertEqual(result['health']['boot_uuid'], result['reconciliation']['listener']['boot_uuid'])
        self.assertEqual(guest.calls.count('script.eval'), 1)
        self.assertEqual(guest.calls.count('files.write'), 1)
        self.assertEqual(guest.health_calls, 2)
        self.assertFalse(any(name.startswith('services.') for name in guest.calls))

    def test_reconciliation_rejects_incomplete_or_changed_files_and_preserves_launcher_error(self):
        failures = ('missing_log', 'truncated_log', 'wrong_log_path', 'wrong_encoding',
                    'wrong_log_size', 'invalid_json', 'multiple_records', 'invalid_pid',
                    'invalid_boot', 'wrong_port', 'wrong_status', 'changed_binary',
                    'wrong_binary_path', 'truncated_binary')
        for failure in failures:
            with self.subTest(failure=failure):
                guest = TerminalLaunchGuest(failure)
                binary = b'native bridge fixture'
                with tempfile.TemporaryDirectory() as directory:
                    output = Path(directory) / 'deployment'
                    with self.assertRaises(GuestHTTPError) as caught:
                        installer.install(guest, binary, hashlib.sha256(binary).hexdigest(),
                                          output, guest.health, launch_mode='process')
                    report = json.loads((output / 'deploy.json').read_text())
                self.assertIs(caught.exception, guest.launch_error)
                self.assertIsNotNone(caught.exception.__cause__)
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['error'], str(guest.launch_error))
                self.assertEqual(report['reconciliation']['status'], 'failed')
                self.assertEqual(guest.health_calls, 1)  # Initial absent-listener probe only.
                self.assertEqual(guest.calls.count('script.eval'), 1)
                self.assertEqual(guest.calls.count('files.write'), 1)

    def test_reconciliation_rejects_a_replaced_or_unreachable_listener(self):
        for failure in ('wrong_pid', 'wrong_boot', 'health_timeout'):
            with self.subTest(failure=failure):
                guest = TerminalLaunchGuest(failure)
                binary = b'native bridge fixture'
                with tempfile.TemporaryDirectory() as directory:
                    output = Path(directory) / 'deployment'
                    with self.assertRaises(GuestHTTPError) as caught:
                        installer.install(guest, binary, hashlib.sha256(binary).hexdigest(),
                                          output, guest.health, launch_mode='process')
                    report = json.loads((output / 'deploy.json').read_text())
                self.assertIs(caught.exception, guest.launch_error)
                self.assertEqual(report['reconciliation']['status'], 'failed')
                self.assertEqual(report['error'], str(guest.launch_error))
                self.assertEqual(guest.health_calls, 2)
                self.assertEqual(guest.calls.count('script.eval'), 1)

    def test_only_the_received_terminal_script_timeout_permits_reconciliation(self):
        errors = (GuestTransportError('receive_headers', TimeoutError('lost response')),
                  TimeoutError('caller timed out'), GuestError(str(GuestHTTPError(400, installer.SCRIPT_TIMEOUT_MESSAGE))),
                  GuestHTTPError(500, installer.SCRIPT_TIMEOUT_MESSAGE),
                  GuestHTTPError(400, 'A different native failure'))
        for error in errors:
            with self.subTest(error=type(error).__name__):
                guest = TerminalLaunchGuest(launch_error=error)
                binary = b'native bridge fixture'
                with tempfile.TemporaryDirectory() as directory:
                    output = Path(directory) / 'deployment'
                    with self.assertRaises(type(error)) as caught:
                        installer.install(guest, binary, hashlib.sha256(binary).hexdigest(),
                                          output, guest.health, launch_mode='process')
                    report = json.loads((output / 'deploy.json').read_text())
                self.assertIs(caught.exception, error)
                self.assertNotIn('reconciliation', report)
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(guest.health_calls, 1)
                self.assertEqual(guest.calls.count('script.eval'), 1)

    def test_process_spawn_failure_or_lost_reply_is_never_replayed(self):
        for failure in ('lost_reply', 'exit', 'invalid_pid', 'invalid_boot', 'wrong_pid', 'wrong_boot'):
            with self.subTest(failure=failure):
                guest = ProcessGuest(failure)
                binary = b'native bridge fixture'
                with tempfile.TemporaryDirectory() as directory:
                    output = Path(directory) / 'deployment'
                    with self.assertRaises((GuestError, TimeoutError)):
                        installer.install(guest, binary, hashlib.sha256(binary).hexdigest(),
                                          output, guest.health, launch_mode='process')
                    result = json.loads((output / 'deploy.json').read_text())
                self.assertEqual(result['status'], 'failed')
                self.assertTrue(result['process_spawn_requested'])
                self.assertIn('failure_diagnostics', result)
                self.assertEqual(guest.calls.count('script.eval'), 1)
                self.assertFalse(any(name.startswith('services.') for name in guest.calls))

    def test_invalid_launch_mode_does_not_create_output_or_call_guest(self):
        guest = ProcessGuest()
        binary = b'native bridge fixture'
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'deployment'
            with self.assertRaises(ValueError):
                installer.install(guest, binary, hashlib.sha256(binary).hexdigest(),
                                  output, guest.health, launch_mode='fallback')
            self.assertFalse(output.exists())
        self.assertEqual(guest.calls, [])

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


class ProcessGuest(DeploymentGuest):
    BOOT = 'F0000000-0000-4000-8000-000000000000'

    def __init__(self, failure=None):
        super().__init__()
        self.failure = failure

    def health(self):
        result = super().health() | {'boot_uuid': self.BOOT}
        if self.failure == 'wrong_pid':
            result['pid'] += 1
        if self.failure == 'wrong_boot':
            result['boot_uuid'] = 'F0000000-0000-4000-8000-000000000001'
        return result

    def _rpc(self, method, **params):
        if method != 'script.eval':
            return super()._rpc(method, **params)
        self.calls.append(method)
        self.source = params['source']
        self.running = {'pid': 123}
        if self.failure == 'lost_reply':
            raise TimeoutError('Administrative reply lost after dispatch')
        spawned = {'protocol': 2, 'status': 'spawned', 'pid': 123, 'boot_uuid': self.BOOT,
                   'port': 1343, 'spawn_error': 0}
        if self.failure == 'invalid_pid':
            spawned['pid'] = True
        if self.failure == 'invalid_boot':
            spawned['boot_uuid'] = 'not-a-boot'
        process = {'status': 9 if self.failure == 'exit' else 0, 'signal': None,
                   'stdout': json.dumps(spawned), 'stderr': ''}
        return {'ok': True, 'crashed': False, 'output': '', 'result': json.dumps(process)}


class TerminalLaunchGuest(ProcessGuest):
    """A real child and exclusive log can outlive a failed launcher response."""
    def __init__(self, failure=None, *, launch_error=None):
        super().__init__(failure)
        self.launch_error = launch_error or GuestHTTPError(400, installer.SCRIPT_TIMEOUT_MESSAGE)
        self.health_calls = 0
        self.launched = False

    def health(self):
        self.health_calls += 1
        if self.launched and self.failure == 'health_timeout':
            raise TimeoutError('Listener health timed out')
        return super().health()

    def _rpc(self, method, **params):
        if method == 'script.eval':
            # Execute the modeled native spawn once, then lose only its result.
            super()._rpc(method, **params)
            self.launched = True
            decoder = json.JSONDecoder()
            executable, end = decoder.raw_decode(params['source'][len('proc.run('):])
            arguments = decoder.raw_decode(params['source'][len('proc.run(') + end + 2:])[0]
            self.log = arguments[1]
            listener = {'protocol': 2, 'status': 'listening', 'pid': 123,
                        'boot_uuid': self.BOOT, 'port': 1343}
            for failure, key, value in [('invalid_pid', 'pid', True), ('invalid_boot', 'boot_uuid', 'invalid'),
                                        ('wrong_port', 'port', 1344), ('wrong_status', 'status', 'spawned')]:
                if self.failure == failure:
                    listener[key] = value
            content = json.dumps(listener) + '\n'
            if self.failure == 'invalid_json':
                content = '{unfinished'
            if self.failure == 'multiple_records':
                content += content
            if self.failure != 'missing_log':
                self.files[self.log] = content.encode()
            if self.failure == 'changed_binary':
                self.files[executable] = b'changed'
            raise self.launch_error
        result = super()._rpc(method, **params)
        if method == 'files.read' and self.launched:
            is_log = params['path'] == self.log
            if is_log:
                if self.failure == 'truncated_log':
                    result['truncated'] = True
                elif self.failure == 'wrong_log_path':
                    result['path'] = '/another/launch.log'
                elif self.failure == 'wrong_encoding':
                    result['encoding'] = 'base64'
                elif self.failure == 'wrong_log_size':
                    result['size'] += 1
            elif self.failure == 'wrong_binary_path':
                result['path'] = '/another/executable'
            elif self.failure == 'truncated_binary':
                result['truncated'] = True
        return result


if __name__ == '__main__':
    unittest.main()
