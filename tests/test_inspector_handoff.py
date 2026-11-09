import asyncio
import json
import socket
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tungtungtungbrowser.inspector_transport import InspectorError, InspectorTransport


IDENTITY = {'protocol': 2, 'pid': 42, 'boot_uuid': '11223344-5566-7788-9900-aabbccddeeff'}
OPENED = IDENTITY | {'status': 'opened', 'port': 1343}
INTERRUPTED = IDENTITY | {'status': 'open_failed', 'port': 0, 'handoff_error': 'connection_interrupted'}
ACK_TIMEOUT = INTERRUPTED | {'handoff_error': 'acknowledgment_timeout'}


class HandoffRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.replies = []
        self.connections = []
        self.handlers = set()
        self.services = []
        self.first_closed = asyncio.Event()
        self.listener = await asyncio.start_server(self.accept, '127.0.0.1', 0)
        services = self.services

        class Service:
            def __init__(self, reader, writer):
                self.reader, self.writer = reader, writer
                services.append(self)

            async def close(self):
                self.writer.close()
                await self.writer.wait_closed()

        self.module = patch.dict(sys.modules, {
            'pymobiledevice3.service_connection': SimpleNamespace(ServiceConnection=Service)})
        self.module.start()

    async def asyncTearDown(self):
        self.listener.close()
        await self.listener.wait_closed()
        for task in self.handlers:
            task.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)
        self.module.stop()

    async def accept(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        row = {'command': await reader.readline()}
        index = len(self.connections)
        self.connections.append(row)
        try:
            reply = self.replies[index]
            if reply is not None:
                writer.write(json.dumps(reply).encode() + b'\n')
                await writer.drain()
            row['tail'] = await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            self.first_closed.set()

    async def connect(self):
        # A new attempt must not retain the old owner connection.
        if self.connections:
            await asyncio.wait_for(self.first_closed.wait(), 1)
        sock = socket.socket()
        sock.setblocking(False)
        await asyncio.get_running_loop().sock_connect(sock, self.listener.sockets[0].getsockname())
        return sock

    def transport(self, **options):
        return InspectorTransport(bridge_socket=self.connect, **options)

    async def test_interrupted_handoff_recovers_before_any_inspector_frames(self):
        self.replies = [INTERRUPTED, OPENED]
        transport = self.transport()
        await transport.open()
        self.assertEqual(transport.identity, OPENED)
        self.assertEqual([r['status'] for r in transport.handoff_attempts], ['rejected', 'opened'])
        self.assertEqual(len(self.services), 1)
        await transport.close()
        await asyncio.gather(*self.handlers)
        self.assertEqual(self.connections, [{'command': b'open\n', 'tail': b''}] * 2)

    async def test_exhaustion_and_opt_out_preserve_failure(self):
        for rejection, attempts in ((r, n) for r in (INTERRUPTED, ACK_TIMEOUT) for n in (1, 3, 4)):
            with self.subTest(reason=rejection['handoff_error'], attempts=attempts):
                before = len(self.connections)
                self.replies.extend([rejection] * attempts)
                transport = self.transport(max_open_attempts=attempts)
                with self.assertRaisesRegex(InspectorError, rejection['handoff_error']):
                    await transport.open()
                self.assertEqual(len(self.connections) - before, attempts)
                self.assertEqual(len(transport.handoff_attempts), attempts)
                self.assertTrue(transport.closed)
                self.assertIsNone(transport.writer)
        self.assertFalse(self.services)

    async def test_terminal_ack_timeout_closes_owner_and_recovers_without_plist_frames(self):
        self.replies = [ACK_TIMEOUT, OPENED]
        transport = self.transport()
        await transport.open()
        self.assertEqual(transport.handoff_attempts[0]['reason'], 'acknowledgment_timeout')
        self.assertEqual([r['status'] for r in transport.handoff_attempts], ['rejected', 'opened'])
        self.assertEqual(len(self.services), 1)
        await transport.close()
        await asyncio.gather(*self.handlers)
        self.assertEqual(self.connections, [{'command': b'open\n', 'tail': b''}] * 2)

    async def test_interruption_and_terminal_ack_timeout_share_one_attempt_budget(self):
        self.replies = [INTERRUPTED, ACK_TIMEOUT, OPENED]
        transport = self.transport()
        await transport.open()
        self.assertEqual([r.get('reason') for r in transport.handoff_attempts],
                         ['connection_interrupted', 'acknowledgment_timeout', None])
        self.assertEqual(len(self.services), 1)
        await transport.close()
        await asyncio.gather(*self.handlers)
        self.assertEqual(self.connections, [{'command': b'open\n', 'tail': b''}] * 3)

    async def test_explicit_fourth_attempt_requires_three_completed_rejections(self):
        self.replies = [INTERRUPTED, ACK_TIMEOUT, INTERRUPTED, OPENED]
        transport = self.transport(max_open_attempts=4)
        await transport.open()
        self.assertEqual([r['status'] for r in transport.handoff_attempts],
                         ['rejected', 'rejected', 'rejected', 'opened'])
        await transport.close()
        await asyncio.gather(*self.handlers)
        self.assertEqual(self.connections, [{'command': b'open\n', 'tail': b''}] * 4)

    async def test_fourth_attempt_retains_identity_and_shared_deadline(self):
        self.replies = [ACK_TIMEOUT] * 3 + [OPENED | {'pid': 99}]
        transport = self.transport(max_open_attempts=4)
        with self.assertRaisesRegex(InspectorError, 'Invalid native Inspector handoff'):
            await transport.open()
        self.assertEqual(len(transport.handoff_attempts), 4)
        self.assertFalse(self.services)
        self.replies.append(None)
        transport = self.transport(max_open_attempts=4, timeout=.05)
        with self.assertRaises(TimeoutError): await transport.open()
        self.assertEqual(len(transport.handoff_attempts), 1)
        self.assertTrue(transport.closed)

    async def test_legacy_unknown_and_other_rejections_are_not_retried(self):
        for reason in (None, 'unknown', 'connection_invalid',
                       'termination_imminent', 'initialization_failed', 'unexpected_xpc_event'):
            with self.subTest(reason=reason):
                reply = INTERRUPTED.copy()
                if reason is None:
                    del reply['handoff_error']
                else:
                    reply['handoff_error'] = reason
                self.replies.append(reply)
                transport = self.transport()
                with self.assertRaisesRegex(InspectorError, 'open_failed'):
                    await transport.open()
                self.assertEqual(len(transport.handoff_attempts), 1)
        self.assertFalse(self.services)

    async def test_identity_change_on_second_attempt_is_fatal(self):
        changes = ({'pid': 43}, {'boot_uuid': 'aabbccdd-5566-7788-9900-aabbccddeeff'})
        for rejection, changed in ((r, c) for r in (INTERRUPTED, ACK_TIMEOUT) for c in changes):
            with self.subTest(reason=rejection['handoff_error'], changed=changed):
                self.replies.extend([rejection, OPENED | changed])
                transport = self.transport()
                with self.assertRaisesRegex(InspectorError, 'Invalid native Inspector handoff'):
                    await transport.open()
                self.assertEqual(len(transport.handoff_attempts), 2)
        self.assertFalse(self.services)

    async def test_deadline_bounds_the_entire_sequence_and_silent_reply_is_not_retried(self):
        for reply in (INTERRUPTED, ACK_TIMEOUT, None):
            with self.subTest(reply=reply):
                self.replies.append(reply)
                transport = self.transport(timeout=0.05)
                with self.assertRaises(TimeoutError):
                    await transport.open()
                self.assertEqual(len(transport.handoff_attempts), 1)
                self.assertTrue(transport.closed)
        self.assertFalse(self.services)

    async def test_cancellation_after_rejection_does_not_open_another_connection(self):
        self.replies = [INTERRUPTED]
        transport = self.transport()
        task = asyncio.create_task(transport.open())
        await asyncio.wait_for(self.first_closed.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(transport.closed)
        self.assertEqual(len(self.connections), 1)
        self.assertFalse(self.services)

    async def test_malformed_failed_identity_and_invalid_attempt_limit_are_rejected(self):
        for changed in ({'pid': True}, {'port': 1343}, {'boot_uuid': []}, {'protocol': True}):
            self.replies.append(INTERRUPTED | changed)
            transport = self.transport()
            with self.assertRaisesRegex(InspectorError, 'Invalid native Inspector handoff'):
                await transport.open()
            self.assertEqual(len(transport.handoff_attempts), 1)
        for attempts in (0, 5, True, 1.0, None):
            with self.assertRaises(ValueError):
                self.transport(max_open_attempts=attempts)

    async def test_initial_eof_shares_budget_and_closes_old_stream_before_recovery(self):
        self.replies = [OPENED, INTERRUPTED, OPENED]
        transport = self.transport()
        first = await transport.open()
        second = await transport._recover_initial_eof()
        self.assertIsNot(first, second)
        self.assertTrue(first.writer.is_closing())
        self.assertEqual(len(self.connections), 3)
        self.assertEqual(transport.handoff_attempts[0]['initial_reply_status'], 'eof')
        self.assertEqual([r['status'] for r in transport.handoff_attempts], ['opened', 'rejected', 'opened'])
        self.assertIsNone(await transport._recover_initial_eof())
        self.assertEqual(len(self.connections), 3)
        with self.assertRaisesRegex(InspectorError, 'single-use'):
            await transport.open()
        await transport.close()

    async def test_initial_eof_opt_out_and_bridge_identity_change(self):
        self.replies = [OPENED]
        transport = self.transport(max_open_attempts=1)
        await transport.open()
        self.assertIsNone(await transport._recover_initial_eof())
        self.assertEqual(len(self.connections), 1)
        await transport.close()
        for changed in ({'pid': 43}, {'boot_uuid': 'aabbccdd-5566-7788-9900-aabbccddeeff'}):
            self.replies.extend([OPENED, OPENED | changed])
            transport = self.transport()
            await transport.open()
            with self.assertRaisesRegex(InspectorError, 'Invalid native Inspector handoff'):
                await transport._recover_initial_eof()
            self.assertTrue(transport.closed)
            self.assertEqual(len(transport.handoff_attempts), 2)

    async def test_initial_eof_cleanup_failure_prevents_replacement(self):
        self.replies = [OPENED]
        transport = self.transport()
        service = await transport.open()
        async def failed_close():
            raise OSError('close failed')
        service.close = failed_close
        with self.assertRaisesRegex(InspectorError, 'could not be closed'):
            await transport._recover_initial_eof()
        self.assertTrue(transport.closed)
        self.assertTrue(service.writer.is_closing())
        self.assertEqual(len(self.connections), 1)

    async def test_initial_eof_cancellation_during_backoff_never_reconnects(self):
        self.replies = [OPENED]
        transport = self.transport()
        await transport.open()
        task = asyncio.create_task(transport._recover_initial_eof())
        await asyncio.wait_for(self.first_closed.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(transport.closed)
        self.assertEqual(len(self.connections), 1)
