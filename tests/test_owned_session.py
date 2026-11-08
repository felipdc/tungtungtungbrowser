import asyncio
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tungtungtungbrowser import Browser, InspectorError, InspectorSession
from tungtungtungbrowser.inspector import _InitialInspectorEOF
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
        self.document_url = None
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
            'nodeId': 1, 'documentURL': (self.document_url if self.document_url is not None
                                      else self.pages.get('2', self.pages['1']).web_url)}}
        await super().send_socket_data(*args)


def fixture(**options):
    guest = NativeFixture()
    browser = Browser(guest=guest, input_settle=0, address_field_reader=guest.focused_field)
    transport = SimpleNamespace(open=AsyncMock(return_value=SimpleNamespace()), close=AsyncMock())
    bootstrap = {} if options.get('new_tab') else {'new_tab_url': 'http://fixture.invalid/bootstrap.html'}
    session = InspectorSession(transport=transport, browser=browser, timeout=0.3, **bootstrap, **options)
    native = TabFixture(session, guest)
    return session, native, guest


class OwnedSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_eof_recovers_before_opening_one_owned_tab(self):
        session, native, guest = fixture()
        failed = SimpleNamespace(connect=AsyncMock(side_effect=_InitialInspectorEOF('initial EOF')))
        session.transport._recover_initial_eof = AsyncMock(return_value=SimpleNamespace())
        with patch('tungtungtungbrowser.inspector._direct_inspector', side_effect=[failed, native]):
            async with session:
                self.assertTrue(session.tab_cleanup['owned'])
        session.transport.open.assert_awaited_once()
        session.transport._recover_initial_eof.assert_awaited_once()
        self.assertEqual(native.events, ['open', 'attach', 'detach', 'close'])
        self.assertEqual(set(native.pages), {'1'})

    async def test_exhausted_initial_eof_and_later_failures_never_replay(self):
        session, native, guest = fixture()
        native.connect.side_effect = _InitialInspectorEOF('initial EOF')
        session.transport._recover_initial_eof = AsyncMock(return_value=None)
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            with self.assertRaisesRegex(_InitialInspectorEOF, 'initial EOF'):
                await session.__aenter__()
        self.assertFalse(guest.calls)
        session.transport.close.assert_awaited_once()
        for error in (TimeoutError(), ValueError('malformed first reply')):
            session, native, guest = fixture()
            native.connect.side_effect = error
            session.transport._recover_initial_eof = AsyncMock()
            with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
                with self.assertRaises(type(error)):
                    await session.__aenter__()
            session.transport._recover_initial_eof.assert_not_awaited()
            self.assertFalse(guest.calls)
        session, native, guest = fixture()
        native._get_connected_applications.side_effect = _InitialInspectorEOF('later EOF')
        session.transport._recover_initial_eof = AsyncMock()
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            with self.assertRaisesRegex(_InitialInspectorEOF, 'later EOF'):
                await session.__aenter__()
        session.transport._recover_initial_eof.assert_not_awaited()
        self.assertFalse(guest.calls)

    async def test_initial_recovery_keeps_original_session_deadline(self):
        session, native, guest = fixture()
        session.timeout = 0.035
        async def terminated():
            await asyncio.sleep(0.02)
            raise _InitialInspectorEOF('initial EOF')
        native.connect.side_effect = terminated
        session.transport._recover_initial_eof = AsyncMock(return_value=SimpleNamespace())
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            with self.assertRaises(TimeoutError):
                await session.__aenter__()
        self.assertEqual(native.connect.await_count, 2)
        session.transport._recover_initial_eof.assert_awaited_once()
        self.assertFalse(guest.calls)
        session.transport.close.assert_awaited_once()

    async def test_lost_listing_refreshes_read_without_reopening_or_reclosing_tab(self):
        session, native, guest = fixture()
        send = native._forward_get_listing
        missed = set()
        async def listing(app_id):
            phase = 'after_close' if 'close' in native.events else 'after_open' if 'open' in native.events else 'before'
            if phase != 'before' and phase not in missed:
                missed.add(phase)
                return
            await send(app_id)
        native._forward_get_listing = listing
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native), \
                patch('tungtungtungbrowser.inspector._LISTING_REFRESH_SECONDS', 0.01):
            async with session:
                self.assertTrue(session.tab_cleanup['owned'])
        self.assertEqual(session.listing_refreshes, 2)
        self.assertEqual(native.events, ['open', 'attach', 'detach', 'close'])
        self.assertEqual(set(native.pages), {'1'})
        self.assertEqual(session.tab_cleanup['status'], 'closed')
        self.assertFalse(native.subscribers)
        self.assertFalse(session._listing_queues)

    async def test_listing_disconnect_wakes_pending_read_and_blocks_cleanup_read(self):
        session, native, guest = fixture()
        session._inspector = native
        sent = asyncio.Event()
        async def listing(app_id):
            sent.set()
        native._forward_get_listing = listing
        read = asyncio.create_task(session._fresh_pages(native.app.id_))
        await sent.wait()
        session._disconnected(ConnectionResetError('native stream reset'))
        with self.assertRaisesRegex(InspectorError, 'native stream reset'):
            await asyncio.wait_for(read, 0.1)
        with self.assertRaisesRegex(InspectorError, 'native stream reset'):
            await session._fresh_pages(native.app.id_)
        self.assertFalse(native.subscribers)
        self.assertFalse(session._listing_queues)
        self.assertEqual(session.listing_refreshes, 0)
        self.assertFalse(guest.calls)

    async def test_unanswered_listings_keep_total_deadline_and_release_subscription(self):
        session, native, guest = fixture()
        session._inspector = native
        session.timeout = 0.035
        native._forward_get_listing = AsyncMock()
        with patch('tungtungtungbrowser.inspector._LISTING_REFRESH_SECONDS', 0.01):
            with self.assertRaises(TimeoutError):
                await session._fresh_pages(native.app.id_)
        self.assertGreater(session.listing_refreshes, 0)
        self.assertLessEqual(native._forward_get_listing.await_count, 4)
        self.assertFalse(native.subscribers)
        self.assertFalse(session._listing_queues)
        self.assertFalse(guest.calls)

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

    async def test_unicode_listing_closes_the_matching_native_display_address(self):
        session, native, guest = fixture()
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            await session.__aenter__()
            native.pages['2'].web_url = 'https://fixture.invalid/caf%C3%A9'
            guest.address_value = 'https://fixture.invalid/café'
            await session.close()
        self.assertEqual(session.tab_cleanup['status'], 'closed')
        self.assertEqual(set(native.pages), {'1'})
        self.assertEqual(native.events.count('close'), 1)

    async def test_redirected_document_can_navigate_with_stale_listing(self):
        session, native, guest = fixture()
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            async with session:
                native.pages['2'].web_url = 'https://fixture.invalid/redirect'
                native.document_url = guest.address_value = 'https://www.fixture.invalid/final'
                await session.goto('https://next.invalid/')
                self.assertEqual(native.pages['2'].web_url, 'https://next.invalid/')
                native.document_url = None
        self.assertEqual(session.tab_cleanup['status'], 'closed')
        self.assertEqual(set(native.pages), {'1'})
        self.assertEqual(guest.calls.count(('input.key', {'name': 'return'})), 1)

    async def test_redirected_document_read_allows_owned_cleanup_after_detach(self):
        session, native, guest = fixture()
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            async with session:
                native.pages['2'].web_url = 'https://fixture.invalid/redirect'
                native.document_url = guest.address_value = 'https://www.fixture.invalid/final'
                self.assertEqual(await session.wait_for_url('*'), native.document_url)
        self.assertEqual(session.tab_cleanup['status'], 'closed')
        self.assertEqual(native.events[-2:], ['detach', 'close'])
        self.assertEqual(set(native.pages), {'1'})

    async def test_redirect_source_and_destination_duplicates_block_navigation_and_close(self):
        for duplicate in ('https://fixture.invalid/redirect', 'https://www.fixture.invalid/final'):
            with self.subTest(duplicate=duplicate):
                session, native, guest = fixture()
                with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
                    await session.__aenter__()
                    native.pages['2'].web_url = 'https://fixture.invalid/redirect'
                    native.document_url = guest.address_value = 'https://www.fixture.invalid/final'
                    native.pages['1'].web_url = duplicate
                    before = list(guest.calls)
                    with self.assertRaisesRegex(InspectorError, 'ambiguous'):
                        await session.goto('https://next.invalid/')
                    with self.assertRaisesRegex(InspectorError, 'cleanup did not complete'):
                        await session.close()
                self.assertEqual(before, guest.calls)
                self.assertEqual(set(native.pages), {'1', '2'})

    async def test_redirect_does_not_make_a_different_active_address_equivalent(self):
        for active in ('https://fixture.invalid/redirect', 'https://other.invalid/'):
            with self.subTest(active=active):
                session, native, guest = fixture()
                with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
                    await session.__aenter__()
                    native.pages['2'].web_url = 'https://fixture.invalid/redirect'
                    native.document_url = 'https://www.fixture.invalid/final'
                    guest.address_value = active
                    with self.assertRaisesRegex(Exception, 'expected URL'):
                        await session.goto('https://next.invalid/')
                    with self.assertRaises(InspectorError):
                        await session.close()
                self.assertNotIn('input.paste', [method for method, _ in guest.calls])
                self.assertNotIn('close', native.events)

    async def test_cleanup_discards_unconfirmed_or_invalidated_redirect_addresses(self):
        for invalidate in ('unread', 'target', 'generation', 'listing', 'navigation', 'failed_read'):
            with self.subTest(invalidate=invalidate):
                session, native, guest = fixture()
                with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
                    await session.__aenter__()
                    native.pages['2'].web_url = 'https://fixture.invalid/redirect'
                    native.document_url = guest.address_value = 'https://www.fixture.invalid/final'
                    if invalidate != 'unread':
                        await session.wait_for_url('*')
                    if invalidate == 'target':
                        session.target_id = 'replacement'
                    elif invalidate == 'generation':
                        session._event(session.target_id, {'method': 'DOM.documentUpdated'})
                    elif invalidate == 'listing':
                        native.pages['2'].web_url = 'https://fixture.invalid/changed'
                    elif invalidate == 'navigation':
                        session._event(session.target_id, {'method': 'Page.frameNavigated', 'params': {
                            'frame': {'id': 'main', 'url': 'https://fixture.invalid/changed'}}})
                    elif invalidate == 'failed_read':
                        with patch.object(session, '_command', side_effect=InspectorError('read failed')):
                            with self.assertRaisesRegex(InspectorError, 'read failed'):
                                await session.query_selector('body')
                    with self.assertRaises(InspectorError):
                        await session.close()
                self.assertNotIn('close', native.events)
                self.assertEqual(set(native.pages), {'1', '2'})

    async def test_navigation_requires_a_document_address_and_our_inspection(self):
        for unavailable in ('missing', 'empty', 'foreign'):
            with self.subTest(unavailable=unavailable):
                session, native, guest = fixture()
                with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
                    await session.__aenter__()
                    if unavailable == 'foreign':
                        native.pages['2'].web_connection_id = 'another-client'
                    root = {'nodeId': 1}
                    if unavailable != 'missing':
                        root['documentURL'] = '' if unavailable == 'empty' else native.pages['2'].web_url
                    before = list(guest.calls)
                    with patch.object(session, '_document', return_value=(session.target_id, 0, {'root': root})):
                        with self.assertRaises(InspectorError):
                            await session.goto('https://next.invalid/')
                    self.assertEqual(before, guest.calls)
                    native.pages['2'].web_connection_id = 'our-connection'
                    await session.close()

    async def test_cancelled_navigation_keeps_native_lock_until_worker_finishes(self):
        session, native, guest = fixture()
        started, release = threading.Event(), threading.Event()
        original = session.browser.goto
        def blocked_navigation(*args, **kwargs):
            started.set()
            if not release.wait(2):
                raise AssertionError('test did not release navigation')
            return original(*args, **kwargs)
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            async with session:
                session.browser.goto = blocked_navigation
                first = asyncio.create_task(session.goto('https://next.invalid/'))
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                second = asyncio.create_task(session.goto('https://last.invalid/'))
                first.cancel()
                await asyncio.sleep(0)
                first.cancel()
                await asyncio.sleep(0)
                self.assertFalse(first.done())
                self.assertFalse(second.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                await second
                self.assertEqual(native.pages['2'].web_url, 'https://last.invalid/')
        self.assertEqual(guest.calls.count(('input.key', {'name': 'return'})), 2)
        self.assertEqual(session.tab_cleanup['status'], 'closed')

    async def test_unicode_alias_tabs_reject_ambiguous_navigation_and_cleanup(self):
        session, native, guest = fixture()
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            await session.__aenter__()
            native.pages['1'].web_url = 'https://fixture.invalid/café'
            native.pages['2'].web_url = 'https://fixture.invalid/caf%c3%a9'
            guest.address_value = 'https://fixture.invalid/café'
            with self.assertRaises(InspectorError):
                await session.goto('https://next.invalid/')
            with self.assertRaisesRegex(InspectorError, 'cleanup did not complete'):
                await session.close()
        self.assertNotIn('input.paste', [method for method, _ in guest.calls])
        self.assertNotIn('close', native.events)
        self.assertEqual(set(native.pages), {'1', '2'})

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
