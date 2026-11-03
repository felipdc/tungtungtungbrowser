import contextlib
import io
import json
import signal
import unittest
from unittest.mock import Mock, patch

from tungtungtungbrowser.cli import keep_clock_synchronized, main
from tungtungtungbrowser.guest import GuestError


class ClockLoopTest(unittest.TestCase):
    def test_serial_offers_wait_after_each_response_and_preserve_results(self):
        stopping = Mock()
        stopping.is_set.side_effect = [False, False, True]
        browser = Mock()
        responses = [dict(time=1800000000, automatic=True, changed=True, offset=4),
                     dict(time=1800000060, automatic=False, changed=False, offset=3)]
        order = []
        pending = iter(responses)

        def offer():
            order.append('request')
            return next(pending)

        browser.sync_clock.side_effect = offer
        stopping.wait.side_effect = lambda seconds: order.append(('wait', seconds))
        stdout = io.StringIO()
        handlers = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        with patch('tungtungtungbrowser.cli.threading.Event', return_value=stopping), contextlib.redirect_stdout(stdout):
            keep_clock_synchronized(browser, 60)
        self.assertEqual(order, ['request', ('wait', 60), 'request', ('wait', 60)])
        self.assertEqual([json.loads(line) for line in stdout.getvalue().splitlines()], responses)
        self.assertEqual({number: signal.getsignal(number) for number in handlers}, handlers)

    def test_stop_during_request_retains_reply_without_another_offer(self):
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=number):
                browser = Mock()
                response = dict(time=1800000000, automatic=True, changed=False, offset=0)

                def offer():
                    signal.raise_signal(number)
                    return response

                browser.sync_clock.side_effect = offer
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    keep_clock_synchronized(browser, 3600)
                browser.sync_clock.assert_called_once_with()
                self.assertEqual(json.loads(stdout.getvalue()), response)

    def test_failure_exits_without_waiting_or_retrying_and_restores_handlers(self):
        stopping = Mock()
        stopping.is_set.return_value = False
        browser = Mock()
        browser.sync_clock.side_effect = GuestError('response timed out')
        handlers = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        with patch('tungtungtungbrowser.cli.threading.Event', return_value=stopping):
            with self.assertRaisesRegex(GuestError, 'response timed out'):
                keep_clock_synchronized(browser, 60)
        browser.sync_clock.assert_called_once_with()
        stopping.wait.assert_not_called()
        self.assertEqual({number: signal.getsignal(number) for number in handlers}, handlers)

    def test_invalid_interval_is_rejected_before_creating_a_guest(self):
        for interval in ['0', '-1', '9', '3601', 'nan', 'inf']:
            with self.subTest(interval=interval), patch('tungtungtungbrowser.cli.Guest') as guest:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                    main(['sync-clock', '--interval-seconds', interval])
                self.assertEqual(raised.exception.code, 2)
                guest.assert_not_called()


if __name__ == '__main__':
    unittest.main()
