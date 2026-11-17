"""Run the filesystem check against real mounts/files without a guest runtime."""
import json
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from scripts import run_vm
from tungtungtungbrowser.guest import GuestError


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/run_vm.py'


class VMAccessTest(unittest.TestCase):
    def test_launcher_passes_and_records_explicit_clock_startup_budget(self):
        class OfflineGuest:
            def health(self):
                raise GuestError('fixture has no guest')

        # A real short-lived child exercises launcher/worker lifecycle without
        # QEMU, firmware or a connection to an actual guest.
        self.launcher.write_text('#!/bin/sh\nsleep 0.15\n')
        (self.root/'leases').mkdir(mode=0o700)
        argv = ['run_vm.py', str(self.vm), '--source', str(self.source),
                '--cid', '55555', '--lease-dir', str(self.root/'leases'),
                '--clock-interval-seconds', '10', '--clock-startup-timeout-seconds', '1500']
        with patch.object(sys, 'argv', argv), patch.object(run_vm, 'Guest', return_value=OfflineGuest()), \
                patch.object(run_vm, 'select_manifest', return_value=self.root/'unused-icd.json'), \
                patch.object(run_vm.signal, 'signal'), patch.dict(os.environ, {'REIMS_VGPU_CENSUS_DIR': ''}), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as caught:
            run_vm.main()
        self.assertEqual(caught.exception.code, 0)
        launch = json.loads((self.vm/'launch.json').read_text())
        self.assertEqual(launch['clock_maintenance']['startup_timeout_seconds'], 1500)
        state = json.loads(Path(launch['clock_maintenance']['state_file']).read_text())
        self.assertEqual(state['startup_timeout_seconds'], 1500)
        self.assertEqual(state['offers'], 0)
        self.assertEqual(state['status'], 'stopped')

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

    def check(self, diagnostics=None):
        environment = os.environ.copy()
        environment.pop('REIMS_VGPU_CENSUS_DIR', None)
        if diagnostics is not None:
            environment['REIMS_VGPU_CENSUS_DIR'] = str(diagnostics)
        return subprocess.run([sys.executable, str(SCRIPT), str(self.vm),
                               '--source', str(self.source), '--check-access'],
                              env=environment, capture_output=True, text=True, timeout=10)

    def test_missing_diagnostic_directory_checks_parent_without_creating_it(self):
        target = self.vm / 'diagnostics/new-census'
        result = self.check(target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(target.parent.exists())
        self.assertFalse((self.vm / 'emulator-started').exists())

    def test_diagnostic_destination_below_file_is_rejected(self):
        result = self.check(self.vm / 'Disk.img/census')
        self.assertEqual(result.returncode, 2)
        self.assertIn('not a directory', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse((self.vm / 'emulator-started').exists())

    def test_relative_diagnostic_destination_is_rejected_before_launch(self):
        result = self.check('census')
        self.assertEqual(result.returncode, 2)
        self.assertIn('must be absolute', result.stderr)
        self.assertFalse((self.vm / 'emulator-started').exists())

    @unittest.skipIf(os.geteuid() == 0, 'unrestricted root may bypass directory modes')
    def test_diagnostic_destination_under_read_only_parent_is_rejected(self):
        parent = self.root / 'parent'
        parent.mkdir(mode=0o500)
        self.addCleanup(parent.chmod, 0o700)
        result = self.check(parent / 'census')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Cannot write renderer diagnostics', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse((parent / 'census').exists())
        self.assertFalse((self.vm / 'emulator-started').exists())

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
