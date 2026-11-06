import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(shutil.which('cc') and sys.platform == 'linux', 'requires a Linux C compiler')
class NativeFocusGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.build.name) / 'focus-guard'
        result = subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                                 '-D_FORTIFY_SOURCE=3',
                                 str(Path(__file__).with_name('native_focus_guard_harness.c')),
                                 '-o', str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            cls.build.cleanup()
            raise RuntimeError('Native focus guard harness failed to compile:\n' + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.settings(0, 0)

    def settings(self, application, automation):
        for name, value in [('application', application), ('automation', automation)]:
            (self.root / name).write_text(str(value))

    def observed(self):
        return tuple(int((self.root / name).read_text()) for name in ('application', 'automation'))

    def wait_settings(self, expected):
        end = time.monotonic() + 3
        while time.monotonic() < end:
            try:
                if self.observed() == expected:
                    return
            except ValueError:  # File-backed setter is intentionally not atomic.
                pass
            time.sleep(0.01)
        self.assertEqual(self.observed(), expected)

    def holder(self):
        process = subprocess.Popen([str(self.binary), 'hold'], cwd=self.root,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.addCleanup(self.finish_process, process)
        ready = json.loads(process.stdout.readline())
        self.assertTrue(ready['ready'])
        self.wait_settings((1, 1))
        return process, ready

    @staticmethod
    def finish_process(process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=4)

    def test_normal_completion_preserves_all_original_settings_and_reaps_guard(self):
        for values in [(0, 0), (0, 1), (1, 0), (1, 1)]:
            with self.subTest(values=values):
                self.settings(*values)
                result = subprocess.run([str(self.binary), 'normal'], cwd=self.root,
                                        capture_output=True, text=True, timeout=4, check=True)
                ready, done = map(json.loads, result.stdout.splitlines())
                self.assertEqual(ready['before'], list(values))
                self.assertEqual(done, {'restored': True, 'after': list(values)})
                self.assertEqual(self.observed(), values)
                with self.assertRaises(ProcessLookupError):
                    os.kill(ready['guard_pid'], 0)

    def test_query_death_restores_settings_without_an_sdk_retry(self):
        for sig in [signal.SIGALRM, signal.SIGKILL]:
            with self.subTest(signal=sig):
                process, _ = self.holder()
                process.send_signal(sig)
                process.communicate(timeout=3)
                self.assertEqual(process.returncode, -sig)
                self.wait_settings((0, 0))

    def test_inherited_blocked_and_ignored_alarm_is_reset_before_query(self):
        def block_alarm():
            signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
            signal.signal(signal.SIGALRM, signal.SIG_IGN)
        process = subprocess.Popen([str(self.binary), 'alarm'], cwd=self.root,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, preexec_fn=block_alarm)
        self.addCleanup(self.finish_process, process)
        ready = json.loads(process.stdout.readline())
        self.assertTrue(ready['ready'])
        process.communicate(timeout=4)
        self.assertEqual(process.returncode, -signal.SIGALRM)
        self.wait_settings((0, 0))

    def test_parent_process_group_termination_does_not_kill_restoration(self):
        process, ready = self.holder()
        self.assertNotEqual(os.getpgid(ready['guard_pid']), os.getpgid(process.pid))
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=3)
        self.wait_settings((0, 0))

    def test_guard_deadline_restores_but_late_completion_cannot_report_success(self):
        process, _ = self.holder()
        self.wait_settings((0, 0))
        self.assertIsNone(process.poll())
        process.communicate('D', timeout=3)
        self.assertEqual(process.returncode, 8)

    def test_overlapping_query_cannot_save_temporary_settings_as_its_baseline(self):
        process, _ = self.holder()
        other = subprocess.run([str(self.binary), 'normal'], cwd=self.root,
                               capture_output=True, timeout=3)
        self.assertEqual(other.returncode, 4)
        self.assertEqual(self.observed(), (1, 1))
        process.communicate('D', timeout=3)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(self.observed(), (0, 0))

    def test_eof_and_invalid_completion_restore_settings(self):
        process, _ = self.holder()
        process.communicate(timeout=3)  # EOF terminates the caller without finish.
        self.assertEqual(process.returncode, 5)
        self.wait_settings((0, 0))
        result = subprocess.run([str(self.binary), 'invalid'], cwd=self.root,
                                capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 6)
        self.assertEqual(self.observed(), (0, 0))

    def test_failed_restoration_is_not_reported_as_success(self):
        result = subprocess.run([str(self.binary), 'restore-fail'], cwd=self.root,
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 8)
        self.assertNotIn('"restored":true', result.stdout)
        self.assertEqual(self.observed(), (1, 1))

    def test_lock_symlink_is_rejected_without_changing_settings(self):
        (self.root / 'settings.lock').symlink_to(self.root / 'application')
        result = subprocess.run([str(self.binary), 'normal'], cwd=self.root,
                                capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 4)
        self.assertEqual(self.observed(), (0, 0))


if __name__ == '__main__':
    unittest.main()
