from pathlib import Path
import hashlib
import importlib.util
import json
import shutil
import struct
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('cc'), 'requires a C compiler')
class NativeFocusAttributeBatchTests(unittest.TestCase):
    def test_batch_ownership_and_error_paths(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / 'focus-attributes'
            result = subprocess.run([
                'cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                '-I', str(root / 'native'),
                str(root / 'tests/native_focus_attributes_harness.c'),
                '-o', str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)


class NativeFocusBatchManifestTests(unittest.TestCase):
    def test_missing_or_changed_batch_header_rejects_the_build(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            'focus_batch_installer', root / 'scripts/install_focused_element.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        names = ('focused_element.c', 'focus_deadline.h', 'focus_guard.h',
                 'focus_guard_trace.h', 'address_field.h', 'focus_resolution.h',
                 'focused_element_libSystem.tbd', 'focused_element_entitlements.plist',
                 'focus_attributes.h')
        sources = {name: hashlib.sha256((root / 'native' / name).read_bytes()).hexdigest()
                   for name in names}
        binary = struct.pack('<4I', 0xFEEDFACF, 0x100000C, 0, 2) + bytes(16)
        digest = hashlib.sha256(binary).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'focused-element').write_bytes(binary)
            manifest = {'protocol': 1, 'binary': 'focused-element',
                        'binary_sha256': digest, 'sources': sources}
            path = directory / 'build.json'
            path.write_text(json.dumps(manifest))
            self.assertEqual(installer.read_build(directory), (binary, digest))
            for changed in ({k: v for k, v in sources.items() if k != 'focus_attributes.h'},
                            sources | {'focus_attributes.h': '0' * 64}):
                path.write_text(json.dumps(manifest | {'sources': changed}))
                with self.assertRaisesRegex(ValueError, 'rebuild'):
                    installer.read_build(directory)


if __name__ == '__main__':
    unittest.main()
