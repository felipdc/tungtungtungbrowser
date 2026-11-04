"""Do not attribute republished crash reports to the wrong native boot."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.summarize_crashes import MAX_REPORT_BYTES, boot_uuid, summarize

CURRENT = '11111111-2222-3333-4444-555555555555'
EARLIER = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'


def encoded(body, header=None):
    return ((json.dumps(header) + '\n' if header else '') + json.dumps(body)).encode()


class CrashSummaryTest(unittest.TestCase):
    def test_native_watchdog_report_is_attributed_by_boot(self):
        result = summarize(encoded({
            'bootSessionUUID': CURRENT, 'procName': 'ExampleBrowser', 'pid': 42,
            'termination': {'namespace': 'FRONTBOARD', 'code': 2343432205,
                            'reasons': ['scene-update watchdog transgression: time allowance of 10.00 seconds',
                                        'WatchdogEvent: scene-update']},
        }, {'app_name': 'ExampleBrowser', 'bug_type': '309'}), CURRENT)
        self.assertEqual(result['attribution'], 'matching_boot')
        self.assertTrue(result['watchdog_termination'])
        self.assertEqual(result['watchdog_event'], 'scene-update')
        self.assertEqual(result['wall_clock_allowance_seconds'], 10)

    def test_incident_pid_and_capture_time_are_not_boot_identity(self):
        result = summarize(encoded({'incident': CURRENT, 'pid': 42,
                                    'captureTime': '2026-01-01 00:00:00'}), CURRENT)
        self.assertEqual(result['attribution'], 'unknown_boot')
        result = summarize(encoded({'bootSessionUUID': EARLIER, 'pid': 42}), CURRENT)
        self.assertEqual(result['attribution'], 'different_boot')

    def test_panic_embedded_boot_identity_and_conflicts(self):
        body = {'panicString': f'panic: diagnostic\nBoot session UUID: {EARLIER}\n'}
        self.assertEqual(summarize(encoded(body, {'bug_type': '210'}), CURRENT)['attribution'],
                         'different_boot')
        body['bootSessionUUID'] = CURRENT
        self.assertEqual(summarize(encoded(body), CURRENT)['attribution'],
                         'conflicting_boot_identity')
        body['bootSessionUUID'] = EARLIER.upper()
        self.assertEqual(summarize(encoded(body), EARLIER)['attribution'], 'matching_boot')

    def test_hex_code_is_supported_but_boolean_is_not_a_code(self):
        for code in ('0x8badf00d', '2343432205', 2343432205):
            self.assertTrue(summarize(encoded({'termination': {'code': code}}), CURRENT)['watchdog_termination'])
        for code in (True, None, 'watchdog', 9):
            self.assertFalse(summarize(encoded({'termination': {'code': code}}), CURRENT)['watchdog_termination'])

    def test_repeated_ids_in_unused_panic_task_maps_do_not_hide_boot_identity(self):
        raw = ('{"panicString":"Boot session UUID: ' + EARLIER + '\\n",'
               '"tasks":{"42":{"name":"old","name":"first"},"42":{"name":"second"}}}').encode()
        result = summarize(raw, CURRENT)
        self.assertEqual(result['attribution'], 'different_boot')
        self.assertEqual(result['duplicate_keys_in_unselected_objects'], 2)

    def test_malformed_or_ambiguous_input_is_not_silently_accepted(self):
        for raw in (b'', b'[]', b'{}garbage', b'{}{}', b'{}{}{}', b'\xff',
                    b'{"bootSessionUUID":null}', b'{"bootSessionUUID":"invalid"}',
                    b'{"termination":{"code":1,"code":2343432205}}',
                    b'{"bootSessionUUID":null,"bootSessionUUID":null}',
                    b'{"x":NaN}', b'{"x":1e999}', b'{"termination":[]}',
                    b'{"termination":{"reasons":"text"}}',
                    encoded({'termination': {'code': 2343432205,
                                             'reasons': ['time allowance of ' + '9' * 400 + ' seconds']}}),
                    b' ' * (MAX_REPORT_BYTES + 1)):
            with self.subTest(raw=raw[:80]), self.assertRaises(ValueError):
                summarize(raw, CURRENT)
        for value in (None, '', CURRENT.replace('-', ''), '{' + CURRENT + '}'):
            with self.subTest(uuid=value), self.assertRaises(ValueError):
                boot_uuid(value)

    def test_cli_keeps_unknown_errors_and_old_reports_despite_new_mtime(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/summarize_crashes.py'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, unknown, broken, output = [root / name for name in ('old.ips', 'unknown.ips', 'broken.ips', 'report.json')]
            old.write_bytes(encoded({'bootSessionUUID': EARLIER, 'incident': CURRENT}))
            os.utime(old, (2_000_000_000, 2_000_000_000))
            unknown.write_bytes(encoded({'incident': CURRENT}))
            broken.write_bytes(b'{')
            command = [sys.executable, str(script), str(old), str(unknown), str(broken),
                       '--boot-uuid', CURRENT, '--output', str(output)]
            process = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(process.returncode, 1, process.stderr)
            report = json.loads(output.read_text())
            self.assertEqual(report['counts']['different_boot'], 1)
            self.assertEqual(report['counts']['unknown_boot'], 1)
            self.assertEqual(report['counts']['matching_boot'], 0)
            self.assertEqual(len(report['errors']), 1)
            self.assertFalse(report['inspection_complete'])
            self.assertFalse(report['browser_readiness_verified'])
            saved = output.read_bytes()
            process = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(process.returncode, 1)
            self.assertEqual(output.read_bytes(), saved)


if __name__ == '__main__':
    unittest.main()
