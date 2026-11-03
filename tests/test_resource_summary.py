"""Check resource comparisons against irregular and interrupted observations."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.summarize_resources import summarize


def sample(elapsed, cpu, memory, oom=3):
    return {'kind': 'sample', 'elapsed_seconds': elapsed,
            'memory_bytes': memory, 'peak_bytes_since_container_created': 9000,
            'memory_stat': {'anon': 100, 'file': 200, 'shmem': 150},
            'cpu_stat': {'usage_usec': cpu, 'throttled_usec': 200},
            'memory_events': {'oom': oom, 'oom_kill': 0}}


def complete_run():
    return [
        {'kind': 'configuration', 'requested_seconds': 10, 'interval_seconds': 1},
        sample(0, 5_000_000, 1000),
        sample(1, 7_000_000, 2000),
        sample(10, 16_000_000, 1500, oom=4),
        {'kind': 'summary', 'elapsed_seconds': 10.01, 'sampled_peak_bytes': 2000},
    ]


class ResourceSummaryTest(unittest.TestCase):
    def test_irregular_samples_use_elapsed_cpu_and_separate_lifetime_peak(self):
        result = summarize(iter(complete_run()))
        self.assertTrue(result['measurement_complete'])
        self.assertEqual(result['observed_seconds'], 10)
        self.assertEqual(result['maximum_sample_gap_seconds'], 9)
        self.assertEqual(result['cpu']['average_cores_used'], 1.1)
        self.assertEqual(result['memory']['sampled_peak_bytes'], 2000)
        self.assertEqual(result['memory']['peak_bytes_since_container_created'], 9000)
        self.assertEqual(result['memory_events']['at_start']['oom'], 3)
        self.assertEqual(result['memory_events']['deltas']['oom'], 1)
        self.assertFalse(result['navigation_verified'])

    def test_missing_summary_preserves_samples_as_incomplete(self):
        result = summarize(complete_run()[:-1])
        self.assertFalse(result['measurement_complete'])
        self.assertEqual(result['samples'], 3)
        one = summarize(complete_run()[:2])
        self.assertIsNone(one['cpu']['average_cores_used'])
        self.assertEqual(one['observed_seconds'], 0)

    def test_resets_and_invalid_measurements_cannot_look_complete(self):
        def bad_time(rows): rows[2]['elapsed_seconds'] = 0
        def cpu_reset(rows): rows[2]['cpu_stat']['usage_usec'] = 1
        def event_reset(rows): rows[2]['memory_events']['oom'] = 0
        def missing_counter(rows): del rows[2]['cpu_stat']['throttled_usec']
        def wrong_peak(rows): rows[-1]['sampled_peak_bytes'] = 3000
        def negative_memory(rows): rows[2]['memory_bytes'] = -1
        def nan_time(rows): rows[2]['elapsed_seconds'] = float('nan')
        def early_summary(rows): rows[-1]['elapsed_seconds'] = 1
        def duplicate_run(rows): rows.extend(complete_run())
        for mutate in (bad_time, cpu_reset, event_reset, missing_counter,
                       wrong_peak, negative_memory, nan_time, early_summary, duplicate_run):
            with self.subTest(mutation=mutate.__name__):
                rows = copy.deepcopy(complete_run())
                mutate(rows)
                with self.assertRaises(ValueError):
                    summarize(rows)

    def test_cli_returns_incomplete_status_and_never_overwrites_report(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/summarize_resources.py'
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'samples.jsonl', Path(directory) / 'report.json'
            source.write_text(''.join(json.dumps(row) + '\n' for row in complete_run()[:-1]))
            command = [sys.executable, str(script), str(source), '--output', str(output)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(json.loads(output.read_text())['measurement_complete'])
            saved = output.read_bytes()
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(output.read_bytes(), saved)
            output.unlink()
            source.write_text(source.read_text() + '{"kind":')
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('Invalid JSON at line', result.stderr)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
