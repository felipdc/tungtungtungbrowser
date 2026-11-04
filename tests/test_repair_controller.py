import base64
import hashlib
import json
from pathlib import Path
import plistlib
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import manage_touch_repair as controller
from tungtungtungbrowser.guest import GuestError, GuestTransportError

BOOT = '12345678-1234-1234-1234-123456789abc'
SHA = 'a' * 64


def process(stdout, status=0):
    return {'ok': True, 'crashed': False, 'output': '', 'result': json.dumps(
        {'status': status, 'signal': None, 'stdout': stdout, 'stderr': ''})}


def events(operation, attempt='', boot=BOOT, digest=None):
    if operation == 'inspect':
        stages = [('inspection', 'verified', digest or controller.RECIPE.original_sha256)]
    else:
        before, after = controller.RECIPE.original_sha256, controller.RECIPE.repaired_sha256
        if operation == 'revert': before, after = after, before
        stages = [('preparing', 'running', before), ('backup_verified', 'running', before),
                  ('ready_to_replace', 'running', after), ('reboot_required', 'complete', after)]
    return [{'protocol': 1, 'operation': operation, 'attempt': attempt, 'stage': stage,
             'status': status, 'sha256': value, 'boot_uuid': boot,
             'replacement_performed': stage == 'reboot_required', 'errno': 0}
            for stage, status, value in stages]


class Guest:
    def __init__(self, output):
        self.output = output
        self.calls = []
        self.fail_on = None
        self.read_only = True
        self.boot = BOOT
        self.digest = controller.RECIPE.original_sha256
        self.journal = None
        self.bad_readback = False

    def _rpc(self, method, **params):
        # The durable request record must exist before any request reaches us.
        report = json.loads((self.output / 'report.json').read_text())
        self.calls.append((method, params))
        assert report['calls'][-1]['state'] == 'requested'
        if len(self.calls) == self.fail_on:
            raise GuestTransportError('receive_headers', TimeoutError('lost reply'))
        if method == 'files.read':
            raw = b'\n'.join(json.dumps(row).encode() for row in self.journal) + b'\n'
            if self.bad_readback: raw = b'[]\n'
            return {'path': params['path'], 'encoding': 'base64', 'size': len(raw),
                    'truncated': False, 'content': base64.b64encode(raw).decode()}
        assert method == 'script.eval'
        assert params['timeout'] == 600
        path, arguments, stdin = json.loads('[' + params['source'][9:-1] + ']')
        assert stdin == ''
        if path == '/sbin/mount':
            if arguments:
                assert arguments == ['-u', '-w', '/']
                self.read_only = False
                return process('')
            return process('/dev/disk1s1 on / (apfs, local' + (', read-only' if self.read_only else '') + ')\n')
        assert path == controller.helper_path(SHA)
        if arguments[0] == 'inspect':
            result = events('inspect', boot=self.boot, digest=self.digest)
        else:
            assert report['mutation_requested'] is True
            result = events(arguments[0], arguments[1], boot=self.boot)
            self.journal = result
            self.digest = result[-1]['sha256']
        return process('\n'.join(json.dumps(row) for row in result) + '\n')


class RepairControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def session(self, name='run', operation='apply'):
        output = self.root / name
        guest = Guest(output)
        return controller.Session(guest, SHA, operation, output), guest

    def test_apply_records_fixed_native_requests_and_stops_for_reboot(self):
        session, guest = self.session()
        session.change('apply', {'runtime_firmware_attested': False})
        session.save()
        self.assertEqual(session.report['status'], 'reboot_required')
        self.assertFalse(session.report['browser_verified'])
        self.assertEqual(len(guest.calls), 6)
        expected = 'proc.run(' + json.dumps(controller.helper_path(SHA)) + ', ' + json.dumps(
            ['apply', session.report['attempt']]) + ', "")'
        self.assertEqual(guest.calls[4], ('script.eval', {'source': expected, 'timeout': 600}))
        self.assertEqual(guest.calls[-1][0], 'files.read')
        self.assertEqual(json.loads((session.output / 'native-journal.json').read_text()), guest.journal)

    def test_every_lost_response_stops_without_retry_or_reboot(self):
        for index in range(1, 7):
            with self.subTest(request=index):
                session, guest = self.session(str(index))
                guest.fail_on = index
                with self.assertRaises(GuestTransportError):
                    session.change('apply', {})
                self.assertEqual(len(guest.calls), index)
                report = json.loads((session.output / 'report.json').read_text())
                self.assertEqual(report['calls'][-1]['state'], 'failed')
                self.assertTrue(report['calls'][-1]['request_may_have_been_sent'])
                self.assertEqual(report['mutation_requested'], index >= 5)
                self.assertNotEqual(report['status'], 'reboot_required')

    def test_wrong_preimage_or_writable_root_does_not_mutate_cache(self):
        for name in ('preimage', 'root'):
            session, guest = self.session(name)
            if name == 'preimage': guest.digest = controller.RECIPE.repaired_sha256
            else: guest.read_only = False
            with self.assertRaises(GuestError): session.change('apply', {})
            self.assertLessEqual(len(guest.calls), 2)
            self.assertFalse(session.report['mutation_requested'])

    def test_bad_native_journal_readback_never_claims_success(self):
        session, guest = self.session()
        guest.bad_readback = True
        with self.assertRaises(GuestError): session.change('apply', {})
        self.assertEqual(session.report['status'], 'mutation_requested')
        self.assertEqual(len(guest.calls), 6)
        self.assertEqual(len(session.report['native_events']), 4)

    def test_verify_requires_new_boot_readonly_root_hash_and_same_journal(self):
        session, guest = self.session()
        session.change('apply', {})
        previous = session.report
        for fault in ('none', 'boot', 'mount', 'hash', 'journal'):
            with self.subTest(fault=fault):
                check, remote = self.session('verify-' + fault, 'verify')
                remote.boot = BOOT[:-1] + 'd' if fault != 'boot' else BOOT
                remote.digest = controller.RECIPE.repaired_sha256 if fault != 'hash' else controller.RECIPE.original_sha256
                remote.journal = guest.journal
                remote.read_only = fault != 'mount'
                remote.bad_readback = fault == 'journal'
                if fault == 'none':
                    check.verify(previous)
                    self.assertEqual(check.report['status'], 'reboot_verified')
                    self.assertFalse(check.report['browser_verified'])
                else:
                    with self.assertRaises(GuestError): check.verify(previous)
                self.assertEqual(len(remote.calls), 3)
                self.assertTrue(all('"apply"' not in row[1].get('source', '') for row in remote.calls))

    def test_unknown_native_output_is_retained_without_followup(self):
        session, guest = self.session()
        response = {'ok': False, 'crashed': True, 'error': 'native crash', 'output': ''}
        guest._rpc = lambda *args, **kwargs: response
        with self.assertRaises(GuestError): session.helper('inspect')
        report = json.loads((session.output / 'report.json').read_text())
        self.assertEqual(len(report['calls']), 1)
        self.assertEqual(report['calls'][0]['response'], response)

    def test_bad_identifiers_and_existing_evidence_never_send_requests(self):
        for sha in ('', 'A' * 64, '../target', 'a' * 63):
            with self.assertRaises(ValueError): controller.helper_path(sha)
        session, guest = self.session()
        with self.assertRaises(ValueError): session.read_journal('../outside')
        with self.assertRaises(ValueError): session.helper('apply', 'bad')
        with self.assertRaises(FileExistsError): controller.Session(guest, SHA, 'apply', session.output)
        self.assertEqual(guest.calls, [])

    def test_build_attestation_rejects_changed_binary_recipe_or_sources(self):
        data = struct.pack('<8I', 0xFEEDFACF, 0x100000C, 0, 2, 0, 0, 0, 0)
        (self.root / 'touch-repair').write_bytes(data)
        report = {'protocol': 1, 'binary': 'touch-repair', 'repair_id': controller.REPAIR_ID,
                  'binary_sha256': hashlib.sha256(data).hexdigest(), 'sources': controller.source_hashes(),
                  'recipe_sha256': hashlib.sha256(controller.recipe_header().encode()).hexdigest()}
        (self.root / 'build.json').write_text(json.dumps(report))
        self.assertEqual(controller.read_build(self.root)[0], data)
        for key, value in [('binary_sha256', SHA), ('sources', {}), ('recipe_sha256', SHA), ('protocol', True)]:
            (self.root / 'build.json').write_text(json.dumps({**report, key: value}))
            with self.assertRaises(ValueError): controller.read_build(self.root)

    def test_firmware_receipt_requires_both_prerequisites(self):
        receipt = self.root / 'PatchReceipt.plist'
        parts = {'kernelcache': {'Patches': ['kernel-boot-amfi_trustcache']},
                 'TXM': {'Patches': ['txm-boot-trustcache_bypass']}}
        receipt.write_bytes(plistlib.dumps({'Parts': parts}))
        self.assertFalse(controller.firmware_receipt(receipt)['runtime_firmware_attested'])
        parts['TXM']['Patches'] = []
        receipt.write_bytes(plistlib.dumps({'Parts': parts}))
        with self.assertRaises(ValueError): controller.firmware_receipt(receipt)

    def test_upload_is_verified_before_rename_and_stops_on_each_lost_response(self):
        data = b'native helper fixture'
        digest = hashlib.sha256(data).hexdigest()
        for failed in (None, 1, 2, 3, 4, 5):
            with self.subTest(failed=failed):
                output = self.root / ('install-' + str(failed))
                calls = []

                class UploadGuest:
                    def _rpc(self, method, **params):
                        calls.append((method, params))
                        self_report = json.loads((output / 'report.json').read_text())
                        assert self_report['calls'][-1]['state'] == 'requested'
                        if len(calls) == failed:
                            raise GuestTransportError('read_body', TimeoutError('lost upload response'))
                        if method == 'files.write':
                            return {'path': params['path'], 'bytes': len(data)}
                        if method == 'files.read':
                            return {'path': params['path'], 'size': len(data), 'encoding': 'base64',
                                    'content': base64.b64encode(data).decode(), 'truncated': False}
                        if method == 'files.chmod': return {}
                        if method == 'files.rename':
                            assert [c[0] for c in calls] == ['files.write', 'files.read', 'files.chmod', 'files.rename']
                            return {'source': params['from'], 'destination': params['to']}
                        assert method == 'script.eval' and '"inspect"' in params['source']
                        return process(json.dumps(events('inspect')[0]))

                session = controller.Session(UploadGuest(), digest, 'install', output)
                if failed is None:
                    session.install(data)
                    self.assertEqual(session.report['status'], 'helper_installed')
                    self.assertEqual(len(calls), 5)
                else:
                    with self.assertRaises(GuestTransportError): session.install(data)
                    self.assertEqual(len(calls), failed)
                    self.assertNotEqual(session.report['status'], 'helper_installed')
                self.assertFalse(session.report['mutation_requested'])


if __name__ == '__main__':
    unittest.main()
