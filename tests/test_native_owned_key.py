from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('cc'), 'requires C compiler')
class OwnedKeyTests(unittest.TestCase):
    def test_stale_process_or_boot_is_rejected_before_key_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'owned-key'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                            str(Path(__file__).with_name('native_owned_key_harness.c')),
                            '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, timeout=5)
