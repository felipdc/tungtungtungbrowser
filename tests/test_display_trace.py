"""Trace boundaries, errors and interleaved render jobs remain observable."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.analyze_qemu_display_trace import analyze, parse


def job(display='0xaaa', number=7, offset=0, commit=0, wait=0):
    def line(kind, ns, fields):
        return f'42@100:apple_gfx_frame_{kind} ns={ns+offset} display={display} job={number} {fields}\n'
    return [line('job', 100, 'target=0xbbb width=12 height=24'),
            line('stage', 200, 'stage=encode-begin status=0'),
            line('stage', 500, 'stage=encode-end status=0'),
            line('stage', 600, 'stage=commit-begin status=0'),
            line('stage', 700, f'stage=commit-end status={commit}'),
            line('stage', 800, 'stage=wait-begin status=0'),
            line('stage', 1000, f'stage=wait-end status={wait}'),
            line('stage', 1100, 'stage=read-begin status=0'),
            line('stage', 1200, 'stage=read-end status=0'),
            line('copy', 1400, 'encoded=1 same_size=1 same_target=1')]


class DisplayTraceTest(unittest.TestCase):
    def test_interleaved_displays_and_cross_thread_log_order(self):
        a, b = job(), job('0xccc')
        lines = [v for pair in zip(a, b) for v in pair]
        lines[2], lines[4] = lines[4], lines[2]
        r = analyze(['unrelated logging\n', *lines])
        self.assertEqual(r['jobs_observed'], 2)
        self.assertEqual(r['fully_observed_jobs'], 2)
        self.assertGreater(r['file_timestamp_inversions'], 0)
        self.assertEqual(r['ignored_lines'], 1)
        self.assertAlmostEqual(r['duration_seconds']['encode']['median'], 300e-9)
        self.assertAlmostEqual(r['duration_seconds']['job_to_copy']['median'], 1300e-9)
        self.assertFalse(r['visual_readiness_established'])

    def test_commit_and_wait_errors_survive_successful_read_and_copy(self):
        r = analyze(job(commit=-5, wait=3))
        self.assertEqual(r['fully_observed_jobs'], 1)
        self.assertEqual([(e['stage'], e['status']) for e in r['nonzero_stage_results']],
                         [('commit-end', -5), ('wait-end', 3)])
        self.assertFalse(r['performance_comparison_established'])

    def test_encode_failure_has_a_complete_short_lifecycle(self):
        rows = job()
        rows = rows[:3] + rows[-1:]
        rows[2] = rows[2].replace('status=0', 'status=-1')
        rows[-1] = rows[-1].replace('encoded=1', 'encoded=0')
        r = analyze(rows)
        self.assertEqual(r['fully_observed_jobs'], 1)
        self.assertEqual(len(r['copies_with_false_flags']), 1)
        self.assertNotIn('wait', r['duration_seconds'])

    def test_partial_and_duplicate_jobs_are_retained(self):
        for lines in [job()[1:], job()[:-1], job()+job()]:
            with self.subTest(lines=len(lines)):
                r = analyze(lines)
                self.assertEqual(r['partial_or_inconsistent_jobs'], 1)
                self.assertEqual(len(r['jobs'][0]['events']), len(lines))
        r = analyze([job()[3], job()[4].replace('ns=700', 'ns=550')])
        self.assertIn('negative duration: commit', r['jobs'][0]['issues'])
        self.assertNotIn('commit', r['duration_seconds'])

    def test_request_without_render_does_not_claim_readiness(self):
        r = analyze(['apple_gfx_frame_state ns=1 stage=request-no-render display=0xaaa job=7 dirty=1 rendering=0\n'])
        self.assertEqual(r['states'], {'request-no-render': 1})
        self.assertEqual(r['jobs_observed'], 0)
        self.assertFalse(r['visual_readiness_established'])

    def test_malformed_recognized_events_fail_with_line_number(self):
        for value in [job()[0].replace('width=12', 'width=no'),
                      job()[0].rstrip('\n') + ' width=12\n',
                      job()[0].replace('frame_job', 'frame_unknown_name'),
                      job()[-1].replace('encoded=1', 'encoded=2'),
                      job()[0].replace('ns=100', 'ns=18446744073709551616')]:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'Line 9:'):
                    parse(value, 9)
        with self.assertRaisesRegex(ValueError, 'No display trace'):
            analyze(['ordinary log\n'])

    def test_cli_preserves_existing_output_and_records_input_hash(self):
        script = Path(__file__).resolve().parents[1]/'scripts/analyze_qemu_display_trace.py'
        with tempfile.TemporaryDirectory() as directory:
            trace, output = Path(directory)/'trace.log', Path(directory)/'result.json'
            trace.write_text(''.join(job()))
            command = [sys.executable, str(script), str(trace), '--output', str(output)]
            subprocess.run(command, check=True, capture_output=True)
            data = output.read_bytes()
            self.assertEqual(len(json.loads(data)['trace_sha256']), 64)
            refused = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn('Output already exists', refused.stderr)
            self.assertEqual(output.read_bytes(), data)
