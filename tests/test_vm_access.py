"""Run the filesystem check against real mounts/files without a guest runtime."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/run_vm.py'


class VMAccessTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.vm = self.root / 'vm'
        self.vm.mkdir()
        self.source = self.root / 'runtime'
        self.source.mkdir()
        for name in ('Disk.img', 'nvram.bin', 'ecid', 'config.plist'):
            (self.vm / name).write_bytes(b'access-test fixture\n')
        self.launcher = self.source / 'run-vphone.sh'
        self.launcher.write_text('#!/bin/sh\ntouch "$1/emulator-started"\n')
        self.launcher.chmod(0o755)

    def check(self):
        return subprocess.run([sys.executable, str(SCRIPT), str(self.vm),
                               '--source', str(self.source), '--check-access'],
                              capture_output=True, text=True, timeout=10)

    def test_success_preserves_files_and_does_not_start_emulator(self):
        (self.vm / 'cache/mesa').mkdir(parents=True)
        (self.vm / 'launch.json').write_text('existing launch evidence\n')
        before = {p.relative_to(self.vm): p.read_bytes()
                  for p in self.vm.rglob('*') if p.is_file()}
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['filesystem_access'], 'passed')
        self.assertFalse(json.loads(result.stdout)['guest_started'])
        self.assertEqual({p.relative_to(self.vm): p.read_bytes()
                          for p in self.vm.rglob('*') if p.is_file()}, before)

    def test_missing_disk_fails_with_path_and_no_traceback(self):
        (self.vm / 'Disk.img').unlink()
        result = self.check()
        self.assertEqual(result.returncode, 2)
        self.assertIn(str(self.vm / 'Disk.img'), result.stderr)
        self.assertIn('Runtime UID=', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse((self.vm / 'emulator-started').exists())

    @unittest.skipIf(os.geteuid() == 0, 'unrestricted root may bypass file modes')
    def test_unwritable_disk_fails_without_modifying_it(self):
        disk = self.vm / 'Disk.img'
        original = disk.read_bytes()
        disk.chmod(0o400)
        result = self.check()
        self.assertEqual(result.returncode, 2)
        self.assertIn('open for reading and writing', result.stderr)
        self.assertIn('bind-mount ownership', result.stderr)
        self.assertEqual(disk.read_bytes(), original)

    @unittest.skipIf(os.geteuid() == 0, 'unrestricted root may bypass directory modes')
    def test_unwritable_cache_fails_before_guest_start(self):
        cache = self.vm / 'cache'
        cache.mkdir(mode=0o500)
        self.addCleanup(cache.chmod, 0o700)
        result = self.check()
        self.assertEqual(result.returncode, 2)
        self.assertIn(str(cache), result.stderr)
        self.assertIn('temporary file', result.stderr)
        self.assertFalse((self.vm / 'emulator-started').exists())

    def test_nonexecutable_launcher_fails(self):
        self.launcher.chmod(0o644)
        result = self.check()
        self.assertEqual(result.returncode, 2)
        self.assertIn('runtime launcher is not executable', result.stderr)


if __name__ == '__main__':
    unittest.main()
