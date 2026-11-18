import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from tungtungtungbrowser.proxy_recovery import LoginProxyRecovery, ProxyRecoveryError, ProxyRecoveryExhausted


class ProxyRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / 'phone'
        self.proof = {key: True for key in LoginProxyRecovery.REQUIRED_PROOF}
        self.reset = AsyncMock(return_value=self.proof)
        self.policy = LoginProxyRecovery(self.directory, cid=41, reset_and_verify=self.reset, max_resets=2)

    def state(self):
        return json.loads((self.directory / 'state.json').read_text())

    async def test_ordinary_capture_never_resets(self):
        result = await self.policy.after_capture('capture-1', login_required=False)
        self.assertEqual(result['status'], 'not_required')
        self.reset.assert_not_awaited()
        self.assertFalse(self.directory.exists())

    async def test_login_resets_once_and_only_records_known_proof(self):
        self.reset.return_value = self.proof | {'password': 'private-secret'}
        result = await self.policy.after_capture('capture-1', login_required=True)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['reset_count'], 1)
        self.reset.assert_awaited_once_with()
        row = self.state()['resets'][0]
        self.assertEqual(row['attempt_id'], 'capture-1')
        self.assertEqual(row['proof'], self.proof)
        self.assertNotIn('private-secret', (self.directory / 'state.json').read_text())
        self.assertEqual((self.directory / 'state.json').stat().st_mode & 0o777, 0o600)

    async def test_duplicate_login_does_not_rotate_again_even_after_restart(self):
        await self.policy.after_capture('capture-1', login_required=True)
        restarted = LoginProxyRecovery(self.directory, cid=41, reset_and_verify=self.reset, max_resets=2)
        result = await restarted.after_capture('capture-1', login_required=True)
        self.assertEqual(result['status'], 'already_verified')
        self.reset.assert_awaited_once()

    async def test_budget_is_durable_and_does_not_reset_after_success(self):
        await self.policy.after_capture('capture-1', login_required=True)
        await self.policy.after_capture('capture-2', login_required=False)
        await self.policy.after_capture('capture-3', login_required=True)
        self.policy.check_ready()  # The last permitted reset can still recover.
        with self.assertRaises(ProxyRecoveryExhausted):
            await self.policy.after_capture('capture-4', login_required=True)
        self.assertEqual(self.reset.await_count, 2)
        with self.assertRaises(ProxyRecoveryExhausted):
            self.policy.check_ready()
        with self.assertRaises(ProxyRecoveryExhausted):
            LoginProxyRecovery(self.directory, cid=41, reset_and_verify=self.reset, max_resets=2)

    async def test_callback_failure_is_redacted_and_never_repeated(self):
        self.reset.side_effect = RuntimeError('private-provider-password')
        with self.assertRaises(ProxyRecoveryError) as caught:
            await self.policy.after_capture('capture-1', login_required=True)
        self.assertNotIn('private-provider-password', str(caught.exception))
        self.assertNotIn('private-provider-password', (self.directory / 'state.json').read_text())
        self.assertEqual(self.state()['resets'][0]['failure_type'], 'RuntimeError')
        with self.assertRaises(ProxyRecoveryError):
            await self.policy.after_capture('capture-2', login_required=True)
        self.reset.assert_awaited_once()
        with self.assertRaises(ProxyRecoveryError):
            LoginProxyRecovery(self.directory, cid=41, reset_and_verify=self.reset, max_resets=2)

    async def test_missing_or_nonboolean_verification_is_terminal(self):
        self.reset.return_value = self.proof | {'browser_verified': 1}
        with self.assertRaises(ProxyRecoveryError):
            await self.policy.after_capture('capture-1', login_required=True)
        self.assertEqual(self.state()['resets'][0]['status'], 'failed')
        with self.assertRaises(ProxyRecoveryError):
            self.policy.check_ready()

    async def test_persistent_marker_precedes_external_mutation(self):
        async def reset():
            self.assertEqual(self.state()['resets'][0]['status'], 'reset_requested')
            return self.proof
        self.policy.reset_and_verify = reset
        await self.policy.after_capture('capture-1', login_required=True)

    async def test_cancellation_is_terminal_and_keeps_original_cancellation(self):
        entered = asyncio.Event()
        async def reset():
            entered.set()
            await asyncio.Event().wait()
        self.policy.reset_and_verify = reset
        task = asyncio.create_task(self.policy.after_capture('capture-1', login_required=True))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.state()['resets'][0]['failure_type'], 'CancelledError')
        with self.assertRaises(ProxyRecoveryError):
            self.policy.check_ready()

    async def test_simultaneous_calls_cannot_reset_one_phone_twice(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def reset():
            entered.set()
            await release.wait()
            return self.proof
        self.policy.reset_and_verify = reset
        task = asyncio.create_task(self.policy.after_capture('capture-1', login_required=True))
        await entered.wait()
        try:
            with self.assertRaisesRegex(ProxyRecoveryError, 'Another'):
                await self.policy.after_capture('capture-2', login_required=True)
            with self.assertRaisesRegex(ProxyRecoveryError, 'Another'):
                self.policy.check_ready()
        finally:
            release.set()
            await task
        self.assertEqual(len(self.state()['resets']), 1)

    async def test_reset_deadline_is_terminal(self):
        async def reset():
            await asyncio.Event().wait()
        policy = LoginProxyRecovery(self.directory, cid=41, reset_and_verify=reset,
                                    max_resets=2, timeout_seconds=.01)
        with self.assertRaises(ProxyRecoveryError):
            await policy.after_capture('capture-1', login_required=True)
        self.assertEqual(self.state()['resets'][0]['failure_type'], 'TimeoutError')
        with self.assertRaises(ProxyRecoveryError):
            policy.check_ready()

    async def test_incomplete_record_blocks_a_new_process(self):
        self.directory.mkdir()
        (self.directory / 'state.json').write_text(json.dumps(dict(version=1, cid=41, max_resets=2,
            resets=[dict(attempt_id='capture-1', status='reset_requested')])))
        with self.assertRaises(ProxyRecoveryError):
            LoginProxyRecovery(self.directory, cid=41, reset_and_verify=self.reset, max_resets=2)
        self.reset.assert_not_awaited()

    async def test_phone_and_budget_cannot_be_changed_by_reopening(self):
        await self.policy.after_capture('capture-1', login_required=True)
        for cid, budget in ((42, 2), (41, 3)):
            with self.subTest(cid=cid, budget=budget), self.assertRaises(ProxyRecoveryError):
                LoginProxyRecovery(self.directory, cid=cid, reset_and_verify=self.reset, max_resets=budget)

    async def test_invalid_input_does_not_call_provider(self):
        for attempt, login in [('https://example.org', True), ('request', 1), ('request', 'login')]:
            with self.subTest(attempt=attempt, login=login), self.assertRaises(ValueError):
                await self.policy.after_capture(attempt, login_required=login)
        self.reset.assert_not_awaited()

    async def test_longer_fixed_job_keeps_durable_budget_and_record_size_bound(self):
        # Twelve covers four fixed warmups and eight captures, even if each
        # request returns login. The maximum must still fit the journal limit.
        for budget in (12, LoginProxyRecovery.MAX_RESETS):
            with self.subTest(budget=budget):
                directory = Path(self.temporary.name) / f'budget-{budget}'
                reset = AsyncMock(return_value=self.proof)
                for index in range(budget):
                    policy = LoginProxyRecovery(directory, cid=41, reset_and_verify=reset,
                                                max_resets=budget)
                    attempt = f'{index:02d}' + 'x' * 78
                    result = await policy.after_capture(attempt, login_required=True)
                    self.assertEqual(result['reset_count'], index + 1)
                policy.check_ready()
                duplicate = await policy.after_capture(attempt, login_required=True)
                self.assertEqual(duplicate['status'], 'already_verified')
                self.assertLess((directory/'state.json').stat().st_size, 65536)
                with self.assertRaises(ProxyRecoveryExhausted):
                    await policy.after_capture('next-request', login_required=True)
                self.assertEqual(reset.await_count, budget)
                with self.assertRaises(ProxyRecoveryExhausted):
                    LoginProxyRecovery(directory, cid=41, reset_and_verify=reset,
                                       max_resets=budget)

    async def test_invalid_budgets_fail_without_provider_or_journal(self):
        for budget in (0, -1, True, 1.5, '12', LoginProxyRecovery.MAX_RESETS + 1):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                LoginProxyRecovery(self.directory, cid=41, reset_and_verify=self.reset,
                                   max_resets=budget)
        self.reset.assert_not_awaited()
        self.assertFalse(self.directory.exists())
