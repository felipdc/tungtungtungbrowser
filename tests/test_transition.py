import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts import measure_transition as transition
from tungtungtungbrowser.guest import Guest, GuestError


class TransitionTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.vm = Path(directory.name)
        self.out = self.vm / 'evidence'
        self.launch = {'pid': os.getpid(), 'cid': 3,
                       'pid_start_ticks': Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19],
                       'host_boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                       'started_monotonic': time.monotonic() - 60}
        (self.vm / 'launch.json').write_text(json.dumps(self.launch))
        self.methods = []
        self.inventory_reads = 0
        self.fail_method = None
        self.change_at = None
        self.foreground_pid = 123
        self.launch_pid = 123
        self.verified = True
        self.enterContext(patch.object(Guest, '_rpc', side_effect=self.rpc))
        self.enterContext(patch.object(transition.Browser, 'screenshot', side_effect=self.capture))
        self.enterContext(patch.object(transition.time, 'sleep'))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def report(self):
        return json.loads((self.out / 'report.json').read_text())

    def rpc(self, method, **params):
        # Evidence must exist before a possibly ambiguous native operation.
        report = self.report()
        self.assertEqual(report['calls'][-1]['method'], method)
        self.assertNotIn('finished_monotonic', report['calls'][-1])
        self.methods.append(method)
        if method == self.fail_method:
            raise GuestError('transport receive_headers: timed out')
        if method == 'processes.list':
            self.inventory_reads += 1
            return {'processes': [{'pid': 123, 'start_time': 1000 + (self.inventory_reads == self.change_at),
                                   'executable': '/Applications/MobileSafari.app/MobileSafari',
                                   'bundle_id': transition.SAFARI}]}
        return {'apps.foreground': {'bundle_id': transition.SAFARI, 'verified': True, 'pid': self.foreground_pid},
                'input.button': {'button': 'home'}, 'screen.unlock': {'locked': False},
                'apps.launch': {'pid': self.launch_pid, 'frontmost_verified': self.verified}}[method]

    def capture(self, path):
        Path(path).write_bytes(b'test display')
        class Shot:
            def metadata(self):
                return {'test': True}
        return Shot()

    def run_trial(self, *extra):
        return transition.main([str(self.vm), '--output', str(self.out),
                                '--qmp-socket', str(self.vm / 'control.sock'), *extra])

    def test_complete_preserves_sequence_and_does_not_claim_rendering(self):
        self.assertEqual(self.run_trial(), 0)
        self.assertEqual(self.methods, ['apps.foreground', 'processes.list', 'input.button',
                                       'processes.list', 'screen.unlock', 'apps.launch', 'processes.list'])
        report = self.report()
        self.assertTrue(report['process_continuity_verified'])
        self.assertFalse(report['navigation_verified'])
        self.assertFalse(report['home_screen_verified'])
        self.assertEqual(report['visual_review'], 'pending')
        self.assertEqual(set(report['screenshots']), {'before', 'home', 'returned'})
        self.assertGreaterEqual(report['elapsed_since_launch_seconds'], 60)

    def test_transport_failures_do_not_retry_input_or_continue(self):
        for method in ['apps.foreground', 'processes.list', 'input.button', 'screen.unlock', 'apps.launch']:
            with self.subTest(method=method):
                self.out = self.vm / method
                self.methods = []
                self.fail_method = method
                self.assertEqual(self.run_trial(), 1)
                self.assertEqual(self.methods[-1], method)
                self.assertEqual(self.methods.count(method), 1)
                self.assertEqual(self.report()['status'], 'failed')
                self.assertIn('timed out', self.report()['calls'][-1]['error'])
                self.assertTrue((self.out / 'failure.png').exists())

    def test_pid_reuse_after_home_stops_before_launch(self):
        self.change_at = 2
        self.assertEqual(self.run_trial(), 1)
        self.assertNotIn('screen.unlock', self.methods)
        self.assertNotIn('apps.launch', self.methods)

    def test_pid_reuse_after_return_fails_without_another_launch(self):
        self.change_at = 3
        self.assertEqual(self.run_trial(), 1)
        self.assertEqual(self.methods.count('apps.launch'), 1)
        self.assertNotIn('process_continuity_verified', self.report())

    def test_foreground_pid_disagreement_prevents_home(self):
        self.foreground_pid = 456
        self.assertEqual(self.run_trial(), 1)
        self.assertNotIn('input.button', self.methods)

    def test_launch_pid_disagreement_is_not_recovered(self):
        self.launch_pid = 456
        self.assertEqual(self.run_trial(), 1)
        self.assertEqual(self.methods.count('apps.launch'), 1)
        self.assertEqual(self.inventory_reads, 2)

    def test_unverified_launch_fails_without_retry(self):
        self.verified = False
        self.assertEqual(self.run_trial(), 1)
        self.assertEqual(self.methods.count('apps.launch'), 1)

    def test_existing_evidence_is_not_overwritten(self):
        self.out.mkdir()
        (self.out / 'report.json').write_text('earlier failure')
        with self.assertRaises(FileExistsError):
            self.run_trial()
        self.assertEqual((self.out / 'report.json').read_text(), 'earlier failure')
        self.assertEqual(self.methods, [])

    def test_stale_vm_identity_prevents_native_calls(self):
        self.launch['pid_start_ticks'] = '0'
        (self.vm / 'launch.json').write_text(json.dumps(self.launch))
        with self.assertRaises(RuntimeError):
            self.run_trial()
        self.assertEqual(self.methods, [])
        self.assertFalse(self.out.exists())

    def test_failure_capture_does_not_replace_original_error(self):
        self.fail_method = 'input.button'
        original_capture = self.capture
        def capture(path):
            if Path(path).name == 'failure.png':
                raise RuntimeError('display unavailable')
            return original_capture(path)
        with patch.object(transition.Browser, 'screenshot', side_effect=capture):
            self.assertEqual(self.run_trial(), 1)
        self.assertIn('timed out', self.report()['error'])
        self.assertEqual(self.report()['failure_capture_error'], 'display unavailable')

    def test_invalid_waits_prevent_native_calls(self):
        for arg, value in [('home-seconds', 'nan'), ('return-seconds', '-1'), ('timeout', 'inf')]:
            with self.subTest(arg=arg), self.assertRaises(SystemExit):
                self.run_trial('--' + arg, value)
        self.assertEqual(self.methods, [])

    def test_nonfinite_response_cannot_break_failure_report(self):
        with patch.object(Guest, '_rpc', return_value={'time': float('nan')}):
            self.assertEqual(self.run_trial(), 1)
        report = self.report()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(len(report['calls']), 1)
        self.assertNotIn('result', report['calls'][0])
        self.assertIn('error', report['calls'][0])


class IdentityTest(unittest.TestCase):
    def test_rejects_missing_ambiguous_and_invalid_identity(self):
        row = {'pid': 123, 'start_time': 1000, 'bundle_id': transition.SAFARI,
               'executable': '/Applications/MobileSafari.app/MobileSafari'}
        for value in [None, {}, {'processes': []}, {'processes': [row, row]},
                      *({'processes': [{**row, key: value}]} for key, value in
                        [('pid', True), ('pid', 0), ('start_time', float('nan')),
                         ('start_time', False), ('bundle_id', 'another.app'),
                         ('executable', '/tmp/MobileSafari')])]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                transition.safari_identity(value)


if __name__ == '__main__':
    unittest.main()
