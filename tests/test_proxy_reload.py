import copy
import io
import fcntl
import json
from pathlib import Path
import signal
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import reload_proxy_session as reload


class ProxyReloadTest(unittest.TestCase):
    def setUp(self):
        self.configuration = {
            'dns': {'servers': [{'detour': 'upstream'}]},
            'route': {'rules': [{'network': 'udp', 'action': 'reject'}]},
            'inbounds': [{'type': 'tun', 'strict_route': True}],
            'outbounds': [{'tag': 'upstream', 'type': 'http', 'server': '192.0.2.10',
                           'server_port': 443, 'username': 'old-user', 'password': 'old-secret',
                           'routing_mark': 21588, 'bind_interface': 'eth0',
                           'tls': {'enabled': True, 'server_name': 'proxy.example.net'}}]}
        self.secret = {'scheme': 'https', 'host': 'proxy.example.net', 'address': '192.0.2.10',
                       'port': 443, 'username': 'new-user', 'password': 'new-private-secret'}
        self.identity = {'pid': 1, 'start_ticks': '100', 'boot_id': 'test-boot',
                         'network_namespace': 'net:[100]'}

    def test_only_credentials_change_and_original_is_preserved(self):
        original = copy.deepcopy(self.configuration)
        changed = reload.replace_credentials(self.configuration, self.secret)
        self.assertEqual(self.configuration, original)
        expected = copy.deepcopy(original)
        expected['outbounds'][0].update(username='new-user', password='new-private-secret')
        self.assertEqual(changed, expected)

    def test_browser_relay_reload_preserves_listener_and_direct_device_routing(self):
        original = {'log': {'level': 'warn'},
                    'inbounds': [{'type': 'http', 'tag': 'safari', 'listen': '127.0.0.1', 'listen_port': 1080}],
                    'outbounds': [copy.deepcopy(self.configuration['outbounds'][0])],
                    'route': {'final': 'upstream'}}
        changed = reload.replace_credentials(original, self.secret)
        expected = copy.deepcopy(original)
        expected['outbounds'][0].update(username='new-user', password='new-private-secret')
        self.assertEqual(changed, expected)
        self.assertNotIn('dns', changed)
        self.assertNotIn('tun', json.dumps(changed))

    def test_cli_selects_only_the_requested_existing_router_configuration(self):
        for flags, config in [([], reload.CONFIG), (['--scope', 'browser'], reload.BROWSER_CONFIG)]:
            with self.subTest(flags=flags), \
                    patch.object(sys, 'argv', ['reload', '--output', '/run/test-reload', *flags]), \
                    patch.object(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(self.secret).encode()))), \
                    patch.object(reload, 'reload_session', return_value={'status': 'reload_requested'}) as called, \
                    patch('builtins.print'):
                self.assertEqual(reload.main(), 0)
                called.assert_called_once_with(self.secret, Path('/run/test-reload'), config=config)

    def test_gateway_protocol_and_tls_changes_rejected(self):
        for key, value in [('address', '192.0.2.11'), ('port', 8443), ('port', True),
                           ('host', 'another.example.net'), ('scheme', 'http'),
                           ('scheme', 'socks5'), ('password', 'secret\nheader')]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                reload.replace_credentials(self.configuration, self.secret | {key: value})
        multiple = copy.deepcopy(self.configuration)
        multiple['outbounds'].append({'type': 'direct'})
        with self.assertRaises(ValueError):
            reload.replace_credentials(multiple, self.secret)

    def test_bounded_unique_json_object(self):
        for data in [b'[]', b'{"x":1,"x":2}', b'{"x":NaN}', b'x' * (reload.MAX_CONFIG + 1)]:
            with self.subTest(data=data[:20]), self.assertRaises(ValueError):
                reload.read_json(data)

    def test_evomi_rotation_preserves_gateway_targeting_and_input(self):
        original = self.secret | {'password': 'private_country-BR_lockedsession-1234567890_region-test',
                                  'session': '1234567890'}
        before = copy.deepcopy(original)
        candidate = reload.new_evomi_locked_session(original, token='abcdef1234')
        self.assertEqual(original, before)
        self.assertEqual(candidate, original | {
            'password': 'private_country-BR_lockedsession-abcdef1234_region-test', 'session': 'abcdef1234'})

    def test_evomi_rotation_rejects_ambiguous_modes_and_invalid_tokens(self):
        for password in ('private', 'private_session-1234567890',
                         'private_lockedsession-1234567890_lockedsession-abcdefghij',
                         'private_lockedsession-short', 'private_lockedsession-12345678901',
                         'private_lockedsession-1234567890_lifetime-30'):
            with self.subTest(password=password), self.assertRaises(ValueError):
                reload.new_evomi_locked_session(self.secret | {'password': password}, token='abcdef1234')
        original = self.secret | {'password': 'private_lockedsession-1234567890'}
        for token in ('1234567890', 'short', 'abcdefghijklmnop', 'abcd_12345'):
            with self.subTest(token=token), self.assertRaises(ValueError):
                reload.new_evomi_locked_session(original, token=token)

    def test_generated_evomi_session_uses_supported_length(self):
        with patch.object(reload.secrets, 'token_hex', return_value='abcdef1234') as random:
            candidate = reload.new_evomi_locked_session(self.secret | {'password': 'private_lockedsession-1234567890'})
        random.assert_called_once_with(5)
        self.assertTrue(candidate['password'].endswith('_lockedsession-abcdef1234'))

    def test_cli_rotation_passes_new_credentials_to_browser_reload_without_printing_them(self):
        original = self.secret | {'password': 'private_lockedsession-1234567890'}
        with patch.object(sys, 'argv', ['reload', '--output', '/run/reset', '--scope', 'browser', '--rotate-evomi-session']), \
                patch.object(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(original).encode()))), \
                patch.object(reload.secrets, 'token_hex', return_value='abcdef1234'), \
                patch.object(reload, 'reload_session', return_value={'status': 'reload_requested'}) as called, \
                patch('builtins.print') as printed:
            self.assertEqual(reload.main(), 0)
        self.assertEqual(called.call_args.args[0]['password'], 'private_lockedsession-abcdef1234')
        self.assertEqual(called.call_args.kwargs['config'], reload.BROWSER_CONFIG)
        self.assertNotIn('private_lockedsession', str(printed.call_args_list))

    def invoke(self, directory, *, check_exit=0, identities=None, signal_error=None):
        config = directory / 'router.json'
        original = json.dumps(self.configuration).encode()
        config.write_bytes(original)
        with patch.object(reload, 'process_identity', side_effect=identities or [self.identity] * 2), \
                patch.object(reload.os, 'pidfd_open', return_value=999) as opened, \
                patch.object(reload.os, 'close') as closed, \
                patch.object(reload.subprocess, 'run') as check, \
                patch.object(reload.signal, 'pidfd_send_signal', side_effect=signal_error) as sent:
            check.return_value.returncode = check_exit
            result = reload.reload_session(self.secret, directory / 'evidence', config=config)
            calls = sent.call_args_list
            closed.assert_called_once_with(999)
            opened.assert_called_once_with(1)
        self.assertNotIn('new-private-secret', json.dumps(result))
        self.assertNotIn('old-secret', (directory / 'evidence/report.json').read_text())
        return result, config, original, calls

    def test_verified_reload_signals_exact_handle_once_without_claiming_connectivity(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            result, config, original, calls = self.invoke(directory)
            self.assertEqual(result['status'], 'reload_requested')
            self.assertEqual(result['reload_requests'], 1)
            self.assertTrue(result['configuration_replaced'])
            self.assertFalse(result['upstream_verified'])
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].args, (999, signal.SIGHUP))
            self.assertEqual(json.loads(config.read_bytes())['outbounds'][0]['password'], 'new-private-secret')
            self.assertEqual((directory / 'evidence/previous-config.json').read_bytes(), original)
            for path in [config, directory / 'evidence/report.json', directory / 'evidence/previous-config.json']:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_bad_candidate_does_not_replace_or_signal(self):
        with tempfile.TemporaryDirectory() as temp:
            result, config, original, calls = self.invoke(Path(temp), check_exit=1)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(config.read_bytes(), original)
            self.assertEqual(result['reload_requests'], 0)
            self.assertFalse(result['configuration_replaced'])
            self.assertEqual(calls, [])

    def test_changed_process_rejects_before_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            identities = [self.identity, self.identity | {'start_ticks': '101'}]
            result, config, original, calls = self.invoke(Path(temp), identities=identities)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(config.read_bytes(), original)
            self.assertEqual(calls, [])

    def test_signal_failure_preserves_mutation_and_never_retries(self):
        with tempfile.TemporaryDirectory() as temp:
            result, config, original, calls = self.invoke(Path(temp), signal_error=PermissionError('private-error'))
            self.assertEqual(result['status'], 'failed')
            self.assertTrue(result['configuration_replaced'])
            self.assertEqual(result['reload_requests'], 1)
            self.assertEqual(result['failure_type'], 'PermissionError')
            self.assertNotIn('private-error', json.dumps(result))
            self.assertNotEqual(config.read_bytes(), original)
            self.assertEqual(len(calls), 1)

    def test_wrong_pid_one_is_rejected_before_reading_or_mutating_config(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            with patch.object(reload, 'process_identity', side_effect=ValueError('wrong process')), \
                    patch.object(reload.os, 'pidfd_open') as opened, \
                    patch.object(reload.signal, 'pidfd_send_signal') as sent:
                result = reload.reload_session(self.secret, directory / 'out', config=directory / 'missing')
            self.assertEqual(result['status'], 'failed')
            opened.assert_not_called()
            sent.assert_not_called()

    def test_previous_evidence_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            output = directory / 'out'
            output.mkdir()
            (output / 'report.json').write_text('original failure')
            with patch.object(reload, 'process_identity') as identity, self.assertRaises(FileExistsError):
                reload.reload_session(self.secret, output)
            identity.assert_not_called()
            self.assertEqual((output / 'report.json').read_text(), 'original failure')

    def test_concurrent_reload_is_rejected_before_process_or_configuration_access(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            config = directory / 'router.json'
            with (directory / 'router.json.reload.lock').open('w+b') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(reload, 'process_identity') as identity:
                    result = reload.reload_session(self.secret, directory / 'out', config=config)
                identity.assert_not_called()
            self.assertEqual(result['failure_type'], 'BlockingIOError')
            self.assertEqual(result['reload_requests'], 0)
            self.assertFalse(config.exists())


if __name__ == '__main__':
    unittest.main()
