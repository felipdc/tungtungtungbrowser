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
        self.connection_id = 'our-connection'
        self.detach_connection = ''
        self.open_mode = 'new'
        self.close_mode = 'close'
        self.connect = AsyncMock()
        self.close = AsyncMock()
        self._get_connected_applications = AsyncMock()
        self.actual_call = guest.call
        guest.call = self.native_call

    def native_call(self, method, **params):
        result = self.actual_call(method, **params)
        if method == 'input.key' and params['name'] == 'cmd+t':
            self.events.append('new_blank')
            if self.open_mode != 'blank_no_tab':
                self.pages['2'] = page(2, 'about:blank')
                self.guest.address_value = ''
            if self.open_mode == 'blank_multiple':
                self.pages['3'] = page(3, 'about:blank')
            if self.open_mode == 'blank_lost_reply':
                raise InspectorError('New-tab reply lost')
        if method == 'apps.open_url':
            self.events.append('open')
            self.guest.address_value = params['url']
            if self.open_mode in ('new', 'new_lost_reply', 'new_drop_original', 'new_drop_original_lost_reply'):
                self.pages['2'] = page(2, params['url'])
                if self.open_mode.startswith('new_drop_original'):
                    self.pages.pop('1')
                if self.open_mode.endswith('lost_reply'):
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
                self.guest.address_value = self.pages['1'].web_url if '1' in self.pages else 'about:blank'
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
            self.pages[str(page_id)].web_connection_id = self.detach_connection

    async def send_socket_data(self, *args):
        self.results['DOM.getDocument'] = {'root': {
            'nodeId': 1, 'documentURL': self.pages.get('2', self.pages['1']).web_url}}
        await super().send_socket_data(*args)


def fixture(**options):
    guest = NativeFixture()
    browser = Browser(guest=guest, input_settle=0)
    transport = SimpleNamespace(open=AsyncMock(return_value=SimpleNamespace()), close=AsyncMock())
    bootstrap = {} if options.get('new_tab') else {'new_tab_url': 'http://fixture.invalid/bootstrap.html'}
    session = InspectorSession(transport=transport, browser=browser, timeout=0.3, **bootstrap, **options)
    native = TabFixture(session, guest)
    return session, native, guest


class OwnedSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_blank_session_attaches_before_first_navigation_without_bootstrap_request(self):
        session, native, guest = fixture(new_tab=True)
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            async with session:
                self.assertTrue(session.tab_cleanup['owned'])
                self.assertEqual(session._page.web_url, 'about:blank')
                self.assertEqual(native.events, ['new_blank', 'attach'])
                await session.goto('https://destination.invalid/product')
                self.assertEqual(native.pages['2'].web_url, 'https://destination.invalid/product')
        self.assertEqual(native.events, ['new_blank', 'attach', 'detach', 'close'])
        self.assertEqual(set(native.pages), {'1'})
        self.assertEqual(native.pages['1'].web_url, 'https://example.org/')
        self.assertNotIn('apps.open_url', [method for method, _ in guest.calls])
        self.assertEqual(session.tab_cleanup['status'], 'closed')

    async def test_unused_blank_session_closes_its_verified_empty_address(self):
        session, native, guest = fixture(new_tab=True)
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            async with session:
                pass
        self.assertEqual(native.events, ['new_blank', 'attach', 'detach', 'close'])
        self.assertEqual(set(native.pages), {'1'})
        self.assertEqual(session.tab_cleanup['status'], 'closed')

    async def test_preexisting_blank_is_rejected_before_native_input(self):
        for address in ('about:blank', ''):
            session, native, guest = fixture(new_tab=True)
            native.pages['1'].web_url = address
            with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
                with self.assertRaisesRegex(InspectorError, 'blank tab already exists'):
                    await session.__aenter__()
            self.assertFalse(native.events)
            self.assertFalse(session.tab_cleanup['owned'])
            self.assertFalse(any(method.startswith('input.') for method, _ in guest.calls))

    async def test_ambiguous_missing_or_unacknowledged_blank_tab_never_replays_or_claims(self):
        for mode in ('blank_multiple', 'blank_no_tab', 'blank_lost_reply'):
            with self.subTest(mode=mode):
                session, native, guest = fixture(new_tab=True)
                native.open_mode = mode
                with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
                    with self.assertRaises((InspectorError, TimeoutError)):
                        await session.__aenter__()
                self.assertEqual(native.events, ['new_blank'])
                self.assertFalse(session.tab_cleanup['owned'])
                self.assertEqual(session.tab_cleanup['status'], 'unknown')
                self.assertEqual(guest.calls.count(('input.key', {'name': 'cmd+t'})), 1)
                self.assertNotIn(('input.key', {'name': 'cmd+w'}), guest.calls)

    def test_blank_mode_rejects_conflicting_or_missing_control_before_use(self):
        for options in ({'new_tab': 1}, {'new_tab': True},
                        {'new_tab': True, 'browser': object()},
                        {'new_tab': True, 'browser': Browser(), 'url': 'about:blank'},
                        {'new_tab': True, 'browser': Browser(), 'new_tab_url': 'https://fixture.invalid/'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                InspectorSession(**options)

    async def test_owned_workflow_attaches_before_navigation_and_preserves_original_tab(self):
        session, native, guest = fixture()
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            async with session:
                self.assertTrue(session.tab_cleanup['owned'])
                self.assertIn('#ttt-session-', native.pages['2'].web_url)
                self.assertEqual(native.events, ['open', 'attach'])
                before = len(guest.calls)
                await session.goto('https://second.invalid/product')
                self.assertEqual([method for method, _ in guest.calls[before:]].count('apps.foreground'), 1)
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

    async def test_non_http_sibling_does_not_block_owned_cleanup(self):
        session, native, guest = fixture()
        native.pages['1'].web_url = 'about:blank'
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            async with session:
                pass
        self.assertEqual(session.tab_cleanup['status'], 'closed')
        self.assertEqual(native.pages['1'].web_url, 'about:blank')

    async def test_missing_original_tab_still_fails_but_releases_proven_new_tab(self):
        for mode in ('new_drop_original', 'new_drop_original_lost_reply'):
            with self.subTest(mode=mode):
                session, native, guest = fixture()
                native.open_mode = mode
                with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
                    with self.assertRaises(InspectorError):
                        await session.__aenter__()
                self.assertTrue(session.tab_cleanup['owned'])
                self.assertEqual(session.tab_cleanup['before_page_ids'], [1])
                self.assertEqual(session.tab_cleanup['observed_page_ids'], [2])
                self.assertEqual(session.tab_cleanup['matching_page_ids'], [2])
                self.assertEqual(session.tab_cleanup['missing_before_page_ids'], [1])
                self.assertEqual(session.tab_cleanup['status'], 'closed')
                self.assertEqual(native.events, ['open', 'close'])
                self.assertFalse(native.pages)
                session.transport.close.assert_awaited_once()

    async def test_scheme_only_tab_difference_blocks_navigation_and_cleanup(self):
        session, native, guest = fixture()
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            await session.__aenter__()
            native.pages['1'].web_url = session.url.replace('http://', 'https://', 1)
            before = list(guest.calls)
            with self.assertRaisesRegex(InspectorError, 'ambiguous'):
                await session.goto('https://next.invalid/')
            self.assertEqual(before, guest.calls)
            with self.assertRaises(InspectorError):
                await session.close()
        self.assertNotIn('close', native.events)
        self.assertEqual(before, guest.calls)

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

    async def test_root_slash_only_tab_difference_is_ambiguous(self):
        session, native, guest = fixture()
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            await session.__aenter__()
            native.pages['1'].web_url = 'https://fixture.invalid'
            native.pages['2'].web_url = guest.address_value = 'https://fixture.invalid/'
            before = list(guest.calls)
            with self.assertRaisesRegex(InspectorError, 'ambiguous'):
                await session.goto('https://next.invalid/')
            with self.assertRaises(InspectorError):
                await session.close()
        self.assertEqual(before, guest.calls)

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

    async def test_late_own_detach_listing_allows_close_but_another_client_blocks_it(self):
        for connection in ('our-connection', 'another-client'):
            with self.subTest(connection=connection):
                session, native, guest = fixture()
                native.detach_connection = connection
                with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
                    await session.__aenter__()
                    if connection == 'our-connection':
                        await session.close()
                        self.assertEqual(session.tab_cleanup['status'], 'closed')
                        self.assertTrue(session.tab_cleanup['own_inspection_still_listed'])
                        self.assertEqual(native.events.count('close'), 1)
                    else:
                        with self.assertRaises(InspectorError):
                            await session.close()
                        self.assertNotIn('close', native.events)
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
