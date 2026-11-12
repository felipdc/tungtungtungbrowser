import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import shutil
import subprocess
import struct
import unittest
from unittest.mock import Mock

from tungtungtungbrowser import Browser
from tungtungtungbrowser.guest import Guest, GuestError, GuestTransportError
from tungtungtungbrowser.native_touch import helper_path, check_helper

SPEC = importlib.util.spec_from_file_location('install_touch_helper', Path(__file__).resolve().parents[1] / 'scripts/install_touch_helper.py')
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
SHA = 'a' * 64
SCREEN = {'width': 393, 'height': 852, 'scale': 3, 'orientation': 0, 'locked': False, 'screen_off': False}


def result():
    return {'protocol': 1, 'status': 'dispatched', 'dispatch_count': 2, 'release_dispatched': True,
            'normalized_x': 196 / 393, 'normalized_y': 105 / 852, 'normalized_radius': 5 / 852,
            'requested_hold_ms': 50, 'hold_completed': True, 'page_result_verified': False}


def process_result(value, **overrides):
    process = {'status': 0, 'signal': None, 'stdout': json.dumps(value), 'stderr': ''}
    process.update(overrides)
    return {'ok': True, 'crashed': False, 'output': '', 'result': json.dumps(process)}


class NativeTouchTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'C compiler is required for native helper checks')
    def test_native_event_order_release_on_interruption_and_preallocation_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = str(Path(directory) / 'check')
            source = Path(__file__).resolve().parents[1] / 'native/check_touch_helper.c'
            subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', str(source), '-o', executable],
                           check=True, capture_output=True, text=True)
            result = subprocess.run([executable], check=True, capture_output=True, text=True, timeout=5)
            self.assertIn('Native helper host checks passed.', result.stdout)

    def setUp(self):
        self.calls = []
        self.screen = SCREEN.copy()
        self.answer = process_result(result())
        self.guest = Guest(touch_helper_sha256=SHA)
        def request(method, path, data):
            self.calls.append(data)
            answer = self.screen if data['method'] == 'device.screen' else self.answer
            if isinstance(answer, Exception): raise answer
            return {'id': data['id'], 'type': 'response', 'result': answer}
        self.guest.request = request

    def test_fixed_native_command_and_calibrated_coordinates(self):
        response = self.guest.tap(196, 105)
        self.assertEqual(response['method'], 'native_calibrated_helper')
        self.assertFalse(response['page_result_verified'])
        self.assertEqual([c['method'] for c in self.calls], ['device.screen', 'script.eval'])
        script = self.calls[1]['params']
        expected = 'proc.run(' + json.dumps(helper_path(SHA)) + ', ' + json.dumps(
            ['tap', repr(196 / 393), repr(105 / 852), repr(5 / 852), '50']) + ', "")'
        self.assertEqual(script, {'source': expected, 'timeout': 30})
        self.assertEqual(self.calls[0]['params'], {})

    def test_wrong_geometry_locked_or_missing_state_sends_no_input(self):
        for key, value in [('width', 394), ('height', 853), ('scale', 2), ('orientation', 90),
                           ('locked', True), ('screen_off', True), ('orientation', False), ('width', None)]:
            self.screen = {**SCREEN, key: value}
            self.calls.clear()
            with self.subTest(key=key), self.assertRaises(GuestError): self.guest.tap(196, 105)
            self.assertEqual([c['method'] for c in self.calls], ['device.screen'])

    def test_browser_hold_uses_existing_helper_and_checks_echoed_duration(self):
        self.answer = process_result({**result(), 'requested_hold_ms': 800})
        answer = Browser(guest=self.guest).tap(196, 105, hold_ms=800)
        self.assertEqual(answer['native']['requested_hold_ms'], 800)
        self.assertEqual([c['method'] for c in self.calls], ['device.screen', 'script.eval'])
        self.assertIn(', "800"]', self.calls[-1]['params']['source'])
        self.calls.clear()
        self.answer = process_result(result())
        with self.assertRaises(GuestError):
            Browser(guest=self.guest).tap(196, 105, hold_ms=800)
        self.assertEqual([c['method'] for c in self.calls], ['device.screen', 'script.eval'])

    def test_configured_zero_hold_and_explicit_override(self):
        browser = Browser(guest=self.guest, touch_hold_ms=0)
        self.answer = process_result({**result(), 'requested_hold_ms': 0})
        self.assertEqual(browser.tap(196, 105)['native']['requested_hold_ms'], 0)
        self.assertIn(', "0"]', self.calls[-1]['params']['source'])
        self.calls.clear()
        self.answer = process_result(result())
        self.assertEqual(browser.tap(196, 105, hold_ms=50)['native']['requested_hold_ms'], 50)
        self.assertIn(', "50"]', self.calls[-1]['params']['source'])

    def test_invalid_configured_hold_sends_no_input(self):
        for value in (-1, 1001, True, 0.0, None, '0'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Browser(guest=self.guest, touch_hold_ms=value)
        self.assertEqual(self.calls, [])

    def test_invalid_hold_fails_before_geometry_or_input(self):
        for value in (-1, 1001, True, 50.0, float('nan'), '800', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Browser(guest=self.guest).tap(196, 105, hold_ms=value)
        self.assertEqual(self.calls, [])

    def test_long_hold_transport_failure_is_not_repeated(self):
        self.answer = GuestTransportError('read_body', TimeoutError('timeout'))
        with self.assertRaises(GuestTransportError) as raised:
            Browser(guest=self.guest).tap(196, 105, hold_ms=800)
        self.assertIs(raised.exception, self.answer)
        self.assertEqual([c['method'] for c in self.calls], ['device.screen', 'script.eval'])

    def test_bad_arguments_and_helper_identifiers_fail_before_input(self):
        for x, y in [(True, 1), (float('nan'), 1), (-1, 1), (1, float('inf')), ('1', 1)]:
            with self.assertRaises(ValueError): self.guest.tap(x, y)
        self.assertEqual(self.calls, [])
        for x, y in [(393, 105), (196, 852)]:
            with self.assertRaises(ValueError): self.guest.tap(x, y)
        self.assertTrue(all(c['method'] == 'device.screen' for c in self.calls))
        for digest in (None, '', 'A' * 64, 'a' * 63, '../program', '";print(1)//'):
            with self.assertRaises(ValueError): helper_path(digest)

    def test_administrative_bridge_is_not_a_public_script_api(self):
        with self.assertRaises(ValueError): self.guest.call('script.eval', source='arbitrary()')
        self.assertEqual(self.calls, [])

    def test_helper_transport_failure_is_preserved_without_retry_or_fallback(self):
        self.answer = GuestTransportError('receive_headers', TimeoutError('timeout'))
        with self.assertRaises(GuestTransportError) as caught: self.guest.tap(196, 105)
        self.assertIs(caught.exception, self.answer)
        self.assertTrue(caught.exception.request_may_have_been_sent)
        self.assertEqual([c['method'] for c in self.calls], ['device.screen', 'script.eval'])

    def test_failed_or_malformed_process_retains_original_response(self):
        for answer in [None, {'ok': False}, process_result(result(), status=7),
                       process_result(result(), signal=9), process_result(result(), stderr='error'),
                       {**process_result(result()), 'result': '{}'},
                       {**process_result(result()), 'result': 'not json'}]:
            self.answer = answer
            self.calls.clear()
            with self.subTest(answer=answer), self.assertRaises(GuestError) as caught:
                self.guest.tap(196, 105)
            self.assertEqual(caught.exception.native_script_response, answer)
            self.assertEqual(len(self.calls), 2)

    def test_invalid_release_and_numeric_fields_fail_without_repeating(self):
        for key, value in [('release_dispatched', False), ('dispatch_count', True),
                           ('hold_completed', False), ('page_result_verified', True),
                           ('normalized_radius', 5), ('normalized_y', float('nan')),
                           ('normalized_x', True), ('protocol', 2)]:
            self.answer = process_result({**result(), key: value})
            self.calls.clear()
            with self.subTest(key=key), self.assertRaises(GuestError): self.guest.tap(196, 105)
            self.assertEqual(len(self.calls), 2)

    def test_check_sends_no_tap_or_geometry_query(self):
        self.answer = process_result({'protocol': 1, 'status': 'ready', 'dispatch_count': 0})
        check_helper(self.guest, SHA)
        self.assertEqual(len(self.calls), 1)
        self.assertIn('["check"]', self.calls[0]['params']['source'])
        self.assertEqual(self.calls[0]['params']['timeout'], 60)

    def test_cold_check_failure_is_preserved_without_replay(self):
        self.answer = GuestTransportError('receive_headers', TimeoutError('timeout'))
        with self.assertRaises(GuestTransportError) as caught:
            check_helper(self.guest, SHA)
        self.assertIs(caught.exception, self.answer)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['params']['timeout'], 60)

    def test_browser_accepts_explicit_helper_and_custom_guest_remains_explicit(self):
        self.assertEqual(Browser(touch_helper_sha256=SHA).guest.touch_helper_sha256, SHA)
        with self.assertRaises(ValueError): Browser(guest=self.guest, touch_helper_sha256=SHA)


class TouchInstallerTests(unittest.TestCase):
    def test_build_manifest_architecture_size_and_digests_are_checked(self):
        directory = self.root / 'build'
        directory.mkdir()
        binary = struct.pack('<8I', 0xFEEDFACF, 0x100000C, 0, 2, 0, 0, 0, 0)
        (directory/'touch').write_bytes(binary)
        build = {'protocol': 1, 'binary': 'touch', 'binary_sha256': hashlib.sha256(binary).hexdigest(),
                 'sources': {name: hashlib.sha256((installer.ROOT/'native'/name).read_bytes()).hexdigest()
                             for name in ('touch_helper.c','touch_libSystem.tbd','touch_entitlements.plist')}}
        manifest = directory/'build.json'
        manifest.write_text(json.dumps(build))
        self.assertEqual(installer.read_build(directory), (binary, build['binary_sha256']))
        for changed in ([], {**build, 'protocol': True}, {**build, 'binary_sha256': SHA}, {**build, 'sources': {}}):
            manifest.write_text(json.dumps(changed))
            with self.assertRaises(ValueError): installer.read_build(directory)
        manifest.write_text(json.dumps(build))
        for changed in (b'x', b'x' + binary[1:], b'x' * (256 * 1024 + 1)):
            (directory/'touch').write_bytes(changed)
            with self.assertRaises(ValueError): installer.read_build(directory)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = b'synthetic executable bytes for transport tests'
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.calls = []
        self.fail = None
        self.corrupt = False
        def rpc(method, **params):
            self.calls.append((method, params))
            if method == self.fail: raise GuestTransportError('receive_headers', TimeoutError('timeout'))
            if method == 'files.write': return {'path': params['path'], 'bytes': len(self.data)}
            if method == 'files.read':
                return {'path': params['path'], 'encoding': 'base64', 'truncated': False, 'size': len(self.data),
                        'content': base64.b64encode(b'corrupt' if self.corrupt else self.data).decode()}
            if method == 'files.rename': return {'source': params['from'], 'destination': params['to']}
            if method == 'script.eval': return process_result({'protocol': 1, 'status': 'ready', 'dispatch_count': 0})
            return {}
        self.guest = Mock()
        self.guest._rpc.side_effect = rpc

    def test_upload_readback_rename_and_check_are_recorded_without_input(self):
        report = installer.install(self.guest, self.data, self.sha, self.root / 'install')
        self.assertEqual(report['status'], 'complete')
        self.assertFalse(report['native_input_sent'])
        self.assertEqual([m for m, _ in self.calls], ['files.write','files.read','files.chmod','files.rename','script.eval'])
        self.assertEqual(self.calls[3][1]['to'], helper_path(self.sha))
        self.assertIn('["check"]', self.calls[-1][1]['source'])
        self.assertEqual(json.loads((self.root/'install/install.json').read_text()), report)
        self.assertNotIn('content', report['calls'][1]['result'])

    def test_every_transport_failure_preserves_evidence_without_retry_or_cleanup(self):
        for number, method in enumerate(('files.write','files.read','files.chmod','files.rename','script.eval')):
            self.fail = method
            self.calls.clear()
            out = self.root / str(number)
            with self.subTest(method=method), self.assertRaises(GuestTransportError):
                installer.install(self.guest, self.data, self.sha, out)
            report = json.loads((out/'install.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['calls'][-1]['state'], 'failed')
            self.assertEqual([m for m, _ in self.calls].count(method), 1)
            self.assertEqual(self.calls[-1][0], method)

    def test_corrupt_readback_stops_before_chmod_or_installation(self):
        self.corrupt = True
        with self.assertRaises(GuestError): installer.install(self.guest, self.data, self.sha, self.root/'bad')
        self.assertEqual([m for m, _ in self.calls], ['files.write','files.read'])
        self.assertEqual(json.loads((self.root/'bad/install.json').read_text())['status'], 'failed')

    def test_existing_evidence_directory_and_wrong_digest_send_no_requests(self):
        with self.assertRaises(FileExistsError): installer.install(self.guest, self.data, self.sha, self.root)
        with self.assertRaises(ValueError): installer.install(self.guest, self.data, SHA, self.root/'bad')
        self.assertEqual(self.calls, [])


if __name__ == '__main__': unittest.main()
