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
    def test_stackshot_reason_and_exact_pid_do_not_establish_boot_identity(self):
        reason = 'XPC message timeout. Writing a stackshot and terminating.'
        result = summarize(encoded({
            'bug_type': '288', 'pid': 42, 'incident': CURRENT, 'reason': reason,
            'processByPid': {
                '7': {'procname': 'UnrelatedProcess'},
                '42': {'procname': 'ExampleAudioService', 'threadById': {}},
            },
        }, {'bug_type': '288', 'timestamp': '2026-01-01 00:00:00'}), CURRENT,
            thread_frames=3)
        self.assertEqual(result['process'], 'ExampleAudioService')
        self.assertEqual(result['process_source'], 'processByPid')
        self.assertEqual(result['report_reason'], reason)
        self.assertEqual(result['attribution'], 'unknown_boot')
        self.assertFalse(result['watchdog_termination'])
        self.assertEqual(result['termination'], {})
        self.assertEqual(result['sampled_threads'], [])

    def test_stackshot_selected_identity_rejects_ambiguity_and_invalid_types(self):
        for raw in (
            b'{"pid":42,"processByPid":{"42":{},"42":{"procname":"service"}}}',
            b'{"pid":42,"processByPid":{"42":{"procname":"a","procname":"b"}}}',
            b'{"pid":42,"processByPid":{"42":{"procname":[]}}}',
            b'{"pid":42,"processByPid":{"42":[]}}',
            b'{"pid":true,"processByPid":{}}',
            b'{"pid":42,"processByPid":[]}',
            b'{"reason":{}}', b'{"procName":[]}',
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                summarize(raw, CURRENT)

    def test_stackshot_unselected_fields_and_missing_pid_are_not_guessed(self):
        raw = (b'{"pid":42,"processByPid":{"7":{"procname":"a","procname":"b"},'
               b'"42":{"procname":"service","threadById":{"1":{},"1":{}}}}}')
        result = summarize(raw, CURRENT)
        self.assertEqual(result['process'], 'service')
        self.assertEqual(result['duplicate_keys_in_unselected_objects'], 2)
        for body in ({'pid': 43, 'processByPid': {'42': {'procname': 'service'}}},
                     {'processByPid': {'42': {'procname': 'service'}}}):
            result = summarize(encoded(body), CURRENT)
            self.assertIsNone(result['process'])
            self.assertIsNone(result['process_source'])
        result = summarize(encoded({'procName': 'Explicit', 'pid': 42,
                                    'processByPid': {'42': {'procname': 'Other'}}}), CURRENT)
        self.assertEqual(result['process'], 'Explicit')
        self.assertEqual(result['process_source'], 'procName')

    def test_optional_stacks_keep_identity_and_bound_frames_without_inferring_cause(self):
        body = {
            'bootSessionUUID': EARLIER, 'faultingThread': 2,
            'usedImages': [{'name': 'UIKitCore', 'path': '/private/example/UIKitCore'}],
            'threads': [
                {'id': 1, 'queue': 'worker', 'frames': []},
                {'id': 2, 'queue': 'com.apple.main-thread', 'frames': [
                    {'symbol': 'layout', 'imageIndex': 0, 'imageOffset': 100},
                    {'symbol': 'snapshot', 'imageIndex': 0},
                ]},
                {'id': 3, 'triggered': True, 'frames': [{'imageIndex': 0, 'imageOffset': 200}]},
            ],
        }
        self.assertNotIn('sampled_threads', summarize(encoded(body), CURRENT))
        result = summarize(encoded(body), CURRENT, thread_frames=1)
        self.assertEqual(result['attribution'], 'different_boot')
        main, triggered = result['sampled_threads']
        self.assertEqual(main['selected_by'], ['main_queue'])
        self.assertEqual(main['total_frames'], 2)
        self.assertTrue(main['frames_truncated'])
        self.assertEqual(main['frames'], [{'symbol': 'layout', 'imageIndex': 0,
                                          'imageOffset': 100, 'image_name': 'UIKitCore'}])
        self.assertEqual(triggered['selected_by'], ['triggered', 'faulting_thread_index'])
        self.assertFalse(triggered['frames_truncated'])
        self.assertNotIn('symbol', triggered['frames'][0])
        self.assertNotIn('/private/example', json.dumps(result))
        self.assertIn('not a duration trace', result['stack_scope'])

    def test_optional_stack_selection_rejects_ambiguous_or_unresolvable_fields(self):
        for fragment in (
            '"faultingThread":true', '"faultingThread":9',
            '"threads":{}', '"usedImages":{}',
            '"threads":[{"triggered":1}]',
            '"threads":[{"triggered":true,"triggered":false}]',
            '"threads":[{"queue":{},"id":false}]',
            '"threads":[{"triggered":true,"frames":[],"frames":[]}]',
            '"threads":[{"triggered":true,"frames":[{"imageIndex":0}]}]',
            '"threads":[{"triggered":true,"frames":[{"imageOffset":true}]}]',
            '"threads":[{"triggered":true,"frames":[{"symbol":"a","symbol":"b"}]}]',
            '"threads":[{"triggered":true,"frames":[{"symbol":{}}]}]',
            '"usedImages":[{"name":"a","name":"b"}],'
            '"threads":[{"triggered":true,"frames":[{"imageIndex":0}]}]',
        ):
            raw = ('{' + fragment + '}').encode()
            with self.subTest(fragment=fragment):
                # Optional inspection does not change default boot attribution.
                self.assertEqual(summarize(raw, CURRENT)['attribution'], 'unknown_boot')
                with self.assertRaises(ValueError):
                    summarize(raw, CURRENT, thread_frames=10)
        for limit in (-1, 101, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                summarize(b'{}', CURRENT, thread_frames=limit)
        self.assertEqual(summarize(b'{}', CURRENT, thread_frames=100)['sampled_threads'], [])

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
                       '--boot-uuid', CURRENT, '--thread-frames', '3', '--output', str(output)]
            process = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(process.returncode, 1, process.stderr)
            report = json.loads(output.read_text())
            self.assertEqual(report['counts']['different_boot'], 1)
            self.assertEqual(report['counts']['unknown_boot'], 1)
            self.assertEqual(report['counts']['matching_boot'], 0)
            self.assertEqual(len(report['errors']), 1)
            self.assertFalse(report['inspection_complete'])
            self.assertFalse(report['browser_readiness_verified'])
            self.assertTrue(all(row['sampled_threads'] == [] for row in report['reports']))
            saved = output.read_bytes()
            process = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(process.returncode, 1)
            self.assertEqual(output.read_bytes(), saved)


if __name__ == '__main__':
    unittest.main()
