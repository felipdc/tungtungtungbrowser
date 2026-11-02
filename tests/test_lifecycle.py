"""Exercise shutdown ordering with a real child process and native-RPC outcomes."""
import signal
import subprocess
import sys
import time
import unittest

from scripts.run_vm import stop_guest
from tungtungtungbrowser.guest import GuestError


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


if __name__ == '__main__':
    unittest.main()
