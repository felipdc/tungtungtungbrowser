import asyncio
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tungtungtungbrowser.inspector import _direct_inspector, _InitialInspectorEOF


class ConnectionTerminatedError(Exception):
    pass


class InitialReplyTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self, service, *, send_error=None, handle_error=None):
        class Protocol:
            def __init__(self, lockdown):
                self._recv_task = None
                self.receive_handlers = {}
                self._report_identifier = AsyncMock(side_effect=send_error)
                self._handle_recv = AsyncMock(side_effect=handle_error)

            @property
            def service(self):
                return self._service

        disconnected = Mock()
        with patch.dict(sys.modules, {
                'pymobiledevice3.exceptions': SimpleNamespace(ConnectionTerminatedError=ConnectionTerminatedError),
                'pymobiledevice3.services.webinspector': SimpleNamespace(WebinspectorService=Protocol)}):
            adapter = _direct_inspector(service, Mock(), disconnected)
        return adapter, disconnected

    async def test_only_eof_receiving_first_reply_is_recoverable(self):
        error = ConnectionTerminatedError('EOF')
        error.__cause__ = asyncio.IncompleteReadError(b'', 4)
        service = SimpleNamespace(recv_plist=AsyncMock(side_effect=error))
        adapter, disconnected = self.adapter(service)
        with self.assertRaises(_InitialInspectorEOF) as caught:
            await adapter.connect()
        self.assertIs(caught.exception.__cause__, error)
        adapter._report_identifier.assert_awaited_once()
        adapter._handle_recv.assert_not_awaited()
        self.assertIsNone(adapter._recv_task)
        disconnected.assert_not_called()

    async def test_send_failure_timeout_and_malformed_reply_are_not_recoverable(self):
        for stage, error in [('send', ConnectionTerminatedError()), ('receive', TimeoutError()),
                             ('receive', ValueError('invalid plist')), ('handle', ConnectionTerminatedError())]:
            service = SimpleNamespace(recv_plist=AsyncMock(return_value={}))
            if stage == 'receive':
                service.recv_plist.side_effect = error
            adapter, _ = self.adapter(service, send_error=error if stage == 'send' else None,
                                      handle_error=error if stage == 'handle' else None)
            with self.assertRaises(type(error)):
                await adapter.connect()
            self.assertIsNone(adapter._recv_task)

    async def test_eof_after_first_reply_notifies_disconnect_without_reinitializing(self):
        error = ConnectionTerminatedError('later EOF')
        service = SimpleNamespace(recv_plist=AsyncMock(side_effect=[{}, error]))
        adapter, disconnected = self.adapter(service)
        await adapter.connect()
        await adapter._recv_task
        disconnected.assert_called_once_with(error)
        adapter._report_identifier.assert_awaited_once()
        self.assertTrue(adapter._connection_lost)

    async def test_initial_wrapped_io_errors_are_terminal(self):
        for cause in (None, OSError('I/O failure'), ConnectionResetError(), TimeoutError()):
            with self.subTest(cause=cause):
                error = ConnectionTerminatedError()
                error.__cause__ = cause
                service = SimpleNamespace(recv_plist=AsyncMock(side_effect=error))
                adapter, disconnected = self.adapter(service)
                with self.assertRaises(ConnectionTerminatedError) as caught:
                    await adapter.connect()
                self.assertIs(caught.exception, error)
                self.assertIsNone(adapter._recv_task)
                disconnected.assert_not_called()
