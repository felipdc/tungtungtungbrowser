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


class ReadyClockGuest:
    def clock_state(self):
        return {'automatic': True, 'time': 123}


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
        class NativeGuest(ReadyClockGuest):
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
        class NativeGuest(ReadyClockGuest):
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
                if not request['params']:
                    requests.append(('clock-read', request))
                    self.reply({'id': request['id'], 'type': 'response', 'result': {'automatic': True, 'time': 123}})
                    return
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
        self.assertEqual(connections, ['health', 'health', 'offer'])
        self.assertEqual([kind for kind, _ in requests], ['health', 'clock-read', 'offer'])
        self.assertEqual(requests[2][1]['method'], 'time.clock')
        self.assertEqual(set(requests[2][1]['params']), {'time'})
        self.assertEqual(state['status'], 'stopped')
        self.assertEqual(state['offers'], 1)
        self.assertTrue(state['last_response']['changed'])
        self.assertGreater(state['last_offer_seconds'], HEALTH_RPC_TIMEOUT_SECONDS)

    def test_stop_during_health_never_starts_clock_offer(self):
        entered, release = threading.Event(), threading.Event()

        class NativeGuest(ReadyClockGuest):
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

    def test_offers_wait_after_responses_and_idle_stop_interrupts_wait(self):
        calls, responses, waits = [], [], []
        first_interval, advance_interval, idle = (threading.Event() for _ in range(3))

        class StopSignal:
            """Advance the first interval explicitly; interrupt the second."""
            def __init__(self):
                self.event = threading.Event()

            def is_set(self):
                return self.event.is_set()

            def set(self):
                self.event.set()
                advance_interval.set()

            def wait(self, timeout):
                waits.append((timeout, len(responses)))
                if len(waits) == 1:
                    first_interval.set()
                    if not advance_interval.wait(10):
                        raise RuntimeError('test interval was not advanced')
                    return self.event.is_set()
                idle.set()
                return self.event.wait(timeout)

        class NativeGuest(ReadyClockGuest):
            def health(self):
                pass

            def sync_clock(self):
                calls.append('offer')
                responses.append('response')
                return {'automatic': True, 'changed': True, 'time': 123, 'offset': 2}

        worker = self.worker(NativeGuest())
        worker.stopping = StopSignal()
        worker.start()
        self.assertTrue(first_interval.wait(5))
        self.assertEqual(len(calls), 1)
        self.assertEqual(waits, [(10, 1)])
        advance_interval.set()
        self.assertTrue(idle.wait(5))
        stopping = threading.Thread(target=worker.stop)
        stopping.start()
        stopping.join(5)
        self.assertFalse(stopping.is_alive(), 'stop did not interrupt the idle interval')
        state = self.join(worker)
        self.assertEqual(len(calls), 2)
        self.assertEqual(waits, [(10, 1), (10, 2)])
        self.assertEqual(state['status'], 'stopped')
        self.assertEqual(state['offers'], 2)
        self.assertEqual(state['last_response']['offset'], 2)

    def test_startup_deadline_sends_no_clock_offer(self):
        class NativeGuest(ReadyClockGuest):
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

    def test_healthy_daemon_waits_for_read_only_native_clock(self):
        class NativeGuest(ReadyClockGuest):
            reads = 0
            offers = 0
            def health(self):
                pass
            def clock_state(self):
                self.reads += 1
                if self.reads == 1:
                    raise GuestError('timed did not answer within 3 seconds')
                return {'automatic': False, 'time': 123}
            def sync_clock(self):
                self.offers += 1
                return {'automatic': False, 'changed': False, 'time': 123, 'offset': 0}
        guest = NativeGuest()
        worker = self.worker(guest)
        worker.start()
        state = self.join(worker)
        self.assertEqual((guest.reads, guest.offers), (2, 1))
        self.assertEqual(state['status'], 'manual_time_preserved')
        self.assertEqual(state['clock_checks'], 2)
        self.assertIn('timed', state['last_clock_read_error'])
        self.assertFalse(state['initial_clock_state']['automatic'])

    def test_native_clock_deadline_never_starts_offer(self):
        class NativeGuest(ReadyClockGuest):
            offers = 0
            def health(self):
                pass
            def clock_state(self):
                raise GuestError('timed not ready')
            def sync_clock(self):
                self.offers += 1
        guest = NativeGuest()
        worker = self.worker(guest, startup_timeout=0.03)
        with contextlib.redirect_stderr(io.StringIO()):
            worker.start()
            state = self.join(worker)
        self.assertEqual(guest.offers, 0)
        self.assertGreater(state['clock_checks'], 0)
        self.assertIn('native-clock readiness deadline', state['error'])

    def test_stop_joins_clock_read_before_any_offer(self):
        entered, release = threading.Event(), threading.Event()
        class NativeGuest(ReadyClockGuest):
            offers = 0
            def health(self):
                pass
            def clock_state(self):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError('clock read gate not released')
                return {'automatic': True, 'time': 123}
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

    def test_late_clock_read_cannot_authorize_first_offer(self):
        class NativeGuest(ReadyClockGuest):
            offers = 0
            def health(self):
                pass
            def clock_state(self):
                time.sleep(0.06)
                return {'automatic': True, 'time': 123}
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
