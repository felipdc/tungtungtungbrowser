import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest

from scripts.configure_safari_proxy import configure, ENDPOINT, KEYS
from tungtungtungbrowser.guest import GuestTransportError

spec = importlib.util.spec_from_file_location('browser_relay', Path(__file__).resolve().parents[1] / 'runtime/proxy/browser_entrypoint.py')
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class BrowserProxyTests(unittest.TestCase):
    def test_only_explicit_loopback_requests_can_enter_upstream_route(self):
        secret = {'address': '192.0.2.1', 'port': 443, 'scheme': 'https',
                  'host': 'proxy.example', 'username': 'user', 'password': 'secret'}
        config = relay.configuration(secret)
        self.assertEqual(config['inbounds'], [{'type': 'http', 'tag': 'safari', 'listen': '127.0.0.1', 'listen_port': 1080}])
        self.assertEqual(config['route'], {'final': 'upstream'})
        self.assertEqual(len(config['outbounds']), 1)
        self.assertEqual(config['outbounds'][0]['tls'], {'enabled': True, 'server_name': 'proxy.example'})
        self.assertNotIn('routing_mark', config['outbounds'][0])
        self.assertNotIn('dns', config)
        self.assertEqual(secret['password'], 'secret')

    def test_bad_gateway_configuration_is_rejected_before_startup(self):
        secret = {'address': '192.0.2.1', 'port': 443, 'scheme': 'https',
                  'host': 'proxy.example', 'username': 'user', 'password': 'secret'}
        for change in [{'port': True}, {'port': 65536}, {'address': 'proxy.example'},
                       {'scheme': 'direct'}, {'password': 'secret\n'}, {'host': ''}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                relay.configuration(secret | change)

    def guest(self, fail_mutation=False):
        class FakeGuest:
            cid = 153
            actions = []
            preferences = {}

            def _rpc(self, method, **params):
                if method == 'files.write':
                    self.data = base64.b64decode(params['content'])
                    return {'bytes': len(self.data)}
                if method == 'files.read':
                    return {'truncated': False, 'content': base64.b64encode(self.data).decode()}
                if method == 'files.chmod':
                    return {}
                self.actions.append(params['source'])
                action = 'enable' if '["enable"]' in params['source'] else 'read'
                before = dict(self.preferences)
                if action == 'enable':
                    self.preferences = dict.fromkeys(KEYS, ENDPOINT)
                    if fail_mutation:
                        raise GuestTransportError('receive_headers', TimeoutError('uncertain'))
                result = {'status': 'complete', 'application': 'com.apple.mobilesafari',
                          'before': before, 'after': self.preferences, 'action': action}
                return {'ok': True, 'crashed': False, 'result': json.dumps({
                    'status': 0, 'signal': None, 'stdout': plistlib.dumps(result).decode(), 'stderr': ''})}
        return FakeGuest()

    def test_write_is_verified_by_a_separate_native_read_without_claiming_routing(self):
        with tempfile.TemporaryDirectory() as temp:
            g = self.guest()
            result = configure(g, b'helper', hashlib.sha256(b'helper').hexdigest(), Path(temp)/'result', 'browser')
            self.assertEqual(result['status'], 'preferences_verified')
            self.assertEqual(len(g.actions), 3)
            self.assertFalse(result['browser_routing_verified'])
            self.assertFalse(result['system_proxy_modified'])

    def test_uncertain_mutation_is_preserved_and_never_replayed(self):
        with tempfile.TemporaryDirectory() as temp:
            g = self.guest(fail_mutation=True)
            with self.assertRaises(GuestTransportError):
                configure(g, b'helper', hashlib.sha256(b'helper').hexdigest(), Path(temp)/'result', 'browser')
            self.assertEqual(len(g.actions), 2)
            result = json.loads((Path(temp)/'result/report.json').read_text())
            self.assertEqual(result['status'], 'failed_no_retry')
            self.assertEqual(result['calls'][-1]['status'], 'requested')
            with self.assertRaises(FileExistsError):
                configure(g, b'helper', hashlib.sha256(b'helper').hexdigest(), Path(temp)/'result', 'browser')
            self.assertEqual(len(g.actions), 2)
