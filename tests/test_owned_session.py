import asyncio
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tungtungtungbrowser import Browser, InspectorError, InspectorSession
from test_browser import NativeFixture
from test_inspector import NativeProtocolFixture


def page(identifier, url):
    return SimpleNamespace(id_=identifier, web_url=url, web_connection_id='',
                           type_=SimpleNamespace(value='WIRTypeWebPage'))


class TabFixture(NativeProtocolFixture):
    """Native protocol replies plus separate physical tab/input state."""
    def __init__(self, session, guest):
        super().__init__(session)
        self.guest = guest
        self.app = SimpleNamespace(id_='PID:301', pid=301, bundle='com.apple.mobilesafari')
        self.connected_application = {self.app.id_: self.app}
        self.pages = {'1': page(1, guest.address_value)}
        self.application_pages = {self.app.id_: self.pages}
        self.subscribers = set()
        self.listing_count = 0
        self.events = []
        self.open_mode = 'new'
        self.close_mode = 'close'
        self.connect = AsyncMock()
        self.close = AsyncMock()
        self._get_connected_applications = AsyncMock()
        self.actual_call = guest.call
        guest.call = self.native_call

    def native_call(self, method, **params):
        result = self.actual_call(method, **params)
        if method == 'apps.open_url':
            self.events.append('open')
            self.guest.address_value = params['url']
            if self.open_mode in ('new', 'new_lost_reply'):
                self.pages['2'] = page(2, params['url'])
                if self.open_mode == 'new_lost_reply':
                    raise InspectorError('Open reply lost after creating tab')
            elif self.open_mode == 'reuse':
                self.pages['1'].web_url = params['url']
            elif self.open_mode == 'uncertain':
                raise InspectorError('Open reply lost')
        if method == 'input.key' and params['name'] == 'return':
            self.pages['2'].web_url = self.guest.address_value
        if method == 'input.key' and params['name'] == 'cmd+w':
            self.events.append('close')
            if self.close_mode == 'uncertain':
                raise InspectorError('Close reply lost')
            if self.close_mode == 'close':
                self.pages.pop('2', None)
                self.guest.address_value = self.pages['1'].web_url
        return result

    def subscribe_listings(self):
        queue = asyncio.Queue()
        self.subscribers.add(queue)
        return queue

    def unsubscribe_listings(self, queue):
        self.subscribers.remove(queue)

    async def _forward_get_listing(self, app_id):
        self.listing_count += 1
        # Immediate notification with string dictionary keys reproduces the
        # real library's behavior and catches registration/order/type mistakes.
        for queue in self.subscribers:
            queue.put_nowait(app_id)

    async def setup_inspector_socket(self, session_id, app_id, page_id, **options):
        self.events.append('attach')
        self.pages[str(page_id)].web_connection_id = 'our-connection'
        self.session._receive(session_id, {'method': 'Target.targetCreated', 'params': {
            'targetInfo': {'targetId': 'target', 'type': 'page'}}})

    async def teardown_inspector_socket(self, session_id, app_id, page_id):
        self.events.append('detach')
        if str(page_id) in self.pages:
            self.pages[str(page_id)].web_connection_id = ''

    async def send_socket_data(self, *args):
        self.results['DOM.getDocument'] = {'root': {
            'nodeId': 1, 'documentURL': self.pages.get('2', self.pages['1']).web_url}}
        await super().send_socket_data(*args)


def fixture(**options):
    guest = NativeFixture()
    browser = Browser(guest=guest, input_settle=0)
    transport = SimpleNamespace(open=AsyncMock(return_value=SimpleNamespace()), close=AsyncMock())
    session = InspectorSession(transport=transport, browser=browser, timeout=0.3,
                               new_tab_url='http://fixture.invalid/bootstrap.html', **options)
    native = TabFixture(session, guest)
    return session, native, guest


class OwnedSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_owned_workflow_attaches_before_navigation_and_preserves_original_tab(self):
        session, native, guest = fixture()
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            async with session:
                self.assertTrue(session.tab_cleanup['owned'])
                self.assertIn('#ttt-session-', native.pages['2'].web_url)
                self.assertEqual(native.events, ['open', 'attach'])
                await session.goto('https://second.invalid/product')
                self.assertEqual(native.pages['2'].web_url, 'https://second.invalid/product')
                self.assertEqual(native.pages['1'].web_url, 'https://example.org/')
        self.assertEqual(session.tab_cleanup['status'], 'closed')
        self.assertEqual(set(native.pages), {'1'})
        self.assertEqual(native.events, ['open', 'attach', 'detach', 'close'])
        self.assertFalse(native.subscribers)
        self.assertFalse(session._native_tasks)
        self.assertFalse(session.cleanup_errors)
        session.transport.close.assert_awaited_once()
        await session.close()
        self.assertEqual(native.events.count('close'), 1)

    async def test_switched_active_tab_never_receives_navigation_or_close(self):
        session, native, guest = fixture()
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            await session.__aenter__()
            guest.address_value = native.pages['1'].web_url
            with self.assertRaisesRegex(Exception, 'expected URL'):
                await session.goto('https://next.invalid/')
            with self.assertRaisesRegex(InspectorError, 'cleanup did not complete'):
                await session.close()
        self.assertEqual(set(native.pages), {'1', '2'})
        self.assertNotIn('input.paste', [method for method, _ in guest.calls])
        self.assertNotIn('close', native.events)
        self.assertEqual(session.tab_cleanup['status'], 'failed')
        session.transport.close.assert_awaited_once()

    async def test_duplicate_tab_url_prevents_ambiguous_input(self):
        session, native, guest = fixture()
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            await session.__aenter__()
            native.pages['1'].web_url = native.pages['2'].web_url
            before = list(guest.calls)
            with self.assertRaisesRegex(InspectorError, 'ambiguous'):
                await session.goto('https://next.invalid/')
            self.assertEqual(before, guest.calls)
            with self.assertRaises(InspectorError):
                await session.close()
        self.assertNotIn('close', native.events)

    async def test_uncertain_open_and_reuse_do_not_claim_or_close_an_existing_tab(self):
        for mode in ('reuse', 'uncertain', 'no_tab'):
            with self.subTest(mode=mode):
                session, native, guest = fixture()
                native.open_mode = mode
                with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
                    with self.assertRaises((InspectorError, TimeoutError)):
                        await session.__aenter__()
                self.assertEqual(native.events, ['open'])
                self.assertFalse(session.tab_cleanup['owned'])
                self.assertEqual(session.tab_cleanup['status'], 'unknown')
                self.assertTrue(session.cleanup_errors)
                self.assertEqual(set(native.pages), {'1'})
                session.transport.close.assert_awaited_once()

    async def test_close_timeout_or_lost_reply_is_retained_without_replaying_input(self):
        for mode in ('uncertain', 'no_close'):
            with self.subTest(mode=mode):
                session, native, guest = fixture()
                with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
                    await session.__aenter__()
                    native.close_mode = mode
                    with self.assertRaises(InspectorError):
                        await session.close()
                    with self.assertRaises(InspectorError):
                        await session.close()
                self.assertEqual(native.events.count('close'), 1)
                self.assertEqual(set(native.pages), {'1', '2'})
                self.assertFalse(native.subscribers)
                session.transport.close.assert_awaited_once()

    async def test_lost_open_reply_reconciles_and_closes_only_proven_new_tab(self):
        session, native, guest = fixture()
        native.open_mode = 'new_lost_reply'
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            with self.assertRaisesRegex(InspectorError, 'Open reply lost after creating tab'):
                await session.__aenter__()
        self.assertTrue(session.tab_cleanup['reconciled_after_failed_creation'])
        self.assertEqual(session.tab_cleanup['status'], 'closed')
        self.assertEqual(native.events, ['open', 'close'])
        self.assertEqual(set(native.pages), {'1'})
        session.transport.close.assert_awaited_once()

    async def test_concurrent_close_and_cancellation_join_one_native_action(self):
        session, native, guest = fixture()
        started, release = threading.Event(), threading.Event()
        original = session.browser.close_tab
        def blocked_close(**kwargs):
            started.set()
            if not release.wait(2):
                raise AssertionError('test did not release native action')
            return original(**kwargs)
        session.browser.close_tab = blocked_close
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            await session.__aenter__()
            first = asyncio.create_task(session.close())
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            second = asyncio.create_task(session.close())
            first.cancel()
            await asyncio.sleep(0)
            self.assertFalse(first.done())
            first.cancel()
            await asyncio.sleep(0)
            self.assertFalse(first.done())
            self.assertFalse(second.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await first
            await second
        self.assertEqual(native.events.count('close'), 1)
        session.transport.close.assert_awaited_once()
        self.assertEqual(session.tab_cleanup['status'], 'closed')

    async def test_body_error_is_preserved_with_cleanup_failure_note(self):
        session, native, guest = fixture()
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            with self.assertRaisesRegex(ValueError, 'body failed') as caught:
                async with session:
                    guest.address_value = 'https://other.invalid/'
                    raise ValueError('body failed')
        self.assertIn('Session cleanup failed', '\n'.join(caught.exception.__notes__))
        self.assertEqual(session.tab_cleanup['status'], 'failed')
        session.transport.close.assert_awaited_once()

    async def test_existing_tab_attachment_never_owns_or_closes_it(self):
        session, native, guest = fixture()
        session.new_tab_url = None
        session.url = guest.address_value
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            async with session:
                self.assertFalse(session.tab_cleanup['owned'])
        self.assertNotIn('close', native.events)
        self.assertEqual(set(native.pages), {'1'})

    def test_creation_configuration_rejects_ambiguous_bootstrap_before_input(self):
        for options in [{'new_tab_url': 'http://fixture.invalid/'},
                        {'new_tab_url': 'http://fixture.invalid/#route', 'browser': object()},
                        {'new_tab_url': 'http://fixture.invalid/', 'url': 'http://other.invalid/', 'browser': object()},
                        {'new_tab_url': 'javascript:alert(1)', 'browser': object()}]:
            with self.assertRaises(ValueError):
                InspectorSession(**options)


if __name__ == '__main__':
    unittest.main()
