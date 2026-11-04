"""Allocation lifetimes, sparse coverage and preservation of offline evidence."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.analyze_graphics_memory import FULL_MARKER, PREFIX, summarize

MIB = 1 << 20


def block(verb='made a pooled block of images', handle=1, size=64*MIB, memory_type=0):
    return f'{PREFIX}{verb}, memory {handle:#x} of type {memory_type}: {size} bytes, device address unknown\n'


def placement(offset=0, size=4*MIB, handle=1, kind='image', returned=False, memory_type=0):
    verb = ('returned an ' if kind == 'image' else 'returned a ') if returned else 'placed a Private '
    return f'{PREFIX}{verb}{kind} of {size} bytes at offset {offset:#x} of memory {handle:#x} of type {memory_type}\n'


def full():
    return PREFIX + FULL_MARKER + '\n'


class GraphicsMemoryTest(unittest.TestCase):
    def test_sparse_reports_unknown_bytes_and_gaps_not_reclaimable_memory(self):
        r = summarize([block(), placement(0, 24*MIB), placement(32*MIB, 24*MIB)])
        self.assertEqual(r['pooled']['bytes'], 64*MIB)
        self.assertEqual(r['pooled']['logged_live_placement_bytes'], 48*MIB)
        self.assertEqual(r['pooled']['unaccounted_bytes'], 16*MIB)
        self.assertEqual(r['pooled']['rows'][0]['largest_unaccounted_range_bytes'], 8*MIB)
        self.assertEqual(r['coverage']['minimum_pooled_placement_bytes'], 4*MIB)
        self.assertFalse(r['coverage']['capture_completeness_verified'])
        self.assertIsNone(r['reclaimable_bytes'])

    def test_full_capture_includes_small_live_placements_and_reused_offsets(self):
        r = summarize([full(), block(size=64), placement(size=8), placement(offset=8, size=16),
                       placement(size=8, returned=True), placement(size=4), full()])
        self.assertEqual(r['pooled']['logged_live_placement_bytes'], 20)
        self.assertEqual(r['pooled']['unaccounted_bytes'], 44)
        self.assertEqual(r['pooled']['rows'][0]['largest_unaccounted_range_bytes'], 40)
        self.assertTrue(r['coverage']['declared_by_log'])
        self.assertFalse(r['coverage']['capture_completeness_verified'])
        self.assertEqual(r['peaks']['logged_live_placement_bytes'], 24)

    def test_handle_reuse_starts_a_new_lifetime_and_peaks_are_not_summed(self):
        r = summarize([block(), placement(), placement(returned=True), block('freed a block'),
                       block(handle=1, size=16*MIB, memory_type=2),
                       placement(handle=1, size=8*MIB, memory_type=2)])
        self.assertEqual(r['pooled']['bytes'], 16*MIB)
        self.assertEqual(r['peaks']['pooled_bytes'], 64*MIB)
        self.assertEqual(r['peaks']['logged_live_placement_bytes'], 8*MIB)
        self.assertEqual(r['pooled']['rows'][0]['memory_type'], 2)

    def test_imported_and_dedicated_blocks_are_not_pool_headroom(self):
        r = summarize([block('imported host memory 0xabc', handle=2, size=512*MIB),
                       block('imported fresh pages for a buffer', handle=3, size=4096),
                       block('made a dedicated block of a buffer', handle=4, size=128*MIB),
                       block(), block('freed a block', handle=2, size=512*MIB)])
        self.assertEqual(r['pooled']['bytes'], 64*MIB)
        self.assertNotIn('imported_host_memory', r['live_categories'])
        self.assertEqual(r['live_categories']['dedicated_buffer']['bytes'], 128*MIB)

    def test_all_pinned_storage_classes_preserve_their_logged_identity(self):
        for storage in ['Shared', 'WriteCombined', 'Private', 'Managed', 'Memoryless']:
            with self.subTest(storage=storage):
                r = summarize([block('made a pooled block of buffers'),
                               placement(kind='buffer').replace('Private', storage)])
                self.assertEqual(r['pooled']['rows'][0]['placements'][0]['storage'], storage)

    def test_immediately_freed_block_does_not_enter_the_live_set_or_pool_peak(self):
        r = summarize([block('made a pooled block and freed it at once, its pool having room'),
                       block(handle=1, size=8*MIB)])
        self.assertEqual(r['peaks']['pooled_bytes'], 8*MIB)
        self.assertEqual(r['events']['immediate_allocate_free'], 1)

    def test_retirement_does_not_claim_the_driver_freed_memory(self):
        r = summarize([block(), block('a block goes, freed once the batches in flight complete'),
                       block(handle=1, size=8*MIB)])
        self.assertEqual(r['retired_bytes_total'], 64*MIB)
        self.assertEqual(r['pooled']['bytes'], 8*MIB)
        self.assertNotIn('block_frees', r['events'])

    def test_teardown_accounts_for_placements_without_return_events(self):
        r = summarize([block(), placement(), block('freed a block as the device is destroyed')])
        self.assertEqual(r['placements_discarded_at_device_teardown'], 1)
        self.assertEqual(r['pooled']['bytes'], 0)
        self.assertEqual(r['pooled']['logged_live_placement_bytes'], 0)

    def test_inconsistent_lifetimes_fail_instead_of_producing_memory_savings(self):
        cases = [
            [placement()],
            [block('freed a block')],
            [block(), block()],
            [block(), placement(), block('freed a block')],
            [block(), placement(), block('a block goes, freed once the batches in flight complete')],
            [block(), block('freed a block', size=32*MIB)],
            [block(), block('freed a block', memory_type=2)],
            [block(), placement(returned=True)],
            [block(), placement(), placement(size=8*MIB, returned=True)],
            [block('made a dedicated block of an image'), placement()],
            [block(), placement(memory_type=1)],
            [block(), placement(kind='buffer')],
        ]
        for lines in cases:
            with self.subTest(lines=lines), self.assertRaisesRegex(ValueError, 'Memory-log line'):
                summarize(lines)

    def test_overlaps_bounds_and_unsigned_integer_limits_fail(self):
        cases = [
            [block(), placement(), placement(offset=1)],
            [block(), placement(offset=64*MIB)],
            [block(size=0)],
            [block(handle=0)],
            [block(size=1 << 64)],
            [block(memory_type=1 << 32)],
            [full(), block(), placement(size=0)],
            [block(), placement(size=MIB)],
        ]
        for lines in cases:
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                summarize(lines)

    def test_unknown_truncated_and_mixed_coverage_logs_fail(self):
        for lines in [[], ['ordinary console output\n'], [full()],
                      [block().rstrip('\n')], [block(), PREFIX + 'placed a Private image\n'],
                      [block(), full()], [PREFIX + 'future event\n'],
                      [block('imported a dma-buf')]]:
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                summarize(lines)

    def test_logging_prefix_and_crlf_preserve_line_numbers(self):
        r = summarize(['non-memory startup log\n', 'prefix: ' + block().replace('\n', '\r\n')])
        self.assertEqual(r['last_event_line'], 2)

    def test_cli_hash_private_permissions_exclusive_and_failed_input(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/analyze_graphics_memory.py'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root/'console.log', root/'report.json'
            source.write_text('private unrelated console line\n' + block() + placement())
            command = [sys.executable, str(script), str(source), '--output', str(output)]
            done = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            raw = output.read_bytes()
            self.assertEqual(json.loads(raw)['log_sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('private unrelated', done.stdout + done.stderr)
            again = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(output.read_bytes(), raw)
            self.assertEqual(set(root.iterdir()), {source, output})
            source.write_text(block() + placement(returned=True))
            failed = subprocess.run(command[:-1] + [str(root/'invalid.json')], capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse((root/'invalid.json').exists())


if __name__ == '__main__':
    unittest.main()
