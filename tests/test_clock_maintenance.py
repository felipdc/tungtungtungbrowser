"""Exercise real worker threads, preserved state and stop/request ordering."""
import contextlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from tungtungtungbrowser.clock import (ClockMaintenance, CLOCK_RPC_TIMEOUT_SECONDS,
                                     HEALTH_RPC_TIMEOUT_SECONDS)
from tungtungtungbrowser.guest import Guest, GuestError


class ClockMaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'clock.json'

    def worker(self, guest, **options):
        worker = ClockMaintenance(guest, self.path, 10, launch_started_unix=123,
                                  health_interval=0.01, **options)
        self.addCleanup(worker.stop)
        return worker

    def join(self, worker, timeout=2):
        worker.thread.join(timeout)
        self.assertFalse(worker.thread.is_alive(), 'clock worker did not finish')
        return json.loads(self.path.read_text())

    def test_read_only_startup_retries_then_preserves_manual_time(self):
        class NativeGuest:
            health_calls = 0
            offers = 0

            def health(self):
                self.health_calls += 1
                if self.health_calls == 1:
                    raise GuestError('not listening yet')

            def sync_clock(self):
                self.offers += 1
                return {'automatic': False, 'changed': False, 'time': 123, 'offset': 5}

        guest = NativeGuest()
        worker = self.worker(guest)
        worker.start()
        state = self.join(worker)
        self.assertEqual((guest.health_calls, guest.offers), (2, 1))
        self.assertEqual(state['status'], 'manual_time_preserved')
        self.assertFalse(state['last_response']['changed'])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_failed_offer_is_not_repeated_and_failure_survives_stop(self):
        class NativeGuest:
            offers = 0

            def health(self):
                pass

            def sync_clock(self):
                self.offers += 1
                raise GuestError('reply timed out; operation may have reached guest')

        guest = NativeGuest()
        worker = self.worker(guest)
        with contextlib.redirect_stderr(io.StringIO()):
            worker.start()
            state = self.join(worker)
        self.assertEqual(guest.offers, 1)
        self.assertEqual(state['status'], 'failed')
        self.assertIn('may have reached guest', state['error'])
        self.assertEqual(worker.stop()['status'], 'failed')

    def test_slow_clock_reply_outlives_health_timeout_without_another_offer(self):
        entered = threading.Event()
        requests, connections = [], []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, value):
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                requests.append(('health', self.path))
                self.reply({'status': 'ok'})

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(('offer', request))
                entered.set()
                # A valid reply after the former shared five-second timeout.
                time.sleep(HEALTH_RPC_TIMEOUT_SECONDS + 0.2)
                self.reply({'id': request['id'], 'type': 'response', 'result': {
                    'automatic': True, 'changed': True, 'time': 123, 'offset': 2}})

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)

        def client(kind, timeout):
            def connect():
                connections.append(kind)
                return http.client.HTTPConnection(*server.server_address, timeout=timeout)
            return Guest(connection_factory=connect)

        worker = self.worker(client('offer', CLOCK_RPC_TIMEOUT_SECONDS),
                             health_guest=client('health', HEALTH_RPC_TIMEOUT_SECONDS))
        worker.start()
        self.assertTrue(entered.wait(2))
        state = worker.stop()
        self.assertEqual(connections, ['health', 'offer'])
        self.assertEqual([kind for kind, _ in requests], ['health', 'offer'])
        self.assertEqual(requests[1][1]['method'], 'time.clock')
        self.assertEqual(set(requests[1][1]['params']), {'time'})
        self.assertEqual(state['status'], 'stopped')
        self.assertEqual(state['offers'], 1)
        self.assertTrue(state['last_response']['changed'])
        self.assertGreater(state['last_offer_seconds'], HEALTH_RPC_TIMEOUT_SECONDS)

    def test_stop_during_health_never_starts_clock_offer(self):
        entered, release = threading.Event(), threading.Event()

        class NativeGuest:
            offers = 0

            def health(self):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError('test health gate was not released')

            def sync_clock(self):
                self.offers += 1

        guest = NativeGuest()
        worker = self.worker(guest)
        worker.start()
        self.assertTrue(entered.wait(2))
        stopping = threading.Thread(target=worker.stop)
        stopping.start()
        self.assertTrue(worker.stopping.wait(2))
        self.assertTrue(stopping.is_alive())
        release.set()
        stopping.join(2)
        self.assertFalse(stopping.is_alive())
        self.assertEqual(guest.offers, 0)
        self.assertEqual(self.join(worker)['status'], 'stopped')

    def test_offers_are_spaced_after_responses_and_idle_stop_is_prompt(self):
        calls, responses = [], []
        second = threading.Event()

        class NativeGuest:
            def health(self):
                pass

            def sync_clock(self):
                calls.append(time.monotonic())
                time.sleep(0.03)
                responses.append(time.monotonic())
                if len(calls) == 2:
                    second.set()
                return {'automatic': True, 'changed': True, 'time': 123, 'offset': 2}

        worker = self.worker(NativeGuest())
        worker.start()
        self.assertTrue(second.wait(12))
        started = time.monotonic()
        state = worker.stop()
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(len(calls), 2)
        self.assertGreaterEqual(calls[1] - responses[0], 10)
        self.assertEqual(state['status'], 'stopped')
        self.assertEqual(state['offers'], 2)
        self.assertEqual(state['last_response']['offset'], 2)

    def test_startup_deadline_sends_no_clock_offer(self):
        class NativeGuest:
            offers = 0

            def health(self):
                raise GuestError('daemon unavailable')

            def sync_clock(self):
                self.offers += 1

        guest = NativeGuest()
        worker = self.worker(guest, startup_timeout=0.03)
        with contextlib.redirect_stderr(io.StringIO()):
            worker.start()
            state = self.join(worker)
        self.assertEqual(guest.offers, 0)
        self.assertEqual(state['status'], 'failed')
        self.assertIn('deadline', state['error'])

    def test_existing_report_is_preserved(self):
        self.path.write_text('previous run')
        with self.assertRaises(FileExistsError):
            self.worker(object())
        self.assertEqual(self.path.read_text(), 'previous run')


if __name__ == '__main__':
    unittest.main()
