import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest


class NavigationGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.build.name) / 'navigation-guard'
        subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-function', '-D_FORTIFY_SOURCE=3',
                        str(Path(__file__).with_name('native_navigation_guard_harness.c')),
                        '-o', str(cls.binary)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.settings((0, 0))

    def settings(self, values):
        for name, value in zip(('application', 'automation'), values):
            (self.root / name).write_text(str(value))

    def observed(self):
        return tuple(int((self.root / n).read_text()) for n in ('application', 'automation'))

    def holder(self):
        p = subprocess.Popen([str(self.binary), 'hold'], cwd=self.root, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.addCleanup(self.finish, p)
        ready = json.loads(p.stdout.readline())
        self.assertEqual(self.observed(), (1, 1))
        return p, ready

    @staticmethod
    def finish(p):
        if p.poll() is None:
            p.kill()
        p.communicate(timeout=4)

    def send(self, p, command):
        p.stdin.write(command); p.stdin.flush()
        return json.loads(p.stdout.readline())

    def wait_restored(self, expected=(0, 0)):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                if self.observed() == expected:
                    return
            except ValueError:
                pass
            time.sleep(.01)
        self.assertEqual(self.observed(), expected)

    def test_repeated_navigation_and_finish_preserve_every_original_combination(self):
        for original in ((0, 0), (0, 1), (1, 0), (1, 1)):
            with self.subTest(original=original):
                self.settings(original)
                p, ready = self.holder()
                self.assertEqual(ready['before'], list(original))
                for _ in range(3):
                    self.assertTrue(self.send(p, 'P')['ok']); self.assertEqual(self.observed(), original)
                    self.assertTrue(self.send(p, 'H')['ok']); self.assertEqual(self.observed(), original)
                    self.assertTrue(self.send(p, 'A')['ok']); self.assertEqual(self.observed(), (1, 1))
                self.assertTrue(self.send(p, 'D')['closed'])
                p.communicate(timeout=3); self.assertEqual(p.returncode, 0)
                self.assertEqual(self.observed(), original)
                with self.assertRaises(ProcessLookupError): os.kill(ready['guard_pid'], 0)

    def test_owner_death_restores_both_active_and_paused_lease(self):
        for paused in (False, True):
            p, _ = self.holder()
            if paused: self.assertTrue(self.send(p, 'P')['ok'])
            os.killpg(p.pid, signal.SIGKILL)
            p.communicate(timeout=3); self.wait_restored()
            # The lock may be held briefly after the final preference write.
            time.sleep(.03)

    def test_read_requests_cannot_extend_absolute_deadline(self):
        p, _ = self.holder(); start = time.monotonic()
        while True:
            result = self.send(p, 'H')
            if not result['ok']: break
            time.sleep(.1)
            self.assertLess(time.monotonic() - start, 2.5)
        p.communicate(timeout=3); self.assertEqual(p.returncode, 7)
        # The deadline may close the input before our next read request. That
        # outcome is unknown (-1), never a cached successful health response.
        self.assertIn(result['status'], (-1, 4)); self.wait_restored()

    def test_pause_retains_exclusive_ownership(self):
        p, _ = self.holder(); self.assertTrue(self.send(p, 'P')['ok'])
        other = subprocess.run([str(self.binary), 'hold'], cwd=self.root, input='', capture_output=True, timeout=3)
        self.assertEqual(other.returncode, 4); self.assertEqual(self.observed(), (0, 0))
        self.assertTrue(self.send(p, 'D')['closed'])

    def test_duplicate_transition_is_terminal_and_not_replayed(self):
        p, _ = self.holder(); self.assertTrue(self.send(p, 'P')['ok'])
        writes = (self.root / 'writes').read_bytes()
        result = self.send(p, 'P'); self.assertFalse(result['ok']); self.assertEqual(result['status'], 6)
        p.communicate(timeout=3); self.assertEqual((self.root / 'writes').read_bytes(), writes)
        self.wait_restored()

    def test_failed_pause_is_reported_without_a_second_restoration_attempt(self):
        p, _ = self.holder(); (self.root / 'fail-restore').touch()
        result = self.send(p, 'P'); self.assertFalse(result['ok']); self.assertEqual(result['status'], 3)
        p.communicate(timeout=3); self.assertEqual(p.returncode, 7)
        writes = (self.root / 'writes').read_text().splitlines()
        self.assertEqual(writes.count('automation0'), 1); self.assertEqual(writes.count('application0'), 1)

    def test_failed_reactivation_restores_original_without_claiming_active(self):
        p, _ = self.holder(); self.assertTrue(self.send(p, 'P')['ok']); (self.root / 'fail-enable').touch()
        result = self.send(p, 'A'); self.assertFalse(result['ok']); self.assertEqual(result['status'], 2)
        p.communicate(timeout=3); self.wait_restored()

    def test_unexpected_live_state_is_rejected_before_enabling(self):
        p, _ = self.holder(); self.assertTrue(self.send(p, 'P')['ok']); self.settings((1, 0))
        writes = (self.root / 'writes').read_bytes()
        result = self.send(p, 'A'); self.assertFalse(result['ok']); self.assertEqual(result['status'], 3)
        p.communicate(timeout=3)
        self.assertEqual((self.root / 'writes').read_bytes(), writes)
        self.assertEqual(self.observed(), (1, 0))  # Do not overwrite a foreign paused-state change.

    def test_eof_and_invalid_command_restore_without_input(self):
        for command in ('', 'X'):
            p, _ = self.holder()
            p.communicate(command, timeout=3); self.assertIn(p.returncode, (5, 7)); self.wait_restored()
            time.sleep(.03)


if __name__ == '__main__': unittest.main()
