"""Exercise collection against proc/cgroup fixtures without Docker or a guest."""
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from scripts.sample_resources import collect
from scripts.summarize_resources import summarize


class ResourceSamplerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.proc = self.root / 'proc'
        self.group = self.root / 'cgroup/container'
        (self.proc / '123').mkdir(parents=True)
        (self.proc / 'sys/kernel/random').mkdir(parents=True)
        (self.proc / 'sys/kernel/random/boot_id').write_text('fixture-boot\n')
        (self.proc / '123/cgroup').write_text('0::/container\n')
        self.write_identity(999)
        self.group.mkdir(parents=True)
        for name, value in {'cpu.max': '200000 100000',
                            'cpuset.cpus.effective': '0-3',
                            'memory.max': '4096', 'memory.swap.max': '0'}.items():
            (self.group / name).write_text(value + '\n')
        self.write_counters(10_000_000)
        self.output = self.root / 'samples.jsonl'
        self.now = 100_000_000_000
        self.inspect = json.dumps([{
            'Id': 'fixture-container', 'Image': 'fixture-image',
            'State': {'Running': True, 'Pid': 123, 'StartedAt': 'fixture-start'},
            'HostConfig': {'Memory': 4096, 'NanoCpus': 2_000_000_000},
        }])

    def write_identity(self, start):
        # The command name can contain spaces and closing parentheses.
        (self.proc / '123/stat').write_text(
            '123 (container ) init) ' + ' '.join(['S'] + ['0'] * 18 + [str(start)]))

    def write_counters(self, cpu):
        for filename, value in {
            'memory.current': '2000\n', 'memory.peak': '3000\n',
            'memory.stat': 'anon 1000\nfile 900\nshmem 800\n',
            'cpu.stat': f'usage_usec {cpu}\nthrottled_usec 0\n',
            'memory.events': 'oom 0\noom_kill 0\n',
        }.items():
            (self.group / filename).write_text(value)

    def sleep(self, seconds):
        self.now += round(seconds * 1e9)
        self.write_counters(20_000_000)

    def run_collection(self, sleep=None):
        with ExitStack() as stack:
            stack.enter_context(patch('scripts.sample_resources.subprocess.check_output',
                                      return_value=self.inspect))
            stack.enter_context(patch('scripts.sample_resources.time.monotonic_ns',
                                      side_effect=lambda: self.now))
            stack.enter_context(patch('scripts.sample_resources.time.sleep',
                                      side_effect=sleep or self.sleep))
            clock = stack.enter_context(patch('scripts.sample_resources.datetime'))
            # Wall-clock correction must not alter CPU or elapsed-time arithmetic.
            clock.now.side_effect = [datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                                     datetime(2026, 1, 1, 11, tzinfo=timezone.utc)]
            collect('fixture', self.output, seconds=10, interval=10,
                    proc_root=self.proc, cgroup_root=self.root / 'cgroup')

    def records(self):
        return [json.loads(line) for line in self.output.read_text().splitlines()]

    def test_monotonic_alignment_survives_wall_clock_correction(self):
        self.run_collection()
        records = self.records()
        config, first, last, terminal = records
        self.assertEqual(config['host_boot_id'], 'fixture-boot')
        self.assertEqual(config['clock'], 'CLOCK_MONOTONIC')
        self.assertEqual((config['host_pid'], config['pid_start_ticks']), (123, '999'))
        self.assertEqual(config['started_monotonic_ns'], 100_000_000_000)
        self.assertEqual(last['monotonic_ns'] - first['monotonic_ns'], 10_000_000_000)
        self.assertEqual(last['elapsed_seconds'], 10)
        self.assertLess(last['at'], first['at'])
        self.assertEqual(last['cpu_cores_used'], 1)
        self.assertEqual(terminal['kind'], 'summary')
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        report = summarize(records)
        self.assertTrue(report['measurement_complete'])
        self.assertEqual(report['cpu']['average_cores_used'], 1)

    def test_pid_reuse_preserves_incomplete_evidence(self):
        def replace_process(seconds):
            self.sleep(seconds)
            self.write_identity(1000)

        with self.assertRaisesRegex(RuntimeError, 'process changed'):
            self.run_collection(replace_process)
        records = self.records()
        self.assertEqual([row['kind'] for row in records], ['configuration', 'sample'])
        self.assertFalse(summarize(records)['measurement_complete'])

    def test_quota_does_not_imply_cpu_affinity(self):
        self.run_collection()
        limits = summarize(self.records())['configuration']['cgroup_limits']
        self.assertEqual(limits['cpu.max'], '200000 100000')
        self.assertEqual(limits['cpuset.cpus.effective'], '0-3')
        self.assertEqual(limits['memory.swap.max'], '0')

    def test_limit_change_preserves_incomplete_evidence(self):
        def change_affinity(seconds):
            self.sleep(seconds)
            (self.group / 'cpuset.cpus.effective').write_text('0-1\n')

        with self.assertRaisesRegex(RuntimeError, 'resource limits changed'):
            self.run_collection(change_affinity)
        records = self.records()
        self.assertEqual([row['kind'] for row in records], ['configuration', 'sample'])
        self.assertFalse(summarize(records)['measurement_complete'])

    def test_unavailable_controller_is_explicit(self):
        (self.group / 'cpuset.cpus.effective').unlink()
        self.run_collection()
        self.assertIsNone(self.records()[0]['cgroup_limits']['cpuset.cpus.effective'])

    def test_memory_stalls_are_recorded_even_without_oom(self):
        pressure = self.group / 'memory.pressure'
        pressure.write_text('some avg10=0.00 avg60=0.00 avg300=0.00 total=500000\n'
                            'full avg10=0.00 avg60=0.00 avg300=0.00 total=100000\n')

        def add_stalls(seconds):
            self.sleep(seconds)
            pressure.write_text('some avg10=7.00 avg60=2.00 avg300=1.00 total=2500000\n'
                                'full avg10=3.00 avg60=1.00 avg300=0.50 total=600000\n')

        self.run_collection(add_stalls)
        result = summarize(self.records())
        self.assertEqual(result['memory_events']['deltas']['oom'], 0)
        self.assertEqual(result['memory_pressure']['counter_deltas_usec'],
                         {'some': 2_000_000, 'full': 500_000})
        self.assertEqual(result['memory_pressure']['stall_percent'], {'some': 20, 'full': 5})

    def test_missing_pressure_is_not_reported_as_zero_stalls(self):
        self.run_collection()
        result = summarize(self.records())['memory_pressure']
        self.assertFalse(result['available'])
        self.assertIsNone(result['counter_deltas_usec'])
        self.assertIsNone(result['stall_percent'])

    def test_container_exit_preserves_incomplete_evidence(self):
        def exit_process(seconds):
            self.sleep(seconds)
            (self.proc / '123/stat').unlink()

        with self.assertRaises(FileNotFoundError):
            self.run_collection(exit_process)
        self.assertFalse(summarize(self.records())['measurement_complete'])

    def test_existing_output_is_unchanged(self):
        self.output.write_bytes(b'previous evidence\n')
        with self.assertRaises(FileExistsError):
            self.run_collection()
        self.assertEqual(self.output.read_bytes(), b'previous evidence\n')


if __name__ == '__main__':
    unittest.main()
