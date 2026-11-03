import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('jit', Path(__file__).resolve().parents[1] / 'scripts/map_jit_samples.py')
jit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jit)


class TimedMappingTest(unittest.TestCase):
    @staticmethod
    def header(*, flags=0, created=1):
        return struct.pack('<IIIIIIQQ', 0x4A695444, 1, 40, 62, 0, 14, created, flags)

    @staticmethod
    def load(timestamp=20, address=100, name='guest-0x40200000'):
        name = name.encode() + b'\0'
        return (struct.pack('<IIQIIQQQQ', 0, 56 + len(name) + 32, timestamp,
                            14, 36, address, address, 32, 0) + name + b'x' * 32)

    def test_address_reuse_partial_overlap_and_unmapped_samples(self):
        header = struct.pack('<IIIIIIQQ', 0x4A695444, 1, 40, 62, 0, 14, 1, 0)
        def load(timestamp, address, count, name, index):
            name = name.encode() + b'\0'
            return (struct.pack('<IIQIIQQQQ', 0, 56 + len(name) + count, timestamp,
                                14, 36, address, address, count, index) + name + b'x' * count)
        data = header + load(20, 100, 32, 'guest-A', 0) + load(40, 100, 32, 'guest-B', 1)
        data += load(60, 110, 16, 'guest-C', 2)
        samples = [{'time_ns': t, 'ip': ip, 'pid': 99, 'tid': 103}
                   for t, ip in [(10, 105), (30, 105), (50, 105), (70, 105), (70, 115), (70, 300)]]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'jit.dump'; path.write_bytes(data)
            report = jit.map_samples(path, samples)
            self.assertEqual(report['mapped_samples'], 4)
            self.assertEqual({r['guest_block']: r['samples'] for r in report['top_guest_blocks']},
                             {None: 2, 'guest-A': 1, 'guest-B': 2, 'guest-C': 1})
            with self.assertRaisesRegex(ValueError, 'no complete record beyond'):
                jit.map_samples(path, samples, live=True)
            path.write_bytes(data + load(90, 100, 32, 'future-code', 3) + b'unfinished tail')
            live = jit.map_samples(path, samples, live=True)
            self.assertTrue(live['timestamp_barrier_verified'])
            self.assertFalse(live['producer_exit_must_be_verified_separately'])
            self.assertEqual(live['top_guest_blocks'], report['top_guest_blocks'])
            path.write_bytes(data[:-1])
            with self.assertRaisesRegex(ValueError, 'Truncated'):
                jit.map_samples(path, samples)

    def test_primary_ip_and_nanosecond_clock_parsing(self):
        samples = jit.read_samples(' 99/103  1.000000030: 69\n 99/103  2.5: \n\t73\n\tdeadbeef\n')
        self.assertEqual([(s['time_ns'], s['ip']) for s in samples], [(1000000030, 105), (2500000000, 115)])
        with self.assertRaises(ValueError):
            jit.read_samples('99/103 2.5:\n')
        with self.assertRaisesRegex(ValueError, 'Unrecognized'):
            jit.read_samples('99/103 2.5: 69\nunexpected perf output\n')

    def test_rejects_clock_mismatch_and_mixed_processes(self):
        samples = [{'pid': 99, 'tid': 103, 'time_ns': 30, 'ip': 105}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'jit.dump'
            path.write_bytes(self.header(created=31) + self.load())
            with self.assertRaisesRegex(ValueError, 'precede JIT creation'):
                jit.map_samples(path, samples)
            path.write_bytes(self.header(flags=1) + self.load())
            with self.assertRaisesRegex(ValueError, 'Unsupported JIT header or clock'):
                jit.map_samples(path, samples)
            with self.assertRaisesRegex(ValueError, 'exactly one host process'):
                jit.map_samples(path, samples + [samples[0] | {'pid': 100}])

    def test_requires_complete_live_barrier_and_rejects_code_moves(self):
        samples = [{'pid': 99, 'tid': 103, 'time_ns': 30, 'ip': 105}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'jit.dump'
            path.write_bytes(self.header() + self.load() + self.load(timestamp=40)[:-1])
            with self.assertRaisesRegex(ValueError, 'Truncated'):
                jit.map_samples(path, samples, live=True)
            path.write_bytes(self.header() + self.load() + struct.pack('<IIQ', 1, 16, 25))
            with self.assertRaisesRegex(ValueError, 'code moves are not modeled'):
                jit.map_samples(path, samples)

    def test_cli_filters_host_pid_and_preserves_existing_private_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dump, samples, output = (root / name for name in ('jit.dump', 'samples.txt', 'report.json'))
            dump.write_bytes(self.header() + self.load())
            samples.write_text('99/103 0.000000030: 69\n100/104 0.000000031: 69\n')
            args = [str(dump), str(samples), '--host-pid', '99', '--output', str(output)]
            with contextlib.redirect_stdout(io.StringIO()):
                jit.main(args)
            report = json.loads(output.read_text())
            self.assertEqual(report['host_pid'], 99)
            self.assertEqual(report['jit_header_pid'], 14)  # Container PID namespace.
            self.assertEqual(report['excluded_pid_samples'], 1)
            self.assertEqual(report['mapped_samples'], 1)
            if os.name == 'posix':
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            original = output.read_bytes()
            with self.assertRaises(FileExistsError):
                jit.main(args)
            self.assertEqual(output.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
