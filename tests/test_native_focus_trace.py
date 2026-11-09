"""Distinguish successful restoration, failed restoration and a lost final reply."""
import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('cc'), 'requires a C compiler')
class NativeFocusTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.build.name) / 'focus-guard'
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                        str(Path(__file__).with_name('native_focus_guard_harness.c')),
                        '-o', str(cls.binary)], check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def execute(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            for name in ('application', 'automation'):
                Path(directory, name).write_text('0')
            result = subprocess.run([str(self.binary), mode], cwd=directory,
                                    capture_output=True, text=True, timeout=7)
            ready, trace = [json.loads(line) for line in result.stdout.splitlines()][:2]
            self.assertEqual(trace['begin_write'], 1)
            self.assertEqual(trace['begin_received'], 1)
            self.assertEqual(trace['begin_valid'], 1)
            if mode != 'trace-write-fail':
                self.assertEqual(trace['finish_write'], 1)
            self.assertEqual(trace['wait_pid'], ready['guard_pid'])
            self.assertEqual(trace['wait_errno'], 0)
            for key in ('begin_seconds', 'query_seconds', 'finish_receive_seconds', 'reap_seconds'):
                self.assertGreaterEqual(trace[key], 0)
            with self.assertRaises(ProcessLookupError):
                os.kill(ready['guard_pid'], 0)
            state = tuple(int(Path(directory, name).read_text())
                          for name in ('application', 'automation'))
            return result, trace, state

    def test_normal_reply_is_received_and_child_reaped(self):
        result, trace, state = self.execute('trace-normal')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(state, (0, 0))
        self.assertEqual(trace['finish_received'], 1)
        self.assertEqual(trace['finish_valid'], 1)
        self.assertEqual(trace['finish_read_bytes'], 24)
        self.assertEqual(trace['final_status'], 0)
        self.assertEqual(trace['child_status'], 0)

    def test_failed_restoration_has_a_final_error_frame(self):
        result, trace, state = self.execute('trace-restore-fail')
        self.assertEqual(result.returncode, 8)
        self.assertEqual(state, (1, 1))
        self.assertEqual(trace['finish_received'], 1)
        self.assertEqual(trace['finish_valid'], 0)
        self.assertEqual(trace['final_status'], 3)
        self.assertEqual(trace['finish_read_bytes'], 24)
        self.assertEqual(trace['child_status'], 11 << 8)

    def test_failed_finish_write_does_not_reuse_the_begin_reply(self):
        result, trace, state = self.execute('trace-write-fail')
        self.assertEqual(result.returncode, 8)
        self.assertEqual(state, (0, 0))
        self.assertEqual(trace['finish_write'], -1)
        self.assertEqual(trace['finish_errno'], errno.EBADF)
        self.assertEqual(trace['finish_received'], 0)
        self.assertEqual(trace['finish_valid'], 0)
        self.assertEqual(trace['finish_poll_result'], 0)
        self.assertEqual(trace['finish_read_bytes'], -2)
        # The parent closes its reply pipe after the failed write. The guard
        # restores on EOF, then reports its undeliverable final reply as 12.
        self.assertEqual(trace['child_status'], 12 << 8)

    def test_late_restoration_is_not_misreported_as_received_success(self):
        result, trace, state = self.execute('trace-restore-delay')
        self.assertEqual(result.returncode, 8)
        self.assertEqual(state, (0, 0))
        self.assertEqual(trace['finish_received'], 0)
        self.assertEqual(trace['finish_valid'], 0)
        self.assertEqual(trace['finish_poll_result'], 0)
        self.assertEqual(trace['finish_read_bytes'], -2)
        self.assertEqual(trace['child_status'], 12 << 8)
        self.assertGreater(trace['finish_receive_seconds'], 3.8)


if __name__ == '__main__':
    unittest.main()
