import asyncio
import unittest
from unittest.mock import patch
from tungtungtungbrowser import InspectorSession, InspectorError
from test_owned_session import fixture


class RetainedSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_retained_tab_detaches_and_releases_transport_without_close_input(self):
        session, native, guest = fixture(retain_tab=True)
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            async with session:
                self.assertTrue(session.tab_cleanup['owned'])
        self.assertEqual(native.events, ['open', 'attach', 'detach'])
        self.assertEqual(set(native.pages), {'1', '2'})
        self.assertEqual(native.pages['2'].web_connection_id, '')
        self.assertEqual(session.tab_cleanup['status'], 'retained')
        self.assertIs(session.tab_cleanup['retention_verified'], False)
        native.close.assert_awaited_once()
        session.transport.close.assert_awaited_once()
        self.assertFalse(native.subscribers)
        self.assertFalse(session._listing_queues)
        await session.close()
        session.transport.close.assert_awaited_once()
        self.assertNotIn('close', native.events)

    async def test_body_failure_still_detaches_retained_tab(self):
        session, native, guest = fixture(retain_tab=True)
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            with self.assertRaisesRegex(RuntimeError, 'workload failed'):
                async with session:
                    raise RuntimeError('workload failed')
        self.assertEqual(native.events, ['open', 'attach', 'detach'])
        self.assertEqual(session.tab_cleanup['status'], 'retained')
        session.transport.close.assert_awaited_once()

    async def test_lost_open_reply_can_reconcile_and_retain_without_input_replay(self):
        session, native, guest = fixture(retain_tab=True)
        native.open_mode = 'new_lost_reply'
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            with self.assertRaisesRegex(InspectorError, 'Open reply lost'):
                await session.__aenter__()
        self.assertEqual(native.events, ['open'])
        self.assertTrue(session.tab_cleanup['owned'])
        self.assertEqual(session.tab_cleanup['status'], 'retained')
        self.assertEqual(set(native.pages), {'1', '2'})
        session.transport.close.assert_awaited_once()

    async def test_unknown_ownership_remains_an_error_with_retention_enabled(self):
        session, native, guest = fixture(retain_tab=True)
        native.open_mode = 'uncertain'
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            with self.assertRaisesRegex(InspectorError, 'Open reply lost'):
                await session.__aenter__()
        self.assertFalse(session.tab_cleanup['owned'])
        self.assertEqual(session.tab_cleanup['status'], 'unknown')
        self.assertTrue(session.cleanup_errors)
        self.assertNotIn('close', native.events)

    async def test_blank_owned_session_can_be_retained(self):
        session, native, guest = fixture(new_tab=True, retain_tab=True)
        with patch('tungtungtungbrowser.inspector._direct_inspector', return_value=native):
            async with session:
                pass
        self.assertEqual(native.events, ['new_blank', 'attach', 'detach'])
        self.assertEqual(session.tab_cleanup['status'], 'retained')
        self.assertEqual(native.pages['2'].web_url, 'about:blank')

    def test_retention_requires_an_explicit_boolean_and_new_tab(self):
        for value in [1, 0, None, 'yes']:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'retain_tab'):
                InspectorSession(retain_tab=value)
        with self.assertRaisesRegex(ValueError, 'newly owned'):
            InspectorSession(retain_tab=True)


if __name__ == '__main__':
    unittest.main()
