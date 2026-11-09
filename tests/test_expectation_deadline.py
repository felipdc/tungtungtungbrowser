import asyncio
import unittest
from unittest.mock import Mock

from test_inspector import response, session_fixture


def request(session):
    session._event('page-a', {'method': 'Network.requestWillBeSent', 'params': {
        'requestId': 'one', 'request': {
            'url': 'https://fixture.invalid/data', 'method': 'GET'}}})


class ExpectationDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_request_or_response_during_action_cannot_satisfy_wait(self):
        for kind, emit in [('request', request), ('response', response)]:
            with self.subTest(kind=kind):
                session = session_fixture()
                predicate = Mock(return_value=True)
                with self.assertRaises(TimeoutError):
                    async with getattr(session, 'expect_' + kind)(predicate, timeout=0.01):
                        await asyncio.sleep(0.03)
                        emit(session)
                predicate.assert_not_called()
                self.assertFalse(session._waiters)
                await session.close()

    async def test_timely_event_is_preserved_when_the_action_finishes_later(self):
        for kind, emit in [('request', request), ('response', response)]:
            with self.subTest(kind=kind):
                session = session_fixture()
                async with getattr(session, 'expect_' + kind)('*', timeout=0.01) as waiter:
                    emit(session)
                    await asyncio.sleep(0.03)
                self.assertIsNotNone(waiter.value)
                self.assertFalse(session._waiters)
                await session.close()

    async def test_expired_wait_does_not_consume_event_for_a_new_wait(self):
        session = session_fixture()
        with self.assertRaises(TimeoutError):
            async with session.expect_response('*', timeout=0.01):
                await asyncio.sleep(0.03)
                async with session.expect_response('*', timeout=1) as fresh:
                    value = response(session)
                self.assertIs(fresh.value, value)
        self.assertIs(session.responses[('page-a', 'one')], value)
        self.assertFalse(session._waiters)
        await session.close()

    async def test_action_error_is_preserved_after_expired_wait(self):
        session = session_fixture()
        with self.assertRaisesRegex(RuntimeError, 'action failed'):
            async with session.expect_response('*', timeout=0.01):
                await asyncio.sleep(0.03)
                response(session)
                raise RuntimeError('action failed')
        self.assertFalse(session._waiters)
        await session.close()


if __name__ == '__main__':
    unittest.main()
