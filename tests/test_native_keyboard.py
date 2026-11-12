import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from tungtungtungbrowser.guest import Guest, GuestError, GuestTransportError
from tungtungtungbrowser.native_keyboard import KEYS, check_helper, helper_path

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 64
SPEC = importlib.util.spec_from_file_location('install_keyboard_helper', ROOT/'scripts/install_keyboard_helper.py')
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def receipt(name='cmd+w'):
    pairs = [(KEYS[name], 1), (KEYS[name], 0)]
    if name.startswith('cmd+'):
        pairs = [(227, 1), *pairs, (227, 0)]
    return {'protocol': 1, 'status': 'dispatched', 'key': name,
            'dispatch_count': len(pairs), 'release_dispatched': True,
            'post_release_wait_completed': True, 'application_consumption_verified': False,
            'events': [{'usage_page': 7, 'usage': usage, 'down': down,
                        'before_ns': 100 + i * 2, 'after_ns': 101 + i * 2}
                       for i, (usage, down) in enumerate(pairs)]}


def envelope(value, **changes):
    process = {'status': 0, 'signal': None, 'stderr': '', 'stdout': json.dumps(value)}
    process.update(changes)
    return {'ok': True, 'crashed': False, 'output': '', 'result': json.dumps(process)}


class NativeKeyboardTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.answer = envelope(receipt())
        self.guest = Guest(keyboard_helper_sha256=SHA)
        def request(method, path, data):
            self.calls.append(data)
            if isinstance(self.answer, Exception):
                raise self.answer
            return {'id': data['id'], 'type': 'response', 'result': self.answer}
        self.guest.request = request

    def test_navigation_keys_use_one_fixed_native_invocation(self):
        for name in KEYS:
            self.calls.clear()
            self.answer = envelope(receipt(name))
            result = self.guest.call('input.key', name=name)
            self.assertEqual((result['key'], result['dispatched']), (name, True))
            self.assertFalse(result['application_consumption_verified'])
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(self.calls[0]['method'], 'script.eval')
            self.assertEqual(self.calls[0]['params'], {
                'source': 'proc.run(' + json.dumps(helper_path(SHA)) + ', '
                          + json.dumps(['press', name]) + ', "")', 'timeout': 30})

    def test_unconfigured_guest_retains_daemon_key_route(self):
        self.guest.keyboard_helper_sha256 = None
        self.answer = {'key': 'cmd+w', 'dispatched': True}
        self.assertEqual(self.guest.call('input.key', name='cmd+w'), self.answer)
        self.assertEqual([x['method'] for x in self.calls], ['input.key'])

    def test_invalid_configuration_or_key_cannot_send_input(self):
        for digest in ['', 'A'*64, 'a'*63, '../helper', True]:
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                Guest(keyboard_helper_sha256=digest)
        for name in [None, 1, [], 'cmd+shift+w', 'CMD+W', 'return;danger']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.guest.call('input.key', name=name)
        with self.assertRaises(ValueError):
            self.guest.call('input.key', name='cmd+w', extra=True)
        with self.assertRaises(ValueError):
            self.guest.call('input.key')
        with self.assertRaises(ValueError):
            self.guest.call('script.eval', source='caller()')
        self.assertEqual(self.calls, [])

    def test_transport_failure_is_preserved_without_replay_or_daemon_fallback(self):
        self.answer = GuestTransportError('read_body', TimeoutError('lost reply'))
        with self.assertRaises(GuestTransportError) as raised:
            self.guest.call('input.key', name='cmd+w')
        self.assertIs(raised.exception, self.answer)
        self.assertEqual([x['method'] for x in self.calls], ['script.eval'])

    def test_no_input_check_rejects_boolean_dispatch_count(self):
        self.answer = envelope({'protocol': 1, 'status': 'ready', 'dispatch_count': False})
        with self.assertRaises(GuestError):
            check_helper(self.guest, SHA)
        self.assertEqual(len(self.calls), 1)

    def test_cold_check_has_bounded_headroom_without_replaying_failure(self):
        self.answer = envelope({'protocol': 1, 'status': 'ready', 'dispatch_count': 0})
        check_helper(self.guest, SHA)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['params']['timeout'], 60)
        self.calls.clear()
        self.answer = GuestTransportError('read_body', TimeoutError('lost check reply'))
        with self.assertRaises(GuestTransportError) as raised:
            check_helper(self.guest, SHA)
        self.assertIs(raised.exception, self.answer)
        self.assertEqual(len(self.calls), 1)

    def test_process_failure_retains_evidence_and_never_replays(self):
        for changes in [{'status': 7}, {'signal': 15}, {'stderr': 'failed'},
                        {'stdout': 'partial'}, {'status': False}]:
            self.calls.clear()
            self.answer = envelope(receipt(), **changes)
            with self.subTest(changes=changes), self.assertRaises(GuestError) as raised:
                self.guest.call('input.key', name='cmd+w')
            self.assertEqual(raised.exception.native_script_response, self.answer)
            self.assertEqual(len(self.calls), 1)

    def test_bad_admin_envelope_is_terminal(self):
        for changes in [{'ok': False}, {'crashed': True}, {'output': 'unexpected'},
                        {'result': 'x'*16385}, {'result': '{}'}]:
            self.calls.clear()
            self.answer = {**envelope(receipt()), **changes}
            with self.subTest(changes=list(changes)), self.assertRaises(GuestError):
                self.guest.call('input.key', name='cmd+w')
            self.assertEqual(len(self.calls), 1)

    def test_partial_wrong_or_unordered_dispatch_is_not_acknowledged(self):
        broken = []
        for key, value in [('dispatch_count', True), ('release_dispatched', False),
                           ('key', 'cmd+t'), ('application_consumption_verified', True),
                           ('post_release_wait_completed', False), ('events', []),
                           ('protocol', True)]:
            broken.append({**receipt(), key: value})
        for key, value in [('usage', 23), ('down', True), ('usage_page', 8),
                           ('before_ns', -1), ('after_ns', 2**64), ('before_ns', 200),
                           ('before_ns', 100.0)]:
            value_copy = copy.deepcopy(receipt())
            value_copy['events'][2][key] = value
            broken.append(value_copy)
        wrong_order = receipt()
        wrong_order['events'][2], wrong_order['events'][3] = wrong_order['events'][3], wrong_order['events'][2]
        broken.append(wrong_order)
        for value in broken:
            self.calls.clear()
            self.answer = envelope(value)
            with self.subTest(value=value), self.assertRaises(GuestError):
                self.guest.call('input.key', name='cmd+w')
            self.assertEqual(len(self.calls), 1)

    def test_check_requests_no_key(self):
        self.answer = envelope({'protocol': 1, 'status': 'ready', 'dispatch_count': 0})
        self.assertEqual(check_helper(self.guest, SHA)['dispatch_count'], 0)
        self.assertIn('["check"]', self.calls[0]['params']['source'])
        self.answer = envelope(receipt())
        with self.assertRaises(GuestError):
            check_helper(self.guest, SHA)

    @unittest.skipUnless(shutil.which('cc'), 'C compiler required')
    def test_native_preallocation_release_order_and_interrupted_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory)/'check')
            subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                            str(ROOT/'native/check_keyboard_helper.c'), '-o', binary],
                           capture_output=True, check=True)
            for case in ['normal', 'allocation', 'interrupted', 'invalid']:
                with self.subTest(case=case):
                    result = subprocess.run([binary, case], capture_output=True, text=True,
                                            check=True, timeout=5)
                    native = json.loads(result.stdout)
                    self.assertEqual(native['dispatch_count'], 0 if case in ('allocation', 'invalid') else 4)

    def test_installer_rejects_changed_source_or_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = struct.pack('<4I', 0xFEEDFACF, 0x100000C, 0, 2) + bytes(16)
            (root/'keyboard').write_bytes(data)
            manifest = {'protocol': 1, 'binary': 'keyboard',
                        'binary_sha256': hashlib.sha256(data).hexdigest(),
                        'sources': {name: hashlib.sha256((ROOT/'native'/name).read_bytes()).hexdigest()
                                    for name in ('keyboard_helper.c', 'keyboard_libSystem.tbd',
                                                 'keyboard_entitlements.plist')}}
            (root/'build.json').write_text(json.dumps(manifest))
            self.assertEqual(installer.read_build(root)[0], data)
            (root/'keyboard').write_bytes(data + b'x')
            with self.assertRaises(ValueError):
                installer.read_build(root)
            (root/'keyboard').write_bytes(data)
            manifest['sources']['keyboard_helper.c'] = 'b'*64
            (root/'build.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                installer.read_build(root)


if __name__ == '__main__':
    unittest.main()
