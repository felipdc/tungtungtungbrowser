import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tungtungtungbrowser.inspector import _direct_inspector


class ApplicationDisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_notifies_after_upstream_removes_application_and_pages(self):
        class Protocol:
            def __init__(self, lockdown):
                self.receive_handlers = {}
                self.connected_application = {'PID:301': object(), 'PID:999': object()}
                self.application_pages = {'PID:301': {'1': object()}, 'PID:999': {}}

            async def _handle_application_disconnected(self, argument):
                self.connected_application.pop(argument['WIRApplicationIdentifierKey'], None)
                self.application_pages.pop(argument['WIRApplicationIdentifierKey'], None)

        with patch.dict(sys.modules, {
                'pymobiledevice3.exceptions': SimpleNamespace(ConnectionTerminatedError=RuntimeError),
                'pymobiledevice3.services.webinspector': SimpleNamespace(WebinspectorService=Protocol)}):
            adapter = _direct_inspector(object(), Mock(), Mock())
        observed = []
        def disconnected(app_id):
            observed.append((app_id, set(adapter.connected_application), set(adapter.application_pages)))
        adapter.on_application_disconnected = disconnected
        await adapter.receive_handlers['_rpc_applicationDisconnected:']({
            'WIRApplicationIdentifierKey': 'PID:301'})
        self.assertEqual(observed, [('PID:301', {'PID:999'}, {'PID:999'})])
