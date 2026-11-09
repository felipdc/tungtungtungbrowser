from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

@unittest.skipUnless(shutil.which('cc'), 'requires a C compiler')
class NativeFocusResolutionTests(unittest.TestCase):
    def test_bounded_resolution_preserves_failed_and_partial_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'resolve'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                str(Path(__file__).with_name('native_focus_resolution_harness.c')),
                '-o', str(binary)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=3)
        self.assertEqual(result.stdout, '21 guarded resolution scenarios passed\n')
