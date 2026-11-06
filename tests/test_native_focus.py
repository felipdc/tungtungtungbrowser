import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from tungtungtungbrowser import Browser, NativeFocusReader, GuestError
from test_browser import NativeFixture


SHA, TOKEN = 'a' * 64, 'b' * 32


def native_result():
    field = {'label': 'Address', 'value': 'https://fixture.invalid/', 'identifier': 'URL',
             'traits': 1 << 21, 'frame': {'x': 20, 'y': 488, 'width': 265, 'height': 48}}
    return {'protocol': 1, 'requested_pid': 301, 'error': 0,
            'before': {'application': 0, 'automation': 1},
            'after': {'application': 0, 'automation': 1},
            'element': {'pid': 301, 'pid_error': 0, 'timeout_error': 0,
                        'attributes': {k: {'error': 0, 'value': v} for k, v in field.items()}}}


def answer(result):
    return {'ok': True, 'crashed': False, 'output': '', 'result': json.dumps({
        'status': 0, 'signal': None, 'stderr': '', 'stdout': json.dumps(result)})}


class NativeFocusTests(unittest.TestCase):
    def test_validated_fixed_native_command_and_owned_focus(self):
        guest = Mock()
        guest._rpc.return_value = answer(native_result())
        reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
        field = reader(301)
        self.assertEqual(field['identifier'], 'URL')
        self.assertEqual(field['frame']['y'], 488)
        self.assertEqual(field['pid'], 301)
        guest._rpc.assert_called_once_with('script.eval', source=(
            'proc.run(' + json.dumps(reader.path) + ', ["pid", "301"], "")'), timeout=30)

    def test_transient_read_is_repeated_only_after_verified_switch_restoration(self):
        failed = {**native_result(), 'error': -25216, 'element': None}
        guest = Mock()
        guest._rpc.side_effect = [answer(failed), answer(native_result())]
        reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
        self.assertEqual(reader(301)['pid'], 301)
        self.assertEqual([r['status'] for r in reader.last_read_attempts], ['failed', 'read'])
        self.assertEqual(reader.last_read_attempts[0]['ax_error'], -25216)
        self.assertEqual(guest._rpc.call_count, 2)
        for call in guest._rpc.call_args_list:
            self.assertEqual(call.args, ('script.eval',))
            self.assertEqual(call.kwargs['source'], 'proc.run(' + json.dumps(reader.path) + ', ["pid", "301"], "")')

    def test_unrestored_wrong_owner_or_other_failed_read_is_never_repeated(self):
        failed = {**native_result(), 'error': -25216, 'element': None}
        for result in (
                {**failed, 'after': {'application': 1, 'automation': 1}},
                {**failed, 'requested_pid': 302},
                {**failed, 'error': -25204},
                {**failed, 'element': native_result()['element']},
                {k: v for k, v in failed.items() if k != 'before'}):
            with self.subTest(result=result):
                guest = Mock()
                guest._rpc.return_value = answer(result)
                reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
                with self.assertRaises(GuestError):
                    reader(301)
                guest._rpc.assert_called_once()

    def test_focus_read_exhaustion_opt_out_and_invalid_attempt_limit(self):
        failed = {**native_result(), 'error': -25216, 'element': None}
        for attempts in (1, 2):
            guest = Mock()
            guest._rpc.return_value = answer(failed)
            reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN, max_read_attempts=attempts)
            with self.assertRaises(GuestError):
                reader(301)
            self.assertEqual(guest._rpc.call_count, attempts)
        for invalid in (0, 4, True, 1.0, None):
            with self.assertRaises(ValueError):
                NativeFocusReader(Mock(), sha256=SHA, deployment_id=TOKEN, max_read_attempts=invalid)

    def test_no_focus_is_distinct_from_query_failure_or_missing_result(self):
        guest = Mock()
        reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
        result = native_result()
        result['element'] = None
        guest._rpc.return_value = answer(result)
        self.assertIsNone(reader(301))
        guest._rpc.return_value = answer({**result, 'error': -25212})
        self.assertIsNone(reader(301))
        guest._rpc.return_value = answer({**native_result(), 'error': -25212})
        with self.assertRaises(GuestError):
            reader(301)
        for invalid in [{**result, 'error': -25216}, {k: v for k, v in result.items() if k != 'element'}]:
            guest._rpc.return_value = answer(invalid)
            with self.assertRaises(GuestError):
                reader(301)

    def test_wrong_owner_switch_restoration_and_failed_attribute_are_rejected(self):
        guest = Mock()
        reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
        cases = []
        for key, value in [('pid', 302), ('pid_error', -1), ('timeout_error', -1)]:
            result = native_result()
            result['element'][key] = value
            cases.append(result)
        result = native_result()
        result['after']['automation'] = 0
        cases.append(result)
        result = native_result()
        result['element']['attributes']['identifier']['error'] = -25216
        cases.append(result)
        result = native_result()
        result['element']['attributes']['frame']['value']['y'] = float('nan')
        cases.append(result)
        for result in cases:
            with self.subTest(result=result):
                guest._rpc.return_value = answer(result)
                with self.assertRaises(GuestError):
                    reader(301)

    def test_bad_identity_pid_or_native_process_never_becomes_a_field(self):
        guest = Mock()
        for digest, token in [('a;bad', TOKEN), (SHA, '../path')]:
            with self.assertRaises(ValueError):
                NativeFocusReader(guest, sha256=digest, deployment_id=token)
        reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
        for pid in [True, 1, -1, '301', 2147483648]:
            with self.assertRaises(ValueError):
                reader(pid)
        guest._rpc.assert_not_called()
        for process in [{'status': -1, 'signal': 10, 'stdout': '{}', 'stderr': ''},
                        {'status': 0, 'signal': None, 'stdout': 'not JSON', 'stderr': ''}]:
            guest._rpc.return_value = {'ok': True, 'crashed': False, 'output': '', 'result': json.dumps(process)}
            with self.assertRaises(GuestError):
                reader(301)

    def test_deployment_record_cannot_substitute_an_executable_path(self):
        guest = Mock()
        reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
        record = {'status': 'complete', 'helper_sha256': SHA, 'deployment_id': TOKEN, 'path': reader.path}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'install.json'
            path.write_text(json.dumps(record))
            self.assertEqual(NativeFocusReader.from_deployment(guest, path).path, reader.path)
            record['path'] = '/arbitrary/program'
            path.write_text(json.dumps(record))
            with self.assertRaises(ValueError):
                NativeFocusReader.from_deployment(guest, path)

    def test_browser_reads_focus_after_keyboard_move_without_daemon_tree(self):
        guest = NativeFixture()
        guest.editing = False
        reads = []
        def focused(pid):
            reads.append(pid)
            return {'source': 'ax_first_responder', 'pid': pid, 'identifier': 'URL',
                    'traits': (1 << 21) if guest.editing else 0, 'value': guest.address_value,
                    'frame': {'x': 20, 'y': 488, 'width': 265, 'height': 48}}
        result = Browser(guest=guest, input_settle=0, address_field_reader=focused).goto(
            'https://fixture.invalid/', reuse_tab=True)
        self.assertEqual(result['method'], 'native_address_bar')
        self.assertEqual(reads, [301, 301, 301])
        self.assertEqual([m for m, _ in guest.calls].count('ui.tree'), 0)
        self.assertEqual([m for m, _ in guest.calls].count('input.touch_sequence'), 1)
        self.assertEqual(guest.calls[-1], ('input.key', {'name': 'return'}))

    def test_focus_query_failure_never_falls_back_to_tree_or_sends_text(self):
        guest = NativeFixture()
        reader = Mock(side_effect=GuestError('Native query failed'))
        with self.assertRaises(GuestError):
            Browser(guest=guest, input_settle=0, address_field_reader=reader).goto(
                'https://fixture.invalid/', reuse_tab=True)
        names = [m for m, _ in guest.calls]
        self.assertNotIn('ui.tree', names)
        self.assertNotIn('input.paste', names)

    def test_lost_focus_wrong_owner_and_wrong_readback_never_submit_navigation(self):
        good = {'source': 'ax_first_responder', 'pid': 301, 'identifier': 'URL',
                'traits': 1 << 21, 'value': 'https://fixture.invalid/'}
        for last in [None, {**good, 'pid': 302}, {**good, 'value': 'https://other.invalid/'},
                     {**good, 'traits': 0}]:
            with self.subTest(last=last):
                guest = NativeFixture()
                reader = Mock(side_effect=[copy.deepcopy(good), last])
                with self.assertRaises(GuestError):
                    Browser(guest=guest, input_settle=0, address_field_reader=reader).goto(
                        'https://fixture.invalid/', reuse_tab=True)
                self.assertNotIn(('input.key', {'name': 'return'}), guest.calls)
                self.assertEqual([m for m, _ in guest.calls].count('input.paste'), 1)


if __name__ == '__main__':
    unittest.main()
