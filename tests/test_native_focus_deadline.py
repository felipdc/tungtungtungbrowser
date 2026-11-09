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
class NativeFocusDeadlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.build.name) / 'focus-deadline'
        result = subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                                 '-D_FORTIFY_SOURCE=3',
                                 str(Path(__file__).with_name('native_focus_deadline_harness.c')),
                                 '-o', str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            cls.build.cleanup()
            raise RuntimeError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def run_case(self, mode, delays, expected, blocked=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('application', 'automation'):
                (root / name).write_text('0')
            for name, milliseconds in zip(('setup', 'query', 'restore', 'reap'), delays):
                (root / (name + '-ms')).write_text(str(milliseconds))

            def block_alarm():
                signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
                signal.signal(signal.SIGALRM, signal.SIG_IGN)

            started = time.monotonic()
            process = subprocess.Popen([str(self.binary), mode], cwd=root,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       preexec_fn=block_alarm if blocked else None)
            try:
                stdout, stderr = process.communicate(timeout=6)
                elapsed = time.monotonic() - started
                self.assertEqual(process.returncode, expected, stderr)
                guard_pid = json.loads(stdout.splitlines()[0])['guard_pid']
                # A dead caller must not end its guard. Wait for independent
                # restoration and guard return, including deliberate slow reap.
                end = time.monotonic() + 5
                while not (root / 'guard-finished').exists() and time.monotonic() < end:
                    time.sleep(0.01)
                self.assertTrue((root / 'guard-finished').exists())
                self.assertEqual([(root / name).read_text() for name in
                                  ('application', 'automation')], ['0', '0'])
                if expected == 0:
                    self.assertEqual(json.loads(stdout.splitlines()[1]),
                                     {'restored': True, 'after': [0, 0]})
                    with self.assertRaises(ProcessLookupError):
                        os.kill(guard_pid, 0)
                else:
                    self.assertNotIn('"restored":true', stdout)
                return elapsed
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=2)

    def test_slow_setup_cannot_spend_query_and_restoration_allowances(self):
        delays = (800, 800, 800, 0)
        self.run_case('legacy', delays, -signal.SIGALRM)
        self.assertGreater(self.run_case('phased', delays, 0), 2.35)

    def test_each_hung_phase_is_bounded_and_guard_restores_after_caller_death(self):
        for delays in [(3000, 0, 0, 0), (0, 3000, 0, 0),
                       (0, 0, 3000, 0), (0, 0, 0, 3000)]:
            with self.subTest(delays=delays):
                self.run_case('phased', delays, -signal.SIGALRM)

    def test_inherited_signal_mask_cannot_disable_phase_deadlines(self):
        self.run_case('phased', (0, 3000, 0, 0), -signal.SIGALRM, blocked=True)
