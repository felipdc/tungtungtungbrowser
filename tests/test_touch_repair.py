import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location('prepare_touch_repair', Path(__file__).resolve().parents[1] / 'scripts/prepare_touch_repair.py')
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


def fixture():
    """Synthetic two-page cache and signature, containing no firmware."""
    page_size, page_count, signature_size = 16384, 2, 256
    signature_offset = page_size * page_count
    data = bytearray(signature_offset + signature_size)
    data[:16] = b'dyld_v1  arm64e\0'
    struct.pack_into('<QQ', data, 0x28, signature_offset, signature_size)
    directory_offset = signature_offset + 20
    hash_offset = 88
    code_offset = page_size + 256
    code = repair.RECIPE.spans[0]
    data[code_offset:code_offset + 8] = code.original
    struct.pack_into('>IIIII', data, signature_offset, 0xFADE0CC0, 172, 1, 0, 20)
    struct.pack_into('>9I4B', data, directory_offset, 0xFADE0C02, 152, 0x20400,
                     0, hash_offset, 0, 0, page_count, signature_offset, 32, 2, 0, 14)
    for index in range(page_count):
        at = directory_offset + hash_offset + index * 32
        data[at:at + 32] = hashlib.sha256(data[index * page_size:(index + 1) * page_size]).digest()
    original = bytes(data)
    data[code_offset:code_offset + 8] = code.repaired
    slot_offset = directory_offset + hash_offset + 32
    data[slot_offset:slot_offset + 32] = hashlib.sha256(data[page_size:page_size * 2]).digest()
    repaired = bytes(data)
    recipe = repair.Recipe(
        size=len(data), original_sha256=hashlib.sha256(original).hexdigest(),
        repaired_sha256=hashlib.sha256(repaired).hexdigest(),
        signature_offset=signature_offset, signature_size=signature_size,
        directory_offset=directory_offset, page_size=page_size, page_count=page_count,
        spans=(repair.Span(code_offset, code.original, code.repaired),
               repair.Span(slot_offset, original[slot_offset:slot_offset + 32], repaired[slot_offset:slot_offset + 32])),
    )
    return original, repaired, recipe


class TouchRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.original, self.repaired, self.recipe = fixture()
        self.source.write_bytes(self.original)

    def inspect(self, recipe=None):
        with repair.open_source(self.source) as source:
            return repair.inspect(source, recipe or self.recipe)

    def test_original_and_repaired_round_trip_preserves_source_and_receipts(self):
        self.assertEqual(self.inspect()['state'], 'original')
        applied = repair.prepare(self.source, self.root / 'applied', 'apply', self.recipe)
        result = self.root / 'applied' / repair.CACHE_NAME
        self.assertEqual(result.read_bytes(), self.repaired)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(applied['after']['code_pages_verified'], 2)
        self.assertEqual(applied['bytes_written_in_copy'], 40)
        self.assertFalse(applied['guest_install_performed'])
        self.assertEqual(json.loads((result.parent / 'receipt.json').read_text()), applied)
        self.assertEqual(result.parent.stat().st_mode & 0o777, 0o700)
        reverted = repair.prepare(result, self.root / 'reverted', 'revert', self.recipe)
        self.assertEqual((self.root / 'reverted' / repair.CACHE_NAME).read_bytes(), self.original)
        self.assertEqual(result.read_bytes(), self.repaired)
        self.assertEqual(reverted['after']['sha256'], self.recipe.original_sha256)

    def test_wrong_size_or_hash_never_creates_output(self):
        for data in (self.original[:-1], self.original + b'x', b'x' + self.original[1:]):
            self.source.write_bytes(data)
            with self.assertRaises(ValueError):
                repair.prepare(self.source, self.root / 'out', 'apply', self.recipe)
            self.assertFalse((self.root / 'out').exists())
            self.assertEqual(self.source.read_bytes(), data)

    def test_wrong_direction_and_repeated_apply_do_not_create_outputs(self):
        with self.assertRaisesRegex(ValueError, 'repaired cache'):
            repair.prepare(self.source, self.root / 'out', 'revert', self.recipe)
        self.source.write_bytes(self.repaired)
        with self.assertRaisesRegex(ValueError, 'original cache'):
            repair.prepare(self.source, self.root / 'out', 'apply', self.recipe)
        self.assertFalse((self.root / 'out').exists())

    def test_existing_directory_file_and_symlink_are_preserved(self):
        directory = self.root / 'directory'
        directory.mkdir()
        for path in (directory, self.source, self.root / 'link'):
            if path.name == 'link': path.symlink_to(self.root / 'missing')
            with self.subTest(path=path), self.assertRaises(FileExistsError):
                repair.prepare(self.source, path, 'apply', self.recipe)
        self.assertEqual(list(directory.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertTrue((self.root / 'link').is_symlink())

    def test_source_symlink_directory_fifo_and_exclusive_lock_fail(self):
        link, fifo = self.root / 'link', self.root / 'fifo'
        link.symlink_to(self.source)
        os.mkfifo(fifo)
        for path in (link, fifo, self.root):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                with repair.open_source(path): pass
        with self.source.open('rb') as locked:
            fcntl.flock(locked.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                with repair.open_source(self.source): pass

    def test_unsupported_signature_structures_fail_even_with_matching_file_hash(self):
        r = self.recipe
        changes = [
            (r.signature_offset, '>I', 0),
            (r.signature_offset + 4, '>I', r.signature_size + 1),
            (r.signature_offset + 8, '>I', 17),
            (r.signature_offset + 12, '>I', 0x1000),
            (r.signature_offset + 16, '>I', r.signature_size),
            (r.directory_offset + 4, '>I', r.signature_size),
            (r.directory_offset + 16, '>I', 0),
            (r.directory_offset + 24, '>I', 100),
            (r.directory_offset + 28, '>I', 3),
            (r.directory_offset + 32, '>I', 1),
            (r.directory_offset + 36, 'B', 20),
            (r.directory_offset + 37, 'B', 1),
            (r.directory_offset + 39, 'B', 12),
        ]
        for offset, fmt, value in changes:
            data = bytearray(self.original)
            struct.pack_into(fmt, data, offset, value)
            self.source.write_bytes(data)
            spec = r._replace(original_sha256=hashlib.sha256(data).hexdigest())
            with self.subTest(offset=offset), self.assertRaises(ValueError): self.inspect(spec)

    def test_verifies_unchanged_code_pages_as_well_as_repair_page(self):
        data = bytearray(self.original)
        data[500] ^= 1  # Page zero, outside the repair page.
        self.source.write_bytes(data)
        spec = self.recipe._replace(original_sha256=hashlib.sha256(data).hexdigest())
        with self.assertRaisesRegex(ValueError, 'page 0 failed'): self.inspect(spec)

    def test_code_only_repair_cannot_pass_signature_check(self):
        data = bytearray(self.original)
        code, slot = self.recipe.spans
        data[code.offset:code.offset + 8] = code.repaired
        self.source.write_bytes(data)
        spec = self.recipe._replace(original_sha256=hashlib.sha256(data).hexdigest(),
                                    spans=(code._replace(original=code.repaired), slot))
        with self.assertRaisesRegex(ValueError, 'page 1 failed'): self.inspect(spec)

    def test_signature_slot_must_correspond_to_changed_page(self):
        code, slot = self.recipe.spans
        bad = self.recipe._replace(spans=(code._replace(offset=code.offset - self.recipe.page_size), slot))
        with repair.open_source(self.source) as source, self.assertRaisesRegex(ValueError, 'does not cover'):
            repair.signature_layout(source, bad)

    def test_output_hash_mismatch_keeps_partial_and_never_issues_receipt(self):
        bad = self.recipe._replace(repaired_sha256='0' * 64)
        output = self.root / 'bad'
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            repair.prepare(self.source, output, 'apply', bad)
        self.assertTrue((output / (repair.CACHE_NAME + '.partial')).exists())
        self.assertFalse((output / repair.CACHE_NAME).exists())
        self.assertFalse((output / 'receipt.json').exists())
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_source_race_is_detected_before_output_publication(self):
        original_inspect = repair.inspect
        calls = []
        def mutate_after_output_verified(source, recipe):
            result = original_inspect(source, recipe)
            calls.append(result['state'])
            if len(calls) == 2:
                self.source.write_bytes(b'x' + self.original[1:])
            return result
        with patch.object(repair, 'inspect', side_effect=mutate_after_output_verified), self.assertRaisesRegex(ValueError, 'SHA-256'):
            repair.prepare(self.source, self.root / 'race', 'apply', self.recipe)
        self.assertFalse((self.root / 'race' / repair.CACHE_NAME).exists())
        self.assertFalse((self.root / 'race' / 'receipt.json').exists())

    def test_fsync_failure_preserves_source_and_does_not_report_complete(self):
        real_sync = os.fsync
        def fail_regular_file(fd):
            if os.fstat(fd).st_size == self.recipe.size: raise OSError('simulated sync failure')
            return real_sync(fd)
        with patch.object(repair.os, 'fsync', side_effect=fail_regular_file), self.assertRaises(OSError):
            repair.prepare(self.source, self.root / 'sync', 'apply', self.recipe)
        self.assertFalse((self.root / 'sync' / 'receipt.json').exists())
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_output_inside_repository_is_refused_before_directory_creation(self):
        with patch.object(repair, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'outside'):
            repair.prepare(self.source, self.root / 'out', 'apply', self.recipe)
        self.assertFalse((self.root / 'out').exists())

    def test_cli_rejects_unsupported_source_and_invalid_arguments(self):
        for args, code in ((['inspect', str(self.source)], 1),
                           (['apply', str(self.source)], 2),
                           (['inspect', str(self.source), '--output', str(self.root / 'out')], 2)):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                repair.main(args)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.source.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
