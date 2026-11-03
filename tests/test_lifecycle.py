"""Exercise shutdown ordering with a real child process and native-RPC outcomes."""
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from scripts.run_vm import stop_guest
from tungtungtungbrowser.guest import GuestError
from tungtungtungbrowser.clock import ClockMaintenance


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        # SIGUSR1 represents the guest completing its own shutdown; SIGTERM
        # has a distinct result so premature host termination cannot pass.
        self.process = subprocess.Popen([sys.executable, '-u', '-c', """
import signal, sys
signal.signal(signal.SIGUSR1, lambda *_: sys.exit(0))
signal.signal(signal.SIGTERM, lambda *_: sys.exit(77))
print('ready', flush=True)
while True: signal.pause()
"""], stdout=subprocess.PIPE, text=True)
        self.assertEqual(self.process.stdout.readline().strip(), 'ready')

    def tearDown(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()
        self.process.stdout.close()

    def test_native_shutdown_exits_without_host_termination(self):
        process = self.process

        class NativeGuest:
            def shutdown(self):
                process.send_signal(signal.SIGUSR1)
                return {'accepted': True}

        code, report = stop_guest(process, NativeGuest(), 1)
        self.assertEqual(code, 0)
        self.assertFalse(report['forced_termination'])

    def test_disconnect_during_shutdown_still_waits_for_guest_exit(self):
        process = self.process

        class DisconnectingGuest:
            def shutdown(self):
                process.send_signal(signal.SIGUSR1)
                raise GuestError('peer closed during shutdown')

        code, report = stop_guest(process, DisconnectingGuest(), 1)
        self.assertEqual(code, 0)
        self.assertFalse(report['forced_termination'])
        self.assertIn('peer closed', report['native_request_error'])

    def test_unresponsive_guest_gets_the_grace_period_before_termination(self):
        class UnresponsiveGuest:
            def shutdown(self):
                raise GuestError('daemon unavailable')

        start = time.monotonic()
        code, report = stop_guest(self.process, UnresponsiveGuest(), 0.15)
        self.assertGreaterEqual(time.monotonic() - start, 0.15)
        self.assertEqual(code, 77)
        self.assertTrue(report['forced_termination'])

    def test_pending_clock_reply_finishes_before_native_shutdown(self):
        entered, release = threading.Event(), threading.Event()
        order = []
        process = self.process

        class NativeGuest:
            def health(self):
                pass

            def sync_clock(self):
                order.append('clock request')
                entered.set()
                if not release.wait(2):
                    raise RuntimeError('test clock gate was not released')
                order.append('clock reply')
                return {'automatic': True, 'changed': True, 'time': 123, 'offset': 2}

            def shutdown(self):
                order.append('shutdown')
                process.send_signal(signal.SIGUSR1)
                return {'accepted': True}

        with tempfile.TemporaryDirectory() as directory:
            guest = NativeGuest()
            clock = ClockMaintenance(guest, Path(directory) / 'clock.json', 60,
                                     launch_started_unix=time.time())
            clock.start()
            self.assertTrue(entered.wait(2))
            outcome = []
            stopping = threading.Thread(target=lambda: outcome.append(
                stop_guest(process, guest, 1, clock=clock)))
            stopping.start()
            self.assertTrue(clock.stopping.wait(2))
            self.assertEqual(order, ['clock request'])
            self.assertIsNone(process.poll())
            release.set()
            stopping.join(2)
            self.assertFalse(stopping.is_alive())
            self.assertEqual(order, ['clock request', 'clock reply', 'shutdown'])
            self.assertEqual(outcome[0][0], 0)
            self.assertFalse(outcome[0][1]['forced_termination'])
            self.assertFalse(clock.thread.is_alive())


if __name__ == '__main__':
    unittest.main()
