import http.client
import errno
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import contextlib
import io

from tungtungtungbrowser.cli import capture, capture_logs, main
from tungtungtungbrowser.guest import Guest, GuestError, GuestTransportError, SCREEN_NOTIFICATIONS


class GuestTransportTest(unittest.TestCase):
    def test_tap_timeout_does_not_repeat_or_fall_back_after_sending(self):
        connection = Mock()
        connection.getresponse.side_effect = TimeoutError('timed out')
        guest = Guest(connection_factory=lambda: connection)
        with self.assertRaises(GuestTransportError) as raised:
            guest.tap(120, 240)
        self.assertTrue(raised.exception.request_may_have_been_sent)
        connection.request.assert_called_once()
        body = connection.request.call_args.kwargs['body']
        self.assertEqual(json.loads(body)['method'], 'input.touch_sequence')
        connection.close.assert_called_once()

    def test_short_http_body_is_a_transport_failure_even_if_it_is_valid_json(self):
        for payload in (b'{"status":"ok"}', b'{"status":"o'):
            with self.subTest(payload=payload):
                seen = []

                class Handler(BaseHTTPRequestHandler):
                    def do_GET(self):
                        seen.append(self.path)
                        self.send_response(200)
                        self.send_header('Content-Length', str(len(payload) + 100))
                        self.end_headers()
                        self.wfile.write(payload)
                        self.close_connection = True

                    def log_message(self, *args):
                        pass

                server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    guest = Guest(connection_factory=lambda: http.client.HTTPConnection(
                        '127.0.0.1', server.server_port, timeout=2))
                    with self.assertRaises(GuestTransportError) as raised:
                        guest.health()
                    self.assertEqual(raised.exception.phase, 'read_body')
                    self.assertTrue(raised.exception.request_may_have_been_sent)
                    self.assertIsInstance(raised.exception.__cause__, http.client.IncompleteRead)
                    self.assertEqual(raised.exception.__cause__.expected, 100)
                    self.assertNotIn('status', str(raised.exception))
                    self.assertEqual(seen, ['/v1/health'])
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()

    def test_vsock_reset_before_connect_is_distinct_from_an_unknown_action_outcome(self):
        sock = Mock()
        cause = ConnectionResetError(errno.ECONNRESET, 'Connection reset by peer')
        sock.connect.side_effect = cause
        with patch('tungtungtungbrowser.guest.socket.socket', return_value=sock):
            with self.assertRaises(GuestTransportError) as raised:
                Guest().open('https://example.com/')
        error = raised.exception
        self.assertIsInstance(error, GuestError)
        self.assertEqual(error.phase, 'connect')
        self.assertEqual(error.errno, errno.ECONNRESET)
        self.assertFalse(error.request_may_have_been_sent)
        self.assertIs(error.__cause__, cause)
        sock.connect.assert_called_once_with((3, 1339))
        sock.sendall.assert_not_called()
        sock.close.assert_called_once()

    def test_errors_after_connect_preserve_uncertainty_without_retrying(self):
        for phase, failure in [('send_request', 'request'), ('receive_headers', 'getresponse'),
                               ('read_body', 'read')]:
            with self.subTest(phase=phase):
                connection = Mock()
                operation = (connection.getresponse.return_value.read if failure == 'read'
                             else getattr(connection, failure))
                operation.side_effect = TimeoutError('timed out')
                guest = Guest(connection_factory=lambda: connection)
                with self.assertRaises(GuestTransportError) as raised:
                    guest.open('https://example.com/')
                self.assertEqual(raised.exception.phase, phase)
                self.assertTrue(raised.exception.request_may_have_been_sent)
                self.assertNotIn('https://example.com/', str(raised.exception))
                connection.request.assert_called_once()
                operation.assert_called_once()
                connection.close.assert_called_once()

    def test_disconnect_after_server_received_navigation_is_reported_as_uncertain(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                # The action was received, but no response headers reach the client.
                self.close_connection = True

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            guest = Guest(connection_factory=lambda: http.client.HTTPConnection(
                '127.0.0.1', server.server_port, timeout=2))
            with self.assertRaises(GuestTransportError) as raised:
                guest.open('https://example.com/')
            self.assertEqual(raised.exception.phase, 'receive_headers')
            self.assertTrue(raised.exception.request_may_have_been_sent)
            self.assertEqual([r['method'] for r in seen], ['apps.open_url'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class GuestContractTest(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.reply_error = False
        self.method_errors = set()
        self.wrong_id = False
        self.locked = False
        self.notification_states = dict(zip(SCREEN_NOTIFICATIONS.values(), [34, 1, 0]))
        self.notification_override = None
        self.log_result = {"source": "unified_log", "entries": [{"message": "private native event"}],
                           "count": 1, "truncated": True, "seconds": 2}
        self.clock_result = {'time': 1800000000.0, 'automatic': True,
                             'changed': True, 'offset': 120.0}
        self.tap_result = {'events': 2, 'normalized': False,
                           'finger_down': False, 'duration_ms': 51}
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.reply({"status": "ok"})

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                test.requests.append(data)
                result = {"accepted": True}
                if data['method'] == 'screen.unlock':
                    result = {"locked": test.locked, "screen_off": False}
                if data['method'] == 'input.button':
                    result = {'button': data['params']['name']}
                if data['method'] == 'apps.launch':
                    result = {'pid': 301, 'frontmost_verified': True}
                if data['method'] == 'logs.syslog':
                    result = test.log_result
                if data['method'] == 'time.clock':
                    result = test.clock_result
                if data['method'] == 'input.touch_sequence':
                    result = test.tap_result
                if data['method'] == 'notify.state':
                    name = data['params']['name']
                    result = (test.notification_override if test.notification_override is not None
                              else {'name': name, 'state': test.notification_states[name]})
                if data['method'] == 'screen.screenshot':
                    result = {"mime_type": "image/jpeg", "width": 1, "height": 1,
                              "data": base64.b64encode(b'\xff\xd8test-fixture\xff\xd9').decode()}
                self.reply({"type": "response", "id": "wrong" if test.wrong_id else data['id'],
                            **({"error": {"code": "operation_failed", "message": "Safari unavailable"}}
                               if test.reply_error or data['method'] in test.method_errors else {"result": result})})

            def reply(self, data):
                encoded = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.guest = Guest(connection_factory=lambda: http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=2))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_navigation_uses_native_safari_rpc(self):
        url = "https://example.com/catalog/123"
        self.assertEqual(self.guest.open(url), {"accepted": True})
        request = self.requests[0]
        self.assertEqual(request['method'], 'apps.open_url')
        self.assertEqual(request['params'], {"url": url, "bundle_id": "com.apple.mobilesafari"})

    def test_shutdown_requests_native_ios_shutdown(self):
        self.guest.shutdown()
        self.assertEqual(self.requests[0]['method'], 'system.shutdown')
        self.assertEqual(self.requests[0]['params'], {'force': True})

    def test_tap_sends_one_balanced_sequence_in_screen_points(self):
        result = self.guest.tap(120, 240)
        self.assertEqual(result, {'x': 120, 'y': 240, 'method': 'native_touch_sequence',
                                  'sequence': self.tap_result})
        self.assertEqual([(r['method'], r['params']) for r in self.requests], [
            ('input.touch_sequence', {'normalized': False, 'events': [
                {'phase': 'down', 'x': 120, 'y': 240, 'delay_ms': 50},
                {'phase': 'up', 'x': 120, 'y': 240, 'delay_ms': 0},
            ]})])

    def test_invalid_tap_coordinates_send_no_input(self):
        for value in [True, None, '120', -1, float('nan'), float('inf')]:
            for point in [(value, 10), (10, value)]:
                with self.subTest(point=point), self.assertRaises(ValueError):
                    self.guest.tap(*point)
        self.assertEqual(self.requests, [])

    def test_bad_sequence_reply_never_repeats_or_falls_back_to_tap(self):
        valid = self.tap_result
        for result in [None, {}, [], dict(valid, events=1), dict(valid, events=True),
                       dict(valid, normalized=True), dict(valid, finger_down=True),
                       dict(valid, duration_ms=-1), dict(valid, duration_ms=True)]:
            with self.subTest(result=result):
                self.tap_result = result
                self.requests.clear()
                with self.assertRaisesRegex(GuestError, 'Invalid native tap sequence'):
                    self.guest.tap(120, 240)
                self.assertEqual([r['method'] for r in self.requests], ['input.touch_sequence'])

    def test_sequence_operation_error_has_no_input_fallback(self):
        self.method_errors.add('input.touch_sequence')
        with self.assertRaises(GuestError):
            self.guest.tap(120, 240)
        self.assertEqual([r['method'] for r in self.requests], ['input.touch_sequence'])

    def test_clock_sync_offers_host_time_without_changing_manual_mode(self):
        self.clock_result.update(automatic=False, changed=False)
        stdout = io.StringIO()
        with patch('tungtungtungbrowser.guest.time.time', return_value=1800000000.0), \
                patch('tungtungtungbrowser.cli.Guest', return_value=self.guest), \
                contextlib.redirect_stdout(stdout):
            main(['sync-clock'])
        self.assertEqual(json.loads(stdout.getvalue()), self.clock_result)
        self.assertEqual([(r['method'], r['params']) for r in self.requests],
                         [('time.clock', {'time': 1800000000.0})])

    def test_clock_state_cli_reads_without_offering_time_or_setting_automatic_mode(self):
        for automatic in (True, False, None):
            with self.subTest(automatic=automatic):
                self.clock_result = {'time': 1800000000.25, 'automatic': automatic}
                self.requests.clear()
                stdout = io.StringIO()
                with patch('tungtungtungbrowser.cli.Guest', return_value=self.guest), \
                        contextlib.redirect_stdout(stdout):
                    main(['clock-state'])
                self.assertEqual(json.loads(stdout.getvalue()), self.clock_result)
                self.assertEqual([(r['method'], r['params']) for r in self.requests],
                                 [('time.clock', {})])

    def test_clock_state_bad_reply_or_failure_never_corrects_or_repeats(self):
        for result in [None, {}, [], {'time': 1800000000},
                       {'time': True, 'automatic': True},
                       {'time': float('nan'), 'automatic': True},
                       {'time': float('inf'), 'automatic': True},
                       {'time': '1800000000', 'automatic': True},
                       {'time': 1800000000, 'automatic': 1}]:
            with self.subTest(result=result):
                self.clock_result = result
                self.requests.clear()
                with self.assertRaisesRegex(GuestError, 'Invalid native clock state'):
                    self.guest.clock_state()
                self.assertEqual([(r['method'], r['params']) for r in self.requests],
                                 [('time.clock', {})])
        with patch.object(self.guest, 'call', side_effect=GuestError('timed out')) as call:
            with self.assertRaisesRegex(GuestError, 'timed out'):
                self.guest.clock_state()
            call.assert_called_once_with('time.clock')

    def test_clock_sync_failure_or_malformed_reply_is_not_retried(self):
        for result in [{}, [], dict(self.clock_result, time=True),
                       dict(self.clock_result, offset='120'),
                       dict(self.clock_result, automatic='true'),
                       dict(self.clock_result, changed=1)]:
            with self.subTest(result=result):
                self.clock_result = result
                self.requests.clear()
                with self.assertRaisesRegex(GuestError, 'Invalid native clock'):
                    self.guest.sync_clock()
                self.assertEqual(len(self.requests), 1)
        with patch.object(self.guest, 'call', side_effect=GuestError('timed out')) as call:
            with self.assertRaisesRegex(GuestError, 'timed out'):
                self.guest.sync_clock()
            call.assert_called_once()

    def test_home_cli_uses_one_native_button_and_does_not_claim_rendered_home(self):
        stdout = io.StringIO()
        with patch('tungtungtungbrowser.cli.Guest', return_value=self.guest), contextlib.redirect_stdout(stdout):
            main(['home'])
        self.assertEqual(json.loads(stdout.getvalue()), {'button': 'home', 'home_screen_verified': False})
        self.assertEqual([(r['method'], r['params']) for r in self.requests],
                         [('input.button', {'name': 'home'})])

    def test_launch_cli_unlocks_and_launches_safari_without_opening_a_url(self):
        stdout = io.StringIO()
        with patch('tungtungtungbrowser.cli.Guest', return_value=self.guest), contextlib.redirect_stdout(stdout):
            main(['launch'])
        result = json.loads(stdout.getvalue())
        self.assertTrue(result['frontmost_verified'])
        self.assertEqual(result['pid'], 301)
        self.assertFalse(result['navigation_verified'])
        self.assertFalse(result['visual_readiness_verified'])
        self.assertEqual([(r['method'], r['params']) for r in self.requests], [
            ('screen.unlock', {'timeout': 60}),
            ('apps.launch', {'bundle_id': 'com.apple.mobilesafari'})])

    def test_screen_state_cli_reads_notifications_without_input_or_readiness_claim(self):
        stdout = io.StringIO()
        with patch('tungtungtungbrowser.cli.Guest', return_value=self.guest), contextlib.redirect_stdout(stdout):
            main(['screen-state'])
        result = json.loads(stdout.getvalue())
        self.assertEqual(result, {
            'source': 'darwin_notify', 'springboard_started': True,
            'locked': True, 'screen_off': False,
            'states': {'startup': 34, 'locked': 1, 'screen_off': 0},
            'visual_readiness_verified': False})
        self.assertEqual([(r['method'], r['params']) for r in self.requests], [
            ('notify.state', {'name': name}) for name in SCREEN_NOTIFICATIONS.values()])
        self.notification_states = dict.fromkeys(SCREEN_NOTIFICATIONS.values(), 0)
        result = self.guest.screen_state()
        self.assertFalse(result['springboard_started'])
        self.assertIsNone(result['locked'])
        self.assertIsNone(result['screen_off'])

    def test_screen_state_rejects_malformed_notifications_and_never_retries(self):
        startup = SCREEN_NOTIFICATIONS['startup']
        for result in [{}, [], {'name': 'wrong', 'state': 1},
                       {'name': startup, 'state': True}, {'name': startup, 'state': -1},
                       {'name': startup, 'state': 2**64}]:
            with self.subTest(result=result):
                self.notification_override = result
                self.requests.clear()
                with self.assertRaisesRegex(GuestError, 'Invalid native screen-state'):
                    self.guest.screen_state()
                self.assertEqual(len(self.requests), 1)
        self.notification_override = None
        self.notification_states[SCREEN_NOTIFICATIONS['locked']] = 2
        self.requests.clear()
        with self.assertRaisesRegex(GuestError, 'Invalid native screen-state'):
            self.guest.screen_state()
        self.assertEqual(len(self.requests), 2)
        self.method_errors = {'notify.state'}
        self.requests.clear()
        with self.assertRaisesRegex(GuestError, 'Native operation failed'):
            self.guest.screen_state()
        self.assertEqual(len(self.requests), 1)
        with patch.object(self.guest, 'request', side_effect=GuestError('transport timed out')) as request:
            with self.assertRaisesRegex(GuestError, 'transport timed out'):
                self.guest.screen_state()
            self.assertEqual(request.call_count, 1)

    def test_screen_notifications_are_scoped_before_transport(self):
        for params in [{}, {'name': 'unrelated.notification'},
                       {'name': SCREEN_NOTIFICATIONS['locked'], 'state': 0}]:
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.guest.call('notify.state', **params)
        self.assertFalse(self.requests)

    def test_logs_capture_native_events_without_printing_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'logs.json'
            stdout = io.StringIO()
            with patch('tungtungtungbrowser.cli.Guest', return_value=self.guest), contextlib.redirect_stdout(stdout):
                main(['logs', '--output', str(path), '--process', 'MobileSafari',
                      '--level', 'all', '--seconds', '2', '--max-lines', '1'])
            saved = json.loads(path.read_text())
            self.assertEqual(saved['status'], 'complete')
            self.assertEqual(saved['result'], self.log_result)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(stdout.getvalue())['count'], 1)
            self.assertNotIn('private native event', stdout.getvalue())
            self.assertEqual(self.requests[0]['method'], 'logs.syslog')
            self.assertEqual(self.requests[0]['params'], {
                'seconds': 2, 'process': 'MobileSafari', 'level': 'all', 'max_lines': 1})

    def test_log_limits_and_existing_output_prevent_guest_requests(self):
        invalid = [{'seconds': float('nan')}, {'seconds': 61}, {'seconds': 0},
                   {'max_lines': 0}, {'max_lines': 5001}, {'max_lines': 1.5},
                   {'max_lines': True}, {'process': ''}, {'process': 'a\nb'}, {'level': 'invalid'}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'logs.json'
            for options in invalid:
                with self.subTest(options=options), self.assertRaises(ValueError):
                    capture_logs(self.guest, path, **options)
                self.assertFalse(path.exists())
            path.write_text('original evidence')
            with self.assertRaises(FileExistsError):
                capture_logs(self.guest, path)
            self.assertEqual(path.read_text(), 'original evidence')
        self.assertFalse(self.requests)

    def test_log_failure_keeps_report_without_retrying(self):
        self.method_errors = {'logs.syslog'}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'logs.json'
            with self.assertRaises(GuestError):
                capture_logs(self.guest, path)
            saved = json.loads(path.read_text())
            self.assertEqual(saved['status'], 'failed')
            self.assertIn('Safari unavailable', saved['error'])
            self.assertIn('finished_at', saved)
            self.assertNotIn('result', saved)
        self.assertEqual(len(self.requests), 1)

    def test_invalid_log_result_is_not_reported_as_success(self):
        for result in [{}, {'error': 'stream unavailable'},
                       dict(self.log_result, count=2), dict(self.log_result, truncated='false'),
                       dict(self.log_result, entries=[None])]:
            with self.subTest(result=result):
                self.log_result = result
                with self.assertRaisesRegex(GuestError, 'Invalid native log'):
                    self.guest.logs()

    def test_scripting_and_nonweb_urls_rejected_before_transport(self):
        for url in ["javascript:alert(1)", "data:text/html,hi", "file:///tmp/x",
                    "https://user:pass@example.com", "https://example.com\nX: y", "https://x:99999"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.guest.open(url)
        with self.assertRaises(ValueError):
            self.guest.call("script.eval", source="anything")
        with self.assertRaises(ValueError):
            self.guest.call("apps.open_url", url="javascript:alert(1)", bundle_id="com.apple.mobilesafari")
        self.assertFalse(self.requests)

    def test_guest_operation_failure_is_not_success(self):
        self.reply_error = True
        with self.assertRaisesRegex(GuestError, 'Safari unavailable'):
            self.guest.open("https://example.com")

    def test_mismatched_reply_rejected(self):
        self.wrong_id = True
        with self.assertRaisesRegex(GuestError, 'Mismatched'):
            self.guest.call("device.info")

    def test_capture_keeps_success_unverified_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'run'
            result = capture(self.guest, 'https://example.com/catalog/123', destination, 0)
            self.assertFalse(result['navigation_verified'])
            self.assertFalse(result['page_javascript_injected'])
            self.assertEqual(len(result['artifacts']), 4)
            self.assertFalse(any(r['method'].startswith('ui.') for r in self.requests))
            self.assertTrue((destination / 'screenshot.jpg').is_file())
            self.assertNotIn('data', json.loads((destination / 'screenshot.json').read_text()))
            saved = json.loads((destination / 'manifest.json').read_text())
            self.assertIn('finished_at', saved)
            with self.assertRaises(FileExistsError):
                capture(self.guest, 'https://example.com', destination, 0)

    def test_optional_diagnostic_failure_preserves_required_screenshot(self):
        self.method_errors = {'ui.tree', 'ui.ocr'}
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'run'
            result = capture(self.guest, 'https://example.com', destination, 0,
                             accessibility=True, ocr=True)
            self.assertEqual(set(result['capture_errors']), {'ui.tree', 'ui.ocr'})
            self.assertTrue((destination / 'screenshot.jpg').is_file())
            self.assertFalse(result['navigation_verified'])
            self.assertTrue(result['optional_diagnostics']['accessibility'])

    def test_missing_required_screenshot_fails_with_manifest(self):
        self.method_errors = {'screen.screenshot'}
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'run'
            with self.assertRaises(GuestError):
                capture(self.guest, 'https://example.com', destination, 0)
            saved = json.loads((destination / 'manifest.json').read_text())
            self.assertIn('error', saved)
            self.assertFalse((destination / 'screenshot.jpg').exists())
            self.assertFalse(saved['navigation_verified'])

    def test_failed_capture_writes_failure_manifest(self):
        self.reply_error = True
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'failure'
            with self.assertRaises(GuestError):
                capture(self.guest, 'https://example.com', destination, 0)
            saved = json.loads((destination / 'manifest.json').read_text())
            self.assertIn('Safari unavailable', saved['error'])
            self.assertFalse(saved['navigation_verified'])

    def test_capture_does_not_navigate_while_locked(self):
        self.locked = True
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'locked'
            with self.assertRaisesRegex(GuestError, 'Screen remains locked'):
                capture(self.guest, 'https://example.com', destination, 0)
            self.assertEqual([r['method'] for r in self.requests], ['screen.unlock'])
            saved = json.loads((destination / 'manifest.json').read_text())
            self.assertFalse(saved['navigation_verified'])


if __name__ == '__main__':
    unittest.main()
