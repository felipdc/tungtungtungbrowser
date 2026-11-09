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
