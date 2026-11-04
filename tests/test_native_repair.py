import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import touch_repair_build as build

SPEC = importlib.util.spec_from_file_location('repair_fixture', Path(__file__).with_name('test_touch_repair.py'))
fixture_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture_module)


class NativeRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler_directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.compiler_directory.cleanup)
        directory = Path(cls.compiler_directory.name)
        cls.original, cls.repaired, cls.recipe = fixture_module.fixture()
        (directory / 'touch_repair_recipe.h').write_text(build.recipe_header(cls.recipe))
        cls.binary = directory / 'check'
        subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-I' + str(directory), str(ROOT / 'native/check_touch_repair.c'),
                        '-ldl', '-o', str(cls.binary)], check=True, capture_output=True, text=True)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cache_directory = self.root / 'System/Cryptexes/OS/System/Library/Caches/com.apple.dyld'
        self.cache_directory.mkdir(parents=True)
        (self.root / 'private/var/root').mkdir(parents=True)
        self.cache = self.cache_directory / build.CACHE_NAME
        self.cache.write_bytes(self.original)
        self.cache.chmod(0o444)
        self.backups = self.root / 'private/var/root/ttt-touch-repair-v1'
        self.lock = self.backups.with_suffix('.lock')

    def invoke(self, operation='apply', attempt='a' * 32, fault='none', success=True):
        command = [str(self.binary), str(self.root), fault, operation]
        if operation != 'inspect': command.append(attempt)
        p = subprocess.run(command, capture_output=True, text=True, timeout=10)
        events = [json.loads(line) for line in p.stdout.splitlines()]
        self.assertEqual(p.returncode == 0, success, (p.stdout, p.stderr))
        self.assertEqual(p.stderr, '')
        self.assertTrue(events, p)
        return events

    def test_inspection_is_read_only_and_rejects_unknown_data(self):
        before = set(self.root.rglob('*'))
        r = self.invoke('inspect')[-1]
        self.assertEqual(r['status'], 'verified')
        self.assertEqual(r['sha256'], self.recipe.original_sha256)
        self.assertFalse(r['replacement_performed'])
        self.assertEqual(set(self.root.rglob('*')), before)
        self.cache.chmod(0o600)
        self.cache.write_bytes(b'x' + self.original[1:])
        self.assertEqual(self.invoke('inspect', success=False)[-1]['stage'], 'unsupported_cache')
        self.assertFalse(self.backups.exists())
        self.assertFalse(self.lock.exists())

    def test_atomic_round_trip_keeps_old_inode_and_complete_backups(self):
        old = self.cache.stat()
        with self.cache.open('rb') as held:
            r = self.invoke()[-1]
            self.assertEqual(r['status'], 'complete')
            self.assertEqual(r['stage'], 'reboot_required')
            self.assertTrue(r['replacement_performed'])
            self.assertEqual(r['sha256'], self.recipe.repaired_sha256)
            self.assertEqual(held.read(), self.original)
        self.assertNotEqual(self.cache.stat().st_ino, old.st_ino)
        self.assertEqual(self.cache.read_bytes(), self.repaired)
        for key in ('st_uid', 'st_gid', 'st_mode'):
            self.assertEqual(getattr(self.cache.stat(), key), getattr(old, key))
        self.assertEqual((self.backups / ('a' * 32) / 'backup.cache').read_bytes(), self.original)
        self.assertEqual(list(self.cache_directory.glob('*.staging')), [])
        r = self.invoke('revert', 'b' * 32)[-1]
        self.assertEqual(r['sha256'], self.recipe.original_sha256)
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertEqual((self.backups / ('b' * 32) / 'backup.cache').read_bytes(), self.repaired)
        for attempt in ['a' * 32, 'b' * 32]:
            journal = self.backups / attempt / 'events.jsonl'
            records = [json.loads(x) for x in journal.read_text().splitlines()]
            self.assertEqual([x['stage'] for x in records],
                             ['preparing', 'backup_verified', 'ready_to_replace', 'reboot_required'])
            self.assertEqual(records[-1]['status'], 'complete')

    def test_existing_attempt_and_wrong_preimage_are_never_overwritten(self):
        self.invoke()
        journal = self.backups / ('a' * 32) / 'events.jsonl'
        before = journal.read_bytes()
        self.assertEqual(self.invoke(success=False)[-1]['stage'], 'expected_preimage')
        self.assertEqual(self.invoke('revert', success=False)[-1]['stage'], 'exclusive_attempt')
        self.assertEqual(journal.read_bytes(), before)
        self.assertEqual(self.cache.read_bytes(), self.repaired)

    def test_short_writes_are_completed(self):
        self.invoke(fault='short_write')
        self.assertEqual(self.cache.read_bytes(), self.repaired)

    def test_failures_before_rename_preserve_original_inode_and_bytes(self):
        cases = ['backup_write', 'backup_sync', 'staging_write', 'signature_write',
                 'metadata', 'metadata_mode', 'metadata_corrupt', 'rename', 'journal_before']
        before_inode = self.cache.stat().st_ino
        for number, fault in enumerate(cases):
            with self.subTest(fault=fault):
                r = self.invoke(attempt=f'{number:032x}', fault=fault, success=False)[-1]
                self.assertEqual(r['status'], 'failed')
                self.assertFalse(r['replacement_performed'])
                self.assertEqual(self.cache.read_bytes(), self.original)
                self.assertEqual(self.cache.stat().st_ino, before_inode)
                if fault not in ('backup_write',):
                    self.assertEqual((self.backups / f'{number:032x}' / 'backup.cache').read_bytes(), self.original)

    def test_failed_postcommit_steps_report_replacement_and_allow_explicit_revert(self):
        for number, fault in enumerate(['sync_after_replace', 'journal_after']):
            with self.subTest(fault=fault):
                r = self.invoke(attempt=f'{number:032x}', fault=fault, success=False)[-1]
                self.assertTrue(r['replacement_performed'])
                self.assertEqual(self.cache.read_bytes(), self.repaired)
                self.assertEqual(self.invoke('inspect')[-1]['sha256'], self.recipe.repaired_sha256)
                self.invoke('revert', f'{number + 10:032x}')
                self.assertEqual(self.cache.read_bytes(), self.original)

    def test_unexpected_live_changes_are_preserved_not_replaced_or_auto_reverted(self):
        self.cache.chmod(0o600)
        r = self.invoke(fault='source_changed', success=False)[-1]
        self.assertEqual(r['stage'], 'source_changed')
        self.assertFalse(r['replacement_performed'])
        self.assertEqual(self.cache.read_bytes(), b'\xff' + self.original[1:])
        self.assertEqual((self.backups / ('a' * 32) / 'backup.cache').read_bytes(), self.original)

    def test_corruption_after_replacement_retains_failure_and_backup(self):
        self.cache.chmod(0o600)
        r = self.invoke(fault='installed_corrupt', success=False)[-1]
        self.assertEqual(r['stage'], 'installed_verification')
        self.assertTrue(r['replacement_performed'])
        self.assertEqual(self.cache.read_bytes(), b'\xff' + self.repaired[1:])
        self.assertEqual((self.backups / ('a' * 32) / 'backup.cache').read_bytes(), self.original)
        self.invoke('revert', 'b' * 32, success=False)
        self.assertFalse((self.backups / ('b' * 32)).exists())

    def test_unexpected_live_metadata_change_is_preserved(self):
        inode = self.cache.stat().st_ino
        r = self.invoke(fault='source_metadata_changed', success=False)[-1]
        self.assertEqual(r['stage'], 'source_changed')
        self.assertFalse(r['replacement_performed'])
        self.assertEqual(self.cache.stat().st_ino, inode)
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cache.read_bytes(), self.original)

    def test_busy_lock_sends_no_writes(self):
        with self.lock.open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.invoke(success=False)[-1]['stage'], 'busy')
            self.assertEqual(self.invoke('inspect', success=False)[-1]['stage'], 'busy')
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertFalse(self.backups.exists())

    def test_symlink_and_hardlink_cache_are_rejected(self):
        other = self.cache.with_name('other')
        self.cache.rename(other)
        self.cache.symlink_to(other)
        self.invoke(success=False)
        self.cache.unlink()
        os.link(other, self.cache)
        self.invoke(success=False)
        self.assertEqual(other.read_bytes(), self.original)
        self.assertFalse(self.backups.exists())

    def test_symlink_cache_directory_is_rejected_before_backup_or_replacement(self):
        actual = self.root / 'actual-cache'
        self.cache_directory.rename(actual)
        self.cache_directory.symlink_to(actual, target_is_directory=True)
        before = self.cache.stat().st_ino
        r = self.invoke(success=False)[-1]
        self.assertEqual(r['stage'], 'cache_directory')
        self.assertFalse(r['replacement_performed'])
        self.assertEqual(self.cache.stat().st_ino, before)
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertFalse((self.backups / ('a' * 32)).exists())

    def test_missing_boot_identity_and_invalid_attempt_fail_before_files(self):
        before = set(self.root.rglob('*'))
        self.invoke(fault='boot_identity', success=False)
        for attempt in ['', 'x', '../outside', 'g' * 32, 'a' * 33, '"' * 32]:
            self.assertEqual(self.invoke(attempt=attempt, success=False)[-1]['stage'], 'attempt_id')
        self.assertEqual(set(self.root.rglob('*')), before)


if __name__ == '__main__':
    unittest.main()
