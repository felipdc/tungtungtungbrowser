import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AccessibilityStateTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'C compiler is required')
    def test_native_state_reader_rejects_unknown_states_without_a_tree_or_setters(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'check')
            subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                            str(ROOT / 'native/check_accessibility_state.c'), '-o', binary],
                           check=True, capture_output=True, text=True)
            result = subprocess.run([binary], check=True, capture_output=True, text=True, timeout=5)
        values = [json.loads(line) for line in result.stdout.splitlines() if line]
        self.assertEqual(len(values), 4)
        self.assertEqual({(v['application_accessibility'], v['automation']) for v in values},
                         {(False, False), (False, True), (True, False), (True, True)})
        for value in values:
            self.assertEqual(value['boot_uuid'], '12345678-1234-1234-1234-123456789ABC')
            self.assertEqual(value['uid'], 501)
            self.assertFalse(value['settings_written'])
            self.assertFalse(value['ax_tree_queried'])
