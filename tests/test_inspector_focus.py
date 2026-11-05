import asyncio
import json
import unittest

from tungtungtungbrowser.inspector import DocumentChangedError, InspectorError
from test_inspector import session_fixture


def focused_session(**options):
    session = session_fixture(**options)
    session._inspector.results = {
        'DOM.getDocument': {'root': {'nodeId': 1}},
        'DOM.querySelectorAll': {'nodeIds': [9]},
        'DOM.performSearch': {'searchId': 'focus-search', 'resultCount': 1},
        'DOM.getSearchResults': {'nodeIds': [9]},
        'DOM.getAccessibilityPropertiesForNode': {'properties': {
            'nodeId': 9, 'exists': True, 'focused': True}},
    }
    return session


class InspectorFocusTests(unittest.IsolatedAsyncioTestCase):
    async def test_css_and_xpath_focus_use_native_command_and_verify_state(self):
        for strategy, selector in [('css', 'button'), ('xpath', '//button')]:
            session = focused_session()
            result = await session.focus(selector, strategy=strategy)
            self.assertEqual(result, {'method': 'native_dom_focus', 'node_id': 9, 'focused': True})
            commands = session._inspector.commands
            self.assertEqual(commands.count('DOM.focus'), 1)
            self.assertEqual(commands[-3:], ['DOM.getAccessibilityPropertiesForNode',
                                             'DOM.focus', 'DOM.getAccessibilityPropertiesForNode'])
            self.assertFalse(any(name.startswith('Runtime.') for name in commands))
            if strategy == 'xpath':
                self.assertIn('DOM.discardSearchResults', commands)

    async def test_missing_or_ambiguous_targets_never_receive_focus(self):
        for strategy in ['css', 'xpath']:
            for count in [0, 2]:
                session = focused_session()
                session._inspector.results['DOM.querySelectorAll']['nodeIds'] = list(range(9, 9 + count))
                session._inspector.results['DOM.performSearch']['resultCount'] = count
                with self.assertRaises(InspectorError):
                    await session.focus('button', strategy=strategy)
                self.assertNotIn('DOM.focus', session._inspector.commands)
                if strategy == 'xpath':
                    self.assertEqual(session._inspector.commands[-1], 'DOM.discardSearchResults')

    async def test_unavailable_disabled_hidden_and_ignored_nodes_never_receive_focus(self):
        for patch in [{'exists': False}, {'disabled': True}, {'hidden': True},
                      {'ignored': True}, {'nodeId': 10}]:
            session = focused_session()
            session._inspector.results['DOM.getAccessibilityPropertiesForNode']['properties'].update(patch)
            with self.assertRaises(InspectorError):
                await session.focus('button')
            self.assertNotIn('DOM.focus', session._inspector.commands)

    async def test_document_change_before_focus_is_rejected(self):
        session = focused_session()
        original = session._command
        async def command(method, **params):
            result = await original(method, **params)
            if method == 'DOM.getAccessibilityPropertiesForNode':
                session.document_generation += 1
            return result
        session._command = command
        with self.assertRaises(DocumentChangedError):
            await session.focus('button')
        self.assertNotIn('DOM.focus', session._inspector.commands)

    async def test_redirected_focus_and_lost_reply_are_not_replayed(self):
        for failure in ['redirected', 'lost_reply']:
            session = focused_session(timeout=0.01)
            original = session._inspector.send_socket_data
            async def send(*args):
                # The fixture answers the initial read, then either changes
                # the observed state or loses the focus acknowledgement.
                outer = args[-1]
                nested = json.loads(outer['params']['message'])
                if nested['method'] == 'DOM.focus':
                    if failure == 'lost_reply':
                        session._inspector.silent = True
                    else:
                        session._inspector.results['DOM.getAccessibilityPropertiesForNode']['properties']['focused'] = False
                await original(*args)
            session._inspector.send_socket_data = send
            with self.assertRaises((InspectorError, TimeoutError)):
                await session.focus('button')
            self.assertEqual(session._inspector.commands.count('DOM.focus'), 1)
            self.assertFalse(session._dom_lock.locked())
            self.assertFalse(session._native_lock.locked())

    async def test_focus_waits_for_existing_native_action_and_cancellation_sends_nothing(self):
        session = focused_session()
        await session._native_lock.acquire()
        task = asyncio.create_task(session.focus('button'))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(session._inspector.commands, [])
        session._native_lock.release()
        await session.focus('button')
