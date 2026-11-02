import http.client
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

from tungtungtungbrowser.cli import capture
from tungtungtungbrowser.guest import Guest, GuestError


class GuestContractTest(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.reply_error = False
        self.wrong_id = False
        self.locked = False
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
                if data['method'] == 'screen.screenshot':
                    result = {"mime_type": "image/jpeg", "width": 1, "height": 1,
                              "data": base64.b64encode(b'\xff\xd8test-fixture\xff\xd9').decode()}
                self.reply({"type": "response", "id": "wrong" if test.wrong_id else data['id'],
                            **({"error": {"code": "operation_failed", "message": "Safari unavailable"}}
                               if test.reply_error else {"result": result})})

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
            self.assertEqual(len(result['artifacts']), 6)
            self.assertTrue((destination / 'screenshot.jpg').is_file())
            self.assertNotIn('data', json.loads((destination / 'screenshot.json').read_text()))
            saved = json.loads((destination / 'manifest.json').read_text())
            self.assertIn('finished_at', saved)
            with self.assertRaises(FileExistsError):
                capture(self.guest, 'https://example.com', destination, 0)

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
