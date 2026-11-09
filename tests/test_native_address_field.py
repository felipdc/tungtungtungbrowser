from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('cc'), 'requires a C compiler')
class NativeAddressFieldTests(unittest.TestCase):
    def test_complete_unique_scan_and_rejection_after_an_early_match(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'address-field'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                            str(Path(__file__).with_name('native_address_field_harness.c')),
                            '-o', str(binary)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=3)
        self.assertEqual(result.stdout, '19 address lookup scenarios passed\n')


if __name__ == '__main__':
    unittest.main()
