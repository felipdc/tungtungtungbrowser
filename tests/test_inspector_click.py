import asyncio
import base64
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_inspector_focus import focused_session
from tungtungtungbrowser.inspector import DocumentChangedError, InspectorError
from tungtungtungbrowser.guest import GuestTransportError
from tungtungtungbrowser.pointer import PointerGeometryError

try:
    import PIL
except ImportError:
    PIL = None


@unittest.skipIf(PIL is None, 'Install the pointer extra for click protocol tests')
class InspectorClickTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settle = patch('tungtungtungbrowser.inspector._POINTER_SETTLE_SECONDS', 0)
        self.settle.start()
        self.geometry = patch('tungtungtungbrowser.pointer.highlight_point', return_value={
            'x': 100, 'y': 200, 'screen_width': 393, 'screen_height': 852, 'coordinate_scale': 3})
        self.point = self.geometry.start()
        self.addCleanup(self.settle.stop)
        self.addCleanup(self.geometry.stop)
        self.loop = asyncio.get_running_loop()

    def fixture(self, *, hit=9, selection=True):
        session = focused_session(timeout=1)
        session._app.pid = 7
        session._inspector.results['DOM.getDocument'] = {'root': {'nodeId': 1, 'children': [
            {'nodeId': 9, 'children': [{'nodeId': 10}]}]}}
        session._inspector.results['DOM.requestNode'] = {'nodeId': hit}
        self.inputs = []
        self.operations = []
        self.foreground = {'bundle_id': 'com.apple.mobilesafari', 'verified': True, 'pid': 7}
        self.contact_error = None
        def call(method):
            self.operations.append(method)
            if method == 'apps.foreground':
                return self.foreground
            if method == 'screen.screenshot':
                return {'mime_type': 'image/jpeg', 'data': base64.b64encode(b'\xff\xd8\xff\xd9').decode(),
                        'width': 1179, 'height': 2556, 'coordinate_scale': 3}
            raise AssertionError(method)
        def tap(x, y):
            self.inputs.append((x, y))
            self.operations.append('touch')
            if self.contact_error:
                raise self.contact_error
            if selection and len(self.inputs) == 1:
                self.loop.call_soon_threadsafe(session._event, 'page-a', {'method': 'Inspector.inspect',
                    'params': {'object': {'subtype': 'node', 'objectId': 'native-node'}}})
            return {'requested': True}
        session.browser = SimpleNamespace(guest=SimpleNamespace(touch_helper_sha256='a' * 64, call=call), tap=tap)
        original = session._command
        self.commands = []
        async def command(method, **params):
            self.commands.append((method, params))
            return await original(method, **params)
        session._command = command
        return session

    async def test_css_xpath_and_nested_content_use_picker_then_one_real_touch(self):
        for strategy in ['css', 'xpath']:
            for hit in [9, 10]:
                session = self.fixture(hit=hit)
                result = await session.click('button' if strategy == 'css' else '//button', strategy=strategy)
                self.assertTrue(result['click_dispatched'])
                self.assertEqual(result['hit_node_id'], hit)
                self.assertFalse(result['page_result_verified'])
                self.assertEqual(self.inputs, [(100, 200), (100, 200)])
                self.assertEqual([p['enabled'] for m, p in self.commands if m == 'DOM.setInspectModeEnabled'], [True, False])
                self.assertFalse(any(m.startswith('Runtime.') for m, _ in self.commands))
                self.assertIsNone(session._pointer_state)
                self.assertFalse(session._pointer_actions)

    async def test_covering_element_receives_only_selection_gesture(self):
        session = self.fixture(hit=20)
        with self.assertRaisesRegex(InspectorError, 'covers'):
            await session.click('button')
        self.assertEqual(len(self.inputs), 1)

    async def test_missing_ambiguous_disabled_and_wrong_foreground_send_no_input(self):
        for failure in ['missing', 'ambiguous', 'disabled', 'wrong_app', 'wrong_pid']:
            session = self.fixture()
            if failure in ['missing', 'ambiguous']:
                session._inspector.results['DOM.querySelectorAll']['nodeIds'] = [] if failure == 'missing' else [9, 10]
            elif failure == 'disabled':
                session._inspector.results['DOM.getAccessibilityPropertiesForNode']['properties']['disabled'] = True
            elif failure == 'wrong_app':
                self.foreground['bundle_id'] = 'com.example.other'
            else:
                self.foreground['pid'] = 8
            with self.assertRaises(InspectorError):
                await session.click('button')
            self.assertEqual(self.inputs, [])
            self.assertNotIn('DOM.highlightNode', [m for m, _ in self.commands])

    async def test_background_or_offscreen_highlight_failure_is_restored_without_touch(self):
        session = self.fixture()
        self.point.side_effect = PointerGeometryError('No visible highlight')
        with self.assertRaisesRegex(InspectorError, 'visible highlight'):
            await session.click('button')
        self.assertEqual(self.inputs, [])
        self.assertEqual([m for m, _ in self.commands].count('DOM.hideHighlight'), 1)

    async def test_document_change_between_captures_never_dispatches_input(self):
        session = self.fixture()
        original = session.browser.guest.call
        def changed(method):
            result = original(method)
            if method == 'screen.screenshot':
                self.loop.call_soon_threadsafe(session._event, 'page-a', {'method': 'DOM.documentUpdated'})
            return result
        session.browser.guest.call = changed
        with self.assertRaises(DocumentChangedError):
            await session.click('button')
        self.assertEqual(self.inputs, [])
        self.assertIn('DOM.hideHighlight', [m for m, _ in self.commands])

    async def test_disconnected_capture_gets_one_fresh_read_before_any_contact(self):
        for failed_capture in (1, 2):
            session = self.fixture()
            original = session.browser.guest.call
            captures = []
            def capture(method):
                if method == 'screen.screenshot':
                    captures.append(method)
                    self.assertEqual(self.inputs, [])
                    if len(captures) == failed_capture:
                        raise GuestTransportError('receive_headers', OSError('peer closed'))
                return original(method)
            session.browser.guest.call = capture
            result = await session.click('button')
            self.assertEqual(len(captures), 3)
            self.assertEqual(result['capture_read_retries'], 1)
            self.assertEqual(self.operations.count('apps.foreground'), 2)
            self.assertEqual(len(self.inputs), 2)

    async def test_capture_retry_exhaustion_restores_highlight_without_contact(self):
        for failures in ((1, 2), (1, 3)):
            session = self.fixture()
            original = session.browser.guest.call
            captures = []
            def capture(method):
                if method == 'screen.screenshot':
                    captures.append(method)
                    if len(captures) in failures:
                        raise GuestTransportError('receive_headers', OSError('peer closed'))
                return original(method)
            session.browser.guest.call = capture
            with self.assertRaises(GuestTransportError):
                await session.click('button')
            self.assertEqual(len(captures), failures[-1])
            self.assertEqual(self.inputs, [])
            self.assertIn('DOM.hideHighlight', [m for m, _ in self.commands])

    async def test_capture_retry_requires_unchanged_document_and_foreground(self):
        for changed in ('document', 'foreground'):
            session = self.fixture()
            original = session.browser.guest.call
            captures = []
            def capture(method):
                if method == 'screen.screenshot':
                    captures.append(method)
                    if changed == 'document':
                        self.loop.call_soon_threadsafe(session._event, 'page-a', {'method': 'DOM.documentUpdated'})
                    else:
                        self.foreground['pid'] = 8
                    raise GuestTransportError('receive_headers', OSError('peer closed'))
                return original(method)
            session.browser.guest.call = capture
            with self.assertRaises(InspectorError):
                await session.click('button')
            self.assertEqual(len(captures), 1)
            self.assertEqual(self.inputs, [])

    async def test_capture_failure_in_other_transport_phases_is_terminal(self):
        for phase in ('connect', 'send_request', 'read_body'):
            session = self.fixture()
            original = session.browser.guest.call
            captures = []
            def capture(method):
                if method == 'screen.screenshot':
                    captures.append(method)
                    raise GuestTransportError(phase, OSError('transport failure'))
                return original(method)
            session.browser.guest.call = capture
            with self.assertRaises(GuestTransportError):
                await session.click('button')
            self.assertEqual(len(captures), 1)
            self.assertEqual(self.inputs, [])

    async def test_cancelled_retry_foreground_read_cannot_capture_or_touch(self):
        session = self.fixture()
        original = session.browser.guest.call
        started, release = threading.Event(), threading.Event()
        captures = []
        def capture(method):
            if method == 'screen.screenshot':
                captures.append(method)
                raise GuestTransportError('receive_headers', OSError('peer closed'))
            if captures and method == 'apps.foreground':
                started.set()
                release.wait(2)
            return original(method)
        session.browser.guest.call = capture
        task = asyncio.create_task(session.click('button'))
        await asyncio.to_thread(started.wait, 1)
        self.assertTrue(started.is_set())
        task.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(captures), 1)
        self.assertEqual(self.inputs, [])
        self.assertFalse(session._native_tasks)

    async def test_selection_timeout_restores_mode_and_does_not_click(self):
        session = self.fixture(selection=False)
        with self.assertRaises(TimeoutError):
            await session.click('button', timeout=0.05)
        self.assertEqual(len(self.inputs), 1)
        self.assertEqual([p['enabled'] for m, p in self.commands if m == 'DOM.setInspectModeEnabled'], [True, False])
        self.assertFalse(session._native_tasks)

    async def test_uncertain_selection_contact_is_not_repeated(self):
        session = self.fixture()
        self.contact_error = OSError('native reply lost')
        with self.assertRaises(OSError):
            await session.click('button')
        self.assertEqual(len(self.inputs), 1)
        self.assertEqual([p['enabled'] for m, p in self.commands if m == 'DOM.setInspectModeEnabled'], [True, False])

    async def test_uncertain_real_click_is_not_replayed(self):
        session = self.fixture()
        original = session.browser.tap
        def contact(x, y):
            if len(self.inputs) == 1:
                self.inputs.append((x, y))
                raise OSError('real click reply lost')
            return original(x, y)
        session.browser.tap = contact
        with self.assertRaisesRegex(OSError, 'real click reply lost'):
            await session.click('button')
        self.assertEqual(len(self.inputs), 2)
        self.assertFalse(session._native_tasks)

    async def test_target_removed_by_selection_cannot_receive_the_real_click(self):
        session = self.fixture()
        original = session._command
        async def command(method, **params):
            result = await original(method, **params)
            if method == 'DOM.requestNode':
                session._event('page-a', {'method': 'DOM.childNodeRemoved',
                                         'params': {'parentNodeId': 1, 'nodeId': 9}})
            return result
        session._command = command
        with self.assertRaises(InspectorError):
            await session.click('button')
        self.assertEqual(len(self.inputs), 1)

    async def test_repeated_cancellation_joins_contact_before_restoring_mode(self):
        session = self.fixture(selection=False)
        started, release = threading.Event(), threading.Event()
        def contact(x, y):
            self.inputs.append((x, y));started.set();release.wait(2)
        session.browser.tap = contact
        task = asyncio.create_task(session.click('button'))
        await asyncio.to_thread(started.wait, 1)
        self.assertTrue(started.is_set())
        task.cancel();await asyncio.sleep(0);task.cancel();await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertTrue(session._native_lock.locked())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(self.inputs), 1)
        self.assertEqual([p['enabled'] for m, p in self.commands if m == 'DOM.setInspectModeEnabled'], [True, False])
        self.assertFalse(session._native_tasks)

    async def test_close_waits_for_pointer_restoration_before_teardown(self):
        session = self.fixture(selection=False)
        task = asyncio.create_task(session.click('button'))
        while not self.inputs:
            await asyncio.sleep(0.001)
        await session.close()
        with self.assertRaises((asyncio.CancelledError, InspectorError)):
            await task
        methods = [m for m, _ in self.commands]
        self.assertEqual([p['enabled'] for m, p in self.commands if m == 'DOM.setInspectModeEnabled'], [True, False])
        self.assertFalse(session._pointer_actions)
        self.assertIsNone(session._pointer_state)
        self.assertEqual(session.cleanup_errors, [])
        session._inspector.teardown_inspector_socket.assert_awaited_once()

    async def test_failed_restoration_poison_session_and_is_retained(self):
        session = self.fixture(selection=False)
        original = session._command
        async def command(method, **params):
            if method == 'DOM.setInspectModeEnabled' and params.get('enabled') is False:
                raise OSError('restoration connection lost')
            return await original(method, **params)
        session._command = command
        with self.assertRaisesRegex(InspectorError, 'restoration'):
            await session.click('button', timeout=0.05)
        with self.assertRaises(InspectorError):
            await session.reload()
        self.assertEqual(len(self.inputs), 1)
        self.assertTrue(session.cleanup_errors)
