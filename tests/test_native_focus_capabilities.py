import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from tungtungtungbrowser import GuestError, NativeFocusReader
from test_native_focus import SHA, TOKEN, answer

READY = {'protocol': 1, 'status': 'ready', 'ui_input_sent': False, 'ax_queried': False}


class FocusCapabilityTests(unittest.TestCase):
    def deployment(self, check):
        guest = Mock()
        reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
        report = {'status': 'complete', 'helper_sha256': SHA, 'deployment_id': TOKEN, 'path': reader.path}
        if check is not None:
            report['native_check'] = check
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'install.json'
            p.write_text(json.dumps(report))
            result = NativeFocusReader.from_deployment(guest, p)
        guest._rpc.assert_not_called()
        return result

    def test_older_deployments_keep_first_responder_behavior(self):
        for check in (None, READY):
            with self.subTest(check=check):
                self.assertFalse(self.deployment(check).supports_address_resolution)

    def test_new_deployment_enables_resolver_without_preliminary_ax_query(self):
        self.assertTrue(self.deployment({**READY, 'address_resolution': 'guarded-v1'}).supports_address_resolution)
        guest = Mock(); guest._rpc.return_value = answer({**READY, 'address_resolution': 'guarded-v1'})
        reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
        self.assertFalse(reader.supports_address_resolution)
        reader.check()
        self.assertTrue(reader.supports_address_resolution)
        guest._rpc.assert_called_once()
        self.assertIn('["check"]', guest._rpc.call_args.kwargs['source'])

    def test_malformed_capability_evidence_does_not_authorize_resolution(self):
        cases = [None, {}, {**READY, 'address_resolution': True}, {**READY, 'extra': True},
                 {**READY, 'protocol': True}, {**READY, 'ui_input_sent': True},
                 {**READY, 'ax_queried': 0}, {**READY, 'ui_input_sent': 0}]
        for check in cases:
            with self.subTest(check=check):
                reader = NativeFocusReader(Mock(), sha256=SHA, deployment_id=TOKEN)
                with self.assertRaises(GuestError):
                    reader._accept_check(check)
                self.assertFalse(reader.supports_address_resolution)

    def test_phase_capability_selects_execution_deadline_without_changing_http_timeout(self):
        from test_native_focus import native_result
        from tungtungtungbrowser.guest import Guest
        for check, timeout in [(READY, 30), ({**READY, 'address_resolution': 'guarded-v1'}, 30),
                               ({**READY, 'address_resolution': 'guarded-v1', 'query_timing': 'phased-v1'}, 60)]:
            with self.subTest(check=check):
                reader = self.deployment(check)
                reader.guest = Guest(timeout=7)
                factory = reader.guest.connection_factory
                self.assertEqual(factory().timeout, 7)
                reader.guest._rpc = Mock()
                reader.guest._rpc.return_value = answer(native_result())
                self.assertEqual(reader(301)['pid'], 301)
                self.assertEqual(reader.guest._rpc.call_args.kwargs['timeout'], timeout)
                self.assertIs(reader.guest.connection_factory, factory)
                self.assertEqual(reader.guest.connection_factory().timeout, 7)
                reader.guest._rpc.return_value = answer(check)
                reader.check()
                self.assertEqual(reader.guest._rpc.call_args.kwargs['timeout'], 60)

    def test_unknown_or_incomplete_timing_capability_is_rejected(self):
        for fields in [{'query_timing': 'phased-v1'},
                       {'address_resolution': 'guarded-v1', 'query_timing': 'phased-v2'},
                       {'address_resolution': 'guarded-v1', 'query_timing': 60},
                       {'address_resolution': 'guarded-v1', 'query_timing': 'phased-v1', 'extra': True}]:
            with self.subTest(fields=fields):
                with self.assertRaises(GuestError):
                    self.deployment({**READY, **fields})

    def test_phase_deadline_does_not_retry_a_failed_process_or_missing_reply(self):
        check = {**READY, 'address_resolution': 'guarded-v1', 'query_timing': 'phased-v1'}
        process_failure = {'ok': True, 'crashed': False, 'output': '', 'result': json.dumps({
            'status': -1, 'signal': 14, 'stderr': '', 'stdout': ''})}
        for reply in [process_failure, GuestError('caller HTTP timeout')]:
            with self.subTest(reply=reply):
                reader = self.deployment(check)
                reader.guest._rpc.side_effect = [reply, answer(READY)]
                with self.assertRaises(GuestError):
                    reader.resolve_address_field(301)
                reader.guest._rpc.assert_called_once()
                self.assertEqual(reader.last_resolution_attempts[0]['status'], 'failed')

    def test_initial_check_allows_script_startup_without_extending_unknown_queries(self):
        from test_native_focus import native_result
        guest = Mock(); guest._rpc.return_value = answer(READY)
        reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
        reader.check()
        self.assertEqual(guest._rpc.call_args.kwargs['timeout'], 60)
        guest._rpc.return_value = answer(native_result())
        reader(301)
        self.assertEqual(guest._rpc.call_args.kwargs['timeout'], 30)
        guest.call.assert_not_called()

    def test_failed_check_is_terminal_and_does_not_enable_queries(self):
        for reply in [GuestError('daemon check timeout'),
                      {'ok': False, 'crashed': True, 'output': '', 'result': ''}]:
            guest = Mock(); guest._rpc.side_effect = [reply, answer(READY)]
            reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
            with self.assertRaises(GuestError):
                reader.check()
            guest._rpc.assert_called_once()
            self.assertFalse(reader.supports_address_resolution)
            self.assertEqual(reader._query_script_timeout, 30)
            guest.call.assert_not_called()
