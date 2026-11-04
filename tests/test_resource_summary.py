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

    def test_pressure_uses_observed_interval_and_supports_older_recordings(self):
        rows = complete_run()
        self.assertFalse(summarize(rows)['memory_pressure']['available'])
        for row, some, full in zip(rows[1:-1], [100, 300_100, 1_000_100],
                                   [50, 50, 250_050]):
            row['memory_pressure_usec'] = {'some': some, 'full': full}
        result = summarize(rows)['memory_pressure']
        self.assertTrue(result['available'])
        self.assertEqual(result['stall_percent'], {'some': 10, 'full': 2.5})
        self.assertIsNone(summarize(rows[:2])['memory_pressure']['stall_percent'])
        for invalid in (None, {'some': 0, 'full': 50}, {'some': 300_100},
                        {'some': True, 'full': 50}, {'some': -1, 'full': 50}):
            with self.subTest(pressure=invalid):
                bad = copy.deepcopy(rows)
                bad[2]['memory_pressure_usec'] = invalid
                with self.assertRaises(ValueError):
                    summarize(bad)

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

    def test_cpu_pressure_rejects_resets_and_partial_or_undeclared_observations(self):
        rows = complete_run()
        rows[0]['include_cpu_pressure'] = True
        for row in rows[1:-1]:
            elapsed = row['elapsed_seconds']
            row['cpu_pressure_usec'] = {
                'host': {'some': 100 + elapsed * 1000},
                'container': {'some': 200 + elapsed * 1000, 'full': elapsed * 100},
            }
        self.assertTrue(summarize(rows)['cpu_pressure']['recorded'])
        self.assertIsNone(summarize(rows[:2])['cpu_pressure']['host']['stall_percent'])
        for change in ('reset', 'missing', 'availability', 'boolean', 'undeclared', 'full', 'config'):
            with self.subTest(change=change):
                bad = copy.deepcopy(rows)
                if change == 'reset': bad[2]['cpu_pressure_usec']['host']['some'] = 0
                elif change == 'missing': del bad[2]['cpu_pressure_usec']
                elif change == 'availability': bad[2]['cpu_pressure_usec']['host'] = None
                elif change == 'boolean': bad[2]['cpu_pressure_usec']['container']['some'] = True
                elif change == 'undeclared': bad[0]['include_cpu_pressure'] = False
                elif change == 'full': bad[2]['cpu_pressure_usec']['host']['full'] = 0
                else: bad[0]['include_cpu_pressure'] = 1
                with self.assertRaises(ValueError):
                    summarize(bad)

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
