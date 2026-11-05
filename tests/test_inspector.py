import asyncio
import json
import socket
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tungtungtungbrowser.inspector import InspectorSession, InspectorError
from tungtungtungbrowser.inspector_transport import InspectorTransport


class NativeProtocolFixture:
    def __init__(self, session):
        self.session = session
        self.commands = []
        self.results = {}
        self.silent = False
        self.outer_error = False

    async def send_socket_data(self, session_id, app_id, page_id, message):
        if message['method'] != 'Target.sendMessageToTarget':
            self.commands.append(message['method'])
            self.session._receive(session_id, {'id': message['id'], 'result': {}})
            return
        nested = json.loads(message['params']['message'])
        self.commands.append(nested['method'])
        target = message['params']['targetId']
        if self.silent:
            return
        if self.outer_error:
            self.session._receive(session_id, {'id': message['id'], 'error': {'message': 'No target'}})
            return
        # Deliver an immediate reply, before the sender resumes; this catches
        # registration-after-send races without timing-dependent sleeps.
        self.session._receive(session_id, {'id': message['id'], 'result': {}})
        result = self.results.get(nested['method'], {})
        self.reply(nested['id'], target, result)

    def reply(self, message_id, target, result):
        self.session._receive(self.session._session_id, {
            'method': 'Target.dispatchMessageFromTarget', 'params': {
                'targetId': target, 'message': json.dumps({'id': message_id, 'result': result})}})


def session_fixture(**options):
    session = InspectorSession(transport=SimpleNamespace(close=AsyncMock()), **options)
    session._app = SimpleNamespace(id_='PID:7')
    session._page = SimpleNamespace(id_=2)
    session.target_id = 'page-a'
    session._targets.add('page-a')
    session._inspector = NativeProtocolFixture(session)
    session._attached = True
    session._inspector.teardown_inspector_socket = AsyncMock()
    session._inspector.close = AsyncMock()
    return session


def response(session, request_id='one', target='page-a', status=200):
    session._event(target, {'method': 'Network.responseReceived', 'params': {
        'requestId': request_id, 'response': {'url': 'https://fixture.invalid/data',
            'status': status, 'headers': {'Content-Type': 'application/json'}, 'mimeType': 'application/json'}}})
    return session.responses[(target, request_id)]


class InspectorContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_immediate_native_replies_and_html_use_dom_without_evaluation(self):
        s = session_fixture()
        s._inspector.results = {'DOM.getDocument': {'root': {'nodeId': 4}},
                                'DOM.getOuterHTML': {'outerHTML': '<p>updated</p>'}}
        self.assertEqual(await s.content(), '<p>updated</p>')
        self.assertEqual(s._inspector.commands, ['DOM.getDocument', 'DOM.getOuterHTML'])
        self.assertFalse(s._pending)
        self.assertFalse(s._outer)

    async def test_timeout_does_not_replay_and_late_response_is_discarded(self):
        s = session_fixture(timeout=0.01)
        s._inspector.silent = True
        with self.assertRaises(TimeoutError):
            await s.reload()
        self.assertEqual(s._inspector.commands, ['Page.reload'])
        s._inspector.reply(1, 'page-a', {})
        self.assertFalse(s._pending)
        self.assertFalse(s._outer)

    async def test_outer_error_does_not_wait_for_command_timeout(self):
        s = session_fixture()
        s._inspector.outer_error = True
        with self.assertRaisesRegex(InspectorError, 'No target'):
            await s.reload()
        self.assertFalse(s._pending)

    async def test_target_destruction_fails_inflight_read_and_wrong_target_cannot_resolve_it(self):
        s = session_fixture()
        s._inspector.silent = True
        task = asyncio.create_task(s._command('DOM.getDocument'))
        await asyncio.sleep(0)
        inner = next(iter(s._pending))
        s._inspector.reply(inner, 'unrelated-worker', {'root': {}})
        self.assertFalse(task.done())
        s._receive(s._session_id, {'method': 'Target.targetDestroyed', 'params': {'targetId': 'page-a'}})
        with self.assertRaisesRegex(InspectorError, 'destroyed'):
            await task

    async def test_provisional_page_is_initialized_without_adopting_worker_or_before_commit(self):
        s = session_fixture()
        s._receive(s._session_id, {'method': 'Target.targetCreated', 'params': {
            'targetInfo': {'targetId': 'worker', 'type': 'worker'}}})
        self.assertNotIn('worker', s._targets)
        s._receive(s._session_id, {'method': 'Target.targetCreated', 'params': {
            'targetInfo': {'targetId': 'page-b', 'type': 'page', 'isProvisional': True}}})
        await asyncio.gather(*s._tasks)
        self.assertEqual(s.target_id, 'page-a')
        self.assertEqual(s._inspector.commands, ['Network.enable', 'Page.enable'])
        s._receive(s._session_id, {'method': 'Target.didCommitProvisionalTarget',
                                 'params': {'oldTargetId': 'page-a', 'newTargetId': 'page-b'}})
        self.assertEqual(s.target_id, 'page-b')

    async def test_response_waiter_registered_before_action_and_body_waits_for_finish(self):
        s = session_fixture()
        s._inspector.results['Network.getResponseBody'] = {'body': '{"answer":42}', 'base64Encoded': False}
        async with s.expect_response('*/data') as wait:
            value = response(s)
        self.assertIs(wait.value, value)
        task = asyncio.create_task(value.json())
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        s._event('page-a', {'method': 'Network.loadingFinished', 'params': {'requestId': 'one'}})
        self.assertEqual(await task, {'answer': 42})
        self.assertFalse(s._waiters)

    async def test_failed_response_and_redirect_cannot_return_wrong_body(self):
        s = session_fixture()
        failed = response(s)
        s._event('page-a', {'method': 'Network.loadingFailed',
                           'params': {'requestId': 'one', 'errorText': 'Connection lost'}})
        with self.assertRaisesRegex(InspectorError, 'Connection lost'):
            await failed.body()
        redirected = response(s, 'two', status=302)
        s._event('page-a', {'method': 'Network.requestWillBeSent', 'params': {
            'requestId': 'two', 'request': {'url': 'https://fixture.invalid/next', 'method': 'GET'}}})
        with self.assertRaisesRegex(InspectorError, 'redirect'):
            await redirected.body()
        self.assertFalse(s._inspector.commands)

    async def test_retention_eviction_is_explicit_and_events_do_not_contain_payloads(self):
        s = session_fixture(max_requests=1, max_events=2)
        old = response(s)
        response(s, 'two')
        response(s, 'three')
        self.assertEqual(len(s.responses), 1)
        self.assertEqual(s.evicted_requests, 2)
        self.assertEqual(s.dropped_events, 1)
        self.assertNotIn('fixture.invalid', json.dumps(list(s.events)))
        with self.assertRaisesRegex(InspectorError, 'evicted'):
            await old.body()

    async def test_disconnect_and_close_release_waiters_and_pending_body(self):
        s = session_fixture()
        value = response(s)
        with self.assertRaisesRegex(InspectorError, 'closed'):
            async with s.expect_request('*'):
                await s.close()
        with self.assertRaisesRegex(InspectorError, 'closed'):
            await value.body()
        await s.close()
        self.assertFalse(s._waiters)
        self.assertFalse(s.responses)
        s.transport.close.assert_awaited_once()
        s._inspector.teardown_inspector_socket.assert_awaited_once()

    async def test_waiter_cancellation_and_timeout_release_subscription(self):
        s = session_fixture()
        async def waiting():
            async with s.expect_request('*', timeout=0.01):
                pass
        with self.assertRaises(TimeoutError):
            await waiting()
        self.assertFalse(s._waiters)
        task = asyncio.create_task(waiting())
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(s._waiters)

    async def test_xpath_search_released_and_css_wait_handles_delayed_attachment(self):
        s = session_fixture()
        s._inspector.results = {'DOM.getDocument': {'root': {'nodeId': 1}},
            'DOM.performSearch': {'searchId': 'search-1', 'resultCount': 1},
            'DOM.getSearchResults': {'nodeIds': [9]}}
        self.assertEqual(await s.query_selector('//button', strategy='xpath'), 9)
        self.assertEqual(s._inspector.commands[-1], 'DOM.discardSearchResults')
        s.query_selector = AsyncMock(side_effect=[None, 9])
        self.assertEqual(await s.wait_for_selector('button', interval=0.05), 9)
        with self.assertRaises(ValueError):
            await s.wait_for_selector('button', state='visible')

    async def test_native_absent_css_node_can_omit_node_id(self):
        s = session_fixture()
        s._inspector.results['DOM.getDocument'] = {'root': {'nodeId': 1}}
        self.assertIsNone(await s.query_selector('.missing'))

    async def test_xpath_expression_does_not_fall_through_to_css_or_ordinary_text(self):
        s = session_fixture()
        expressions = []
        discarded = []
        async def command(method, **params):
            if method == 'DOM.getDocument':
                return {'root': {'nodeId': 1}}
            if method == 'DOM.performSearch':
                query = params['query']
                expressions.append(query)
                self.assertRegex(query, r"^\(.*\)\[boolean\('[0-9a-f]{32}'\)\]$")
                self.assertTrue(params['caseSensitive'])
                # Native evidence: bare `button` adds CSS/text matches, while
                # document-relative XPath button has no matching child.
                count = 7 if query == 'button' else int(query.startswith('(//button)'))
                return {'searchId': 'search', 'resultCount': count}
            if method == 'DOM.getSearchResults':
                return {'nodeIds': [9]}
            if method == 'DOM.discardSearchResults':
                discarded.append(params['searchId'])
                return {}
            self.fail('Unexpected protocol command: ' + method)
        s._command = command
        self.assertIsNone(await s.query_selector('button', strategy='xpath'))
        self.assertEqual(await s.query_selector('//button', strategy='xpath'), 9)
        self.assertIsNone(await s.query_selector('button', strategy='xpath'))
        self.assertNotEqual(expressions[0], expressions[2])
        self.assertEqual(discarded, ['search'] * 3)

    async def test_concurrent_dom_reads_do_not_reset_ids_during_serialization(self):
        s = session_fixture()
        entered, release = asyncio.Event(), asyncio.Event()
        revision = 0
        async def command(method, **params):
            nonlocal revision
            if method == 'DOM.getDocument':
                revision += 1
                return {'root': {'nodeId': revision, 'documentURL': 'https://fixture.invalid/'}}
            if method == 'DOM.getOuterHTML':
                entered.set()
                await release.wait()
                if params['nodeId'] != revision:
                    raise InspectorError('Missing node for given nodeId')
                return {'outerHTML': '<p>current</p>'}
            if method == 'DOM.querySelector':
                self.assertEqual(params['nodeId'], revision)
                return {'nodeId': 9}
            self.fail('Unexpected protocol command: ' + method)
        s._command = command
        html = asyncio.create_task(s.content())
        await entered.wait()
        node = asyncio.create_task(s.query_selector('p'))
        url = asyncio.create_task(s.wait_for_url('https://fixture.invalid/'))
        await asyncio.sleep(0)
        self.assertEqual(revision, 1)
        release.set()
        self.assertEqual(await asyncio.gather(html, node, url),
                         ['<p>current</p>', 9, 'https://fixture.invalid/'])
        self.assertFalse(s._dom_lock.locked())

    async def test_new_page_hold_is_acknowledged_and_resumed_after_setup_failure(self):
        s = session_fixture()
        await s._root_command('Target.setPauseOnStart', pauseOnStart=True)
        s._inspector.outer_error = True
        with self.assertRaises(InspectorError):
            await s._setup_target('page-a', True)
        self.assertEqual(s._inspector.commands[-1], 'Target.resume')
        self.assertFalse(s._root_pending)

    async def test_page_is_not_ready_until_resume_is_acknowledged(self):
        s = session_fixture()
        original = s._inspector.send_socket_data
        resume_id = None
        async def send(session_id, app_id, page_id, message):
            nonlocal resume_id
            if message['method'] == 'Target.resume':
                resume_id = message['id']
                return
            await original(session_id, app_id, page_id, message)
        s._inspector.send_socket_data = send
        task = asyncio.create_task(s._setup_target('page-a', True))
        while resume_id is None:
            await asyncio.sleep(0)
        self.assertFalse(s._target_ready.is_set())
        self.assertFalse(task.done())
        s._receive(s._session_id, {'id': resume_id, 'result': {}})
        await task
        self.assertTrue(s._target_ready.is_set())
        self.assertFalse(s._root_pending)

    async def test_resume_rejection_or_timeout_fails_without_replaying(self):
        for rejected in (True, False):
            with self.subTest(rejected=rejected):
                s = session_fixture(timeout=0.01)
                original = s._inspector.send_socket_data
                resumes = []
                async def send(session_id, app_id, page_id, message):
                    if message['method'] == 'Target.resume':
                        resumes.append(message)
                        if rejected:
                            s._receive(session_id, {'id': message['id'],
                                'error': {'message': 'Target is not paused'}})
                        return
                    await original(session_id, app_id, page_id, message)
                s._inspector.send_socket_data = send
                with self.assertRaises(InspectorError if rejected else TimeoutError):
                    await s._setup_target('page-a', True)
                self.assertEqual(len(resumes), 1)
                self.assertFalse(s._target_ready.is_set())
                self.assertFalse(s._root_pending)

    async def test_paused_frame_is_resumed_without_adopting_it_as_the_page(self):
        s = session_fixture()
        s._receive(s._session_id, {'method': 'Target.targetCreated', 'params': {
            'targetInfo': {'targetId': 'frame-a', 'type': 'frame', 'isPaused': True}}})
        await asyncio.gather(*s._tasks)
        self.assertEqual(s._inspector.commands, ['Target.resume'])
        self.assertEqual(s.target_id, 'page-a')
        self.assertNotIn('frame-a', s._targets)
        self.assertFalse(s._root_pending)

    async def test_large_metadata_is_evicted_under_byte_limit(self):
        s = session_fixture(max_metadata_bytes=1024)
        s._event('page-a', {'method': 'Network.responseReceived', 'params': {
            'requestId': 'large', 'response': {'url': 'https://fixture.invalid/', 'status': 200,
                                             'headers': {'Large': 'x' * 2048}}}})
        self.assertFalse(s.responses)
        self.assertEqual(s.retained_metadata_bytes, 0)
        self.assertEqual(s.evicted_requests, 1)

    async def test_body_size_and_encoding_fail_explicitly(self):
        s = session_fixture()
        value = response(s)
        value._finished.set()
        s._inspector.results['Network.getResponseBody'] = {'body': 'AAEC', 'base64Encoded': True}
        self.assertEqual(await value.body(), b'\0\1\2')
        with self.assertRaisesRegex(InspectorError, 'limit'):
            await value.body(max_bytes=2)


class InspectorControlTest(unittest.IsolatedAsyncioTestCase):
    async def test_direct_stream_delivers_large_frame_and_closes_native_owner(self):
        payload = b'<p>native document</p>' * 65536
        released = asyncio.Event()
        greeting = {'protocol': 2, 'status': 'opened', 'pid': 12,
                    'boot_uuid': '11223344-5566-7788-9900-aabbccddeeff', 'port': 1343}
        async def server(reader, writer):
            try:
                self.assertEqual(await reader.readline(), b'open\n')
                # The greeting and start of the plist can arrive in one packet.
                writer.write(json.dumps(greeting).encode() + b'\n' + struct.pack('>I', len(payload)) + payload)
                await writer.drain()
                self.assertEqual(await reader.read(), b'')
            finally:
                writer.close()
                await writer.wait_closed()
                released.set()
        class Service:
            def __init__(self, reader, writer):
                self.reader, self.writer = reader, writer
            async def close(self):
                self.writer.close()
                await self.writer.wait_closed()
        listener = await asyncio.start_server(server, '127.0.0.1', 0)
        async def connected():
            sock = socket.socket()
            sock.setblocking(False)
            await asyncio.get_running_loop().sock_connect(sock, listener.sockets[0].getsockname())
            return sock
        transport = InspectorTransport(bridge_socket=connected)
        try:
            with patch.dict(sys.modules, {'pymobiledevice3.service_connection': SimpleNamespace(ServiceConnection=Service)}):
                service = await transport.open()
                size, = struct.unpack('>I', await service.reader.readexactly(4))
                self.assertEqual(await asyncio.wait_for(service.reader.readexactly(size), 5), payload)
                await transport.close()
                await asyncio.wait_for(released.wait(), 5)
        finally:
            await transport.close()
            listener.close()
            await listener.wait_closed()

    async def test_bridge_identity_must_remain_stable(self):
        base = {'protocol': 2, 'status': 'opened', 'pid': 12,
                'boot_uuid': '11223344-5566-7788-9900-aabbccddeeff', 'port': 1343}
        transport = InspectorTransport(timeout=0.2)
        transport.reader = asyncio.StreamReader(limit=1024)
        transport.writer = SimpleNamespace(write=lambda value: None, drain=AsyncMock())
        transport.reader.feed_data(json.dumps(base).encode() + b'\n')
        transport.identity = await transport._control('open', 'opened')
        transport.reader.feed_data(json.dumps(base | {'status': 'active', 'pid': 13}).encode() + b'\n')
        with self.assertRaises(InspectorError):
            await transport._control('ping', 'active')

    async def test_bridge_eof_and_oversized_reply_do_not_become_success(self):
        for data in (b'', b'x' * 1500 + b'\n'):
            transport = InspectorTransport(timeout=0.2)
            transport.reader = asyncio.StreamReader(limit=1024)
            transport.writer = SimpleNamespace(write=lambda value: None, drain=AsyncMock())
            transport.reader.feed_data(data)
            transport.reader.feed_eof()
            with self.assertRaises((InspectorError, ValueError)):
                await transport._control('open', 'opened')


if __name__ == '__main__':
    unittest.main()
