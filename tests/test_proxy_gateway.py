import base64
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import check_proxy_gateway as gateway


class Proxy:
    def __init__(self, response, *, delay=0, tls_garbage=False):
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(2)
        self.listener.settimeout(3)
        self.port = self.listener.getsockname()[1]
        self.response, self.delay, self.tls_garbage = response, delay, tls_garbage
        self.requests, self.after_response = [], []
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def serve(self):
        try:
            with self.listener.accept()[0] as connection:
                connection.settimeout(3)
                request = b''
                while not request.endswith(b'\r\n\r\n'):
                    chunk = connection.recv(1 if not self.tls_garbage else 4096)
                    if not chunk:
                        return
                    request += chunk
                    if self.tls_garbage:
                        break
                self.requests.append(request)
                time.sleep(self.delay)
                connection.sendall(self.response)
                self.after_response.append(connection.recv(1))
        except OSError:
            pass
        finally:
            self.listener.close()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.thread.join(timeout=4)
        self.listener.close()


def config(port=443):
    return {'scheme': 'http', 'address': '127.0.0.1', 'host': 'proxy.example.net',
            'port': port, 'username': 'private-user', 'password': 'private-password'}


def silent_worker(connection, *args):
    connection.close()


class ProxyGatewayTest(unittest.TestCase):
    def test_connect_once_and_close_without_destination_data(self):
        with tempfile.TemporaryDirectory() as temp, Proxy(b'HTTP/1.1 200 Connection established\r\n\r\n') as proxy:
            output = Path(temp) / 'result'
            result = gateway.check(config(proxy.port), 'example.org:443', output, timeout=3)
        self.assertEqual(result['status'], 'passed')
        self.assertTrue(result['connect_accepted'])
        self.assertEqual(len(proxy.requests), 1)
        self.assertTrue(proxy.requests[0].startswith(b'CONNECT example.org:443 HTTP/1.1\r\n'))
        self.assertIn(base64.b64encode(b'private-user:private-password'), proxy.requests[0])
        self.assertEqual(proxy.after_response, [b''])
        for field in ['destination_tls_or_http_sent', 'native_browser_verified',
                      'geography_verified', 'future_availability_verified', 'automatic_retry']:
            self.assertFalse(result[field])

    def test_failure_statuses_are_preserved_without_body_headers_or_credentials(self):
        for code in [302, 407, 410, 503]:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temp, \
                    Proxy(f'HTTP/1.1 {code} private-reason\r\nX-Private: private-header\r\nContent-Length: 12\r\n\r\nprivate-body'.encode()) as proxy:
                output = Path(temp) / 'result'
                result = gateway.check(config(proxy.port), 'example.org:443', output, timeout=3)
                encoded = (output / 'report.json').read_text()
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['http_status'], code)
                self.assertEqual(result['failure_kind'], 'proxy_http_status')
                self.assertFalse(result['connect_accepted'])
                self.assertNotIn('private-', encoded)
                self.assertEqual((output / 'report.json').stat().st_mode & 0o777, 0o600)
                self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertEqual(len(proxy.requests), 1)

    def test_deadline_does_not_retry_and_retains_stage(self):
        with tempfile.TemporaryDirectory() as temp, Proxy(b'HTTP/1.1 200 OK\r\n\r\n', delay=2) as proxy:
            started = time.monotonic()
            result = gateway.check(config(proxy.port), 'example.org:443', Path(temp) / 'result', timeout=1)
            self.assertLess(time.monotonic() - started, 2.5)
        self.assertEqual(result['failure_kind'], 'deadline')
        self.assertEqual(result['stage'], 'connect_response')
        self.assertFalse(result['connect_accepted'])
        self.assertEqual(len(proxy.requests), 1)

    def test_malformed_and_oversized_responses(self):
        for response, expected in [
            (b'not-http\r\n\r\n', 'invalid_response'),
            (b'HTTP/1.1 200 OK\r\nX: ' + b'a' * gateway.MAX_HEADERS, 'headers_too_large'),
        ]:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp, Proxy(response) as proxy:
                result = gateway.check(config(proxy.port), 'example.org:443', Path(temp) / 'result', timeout=3)
            self.assertEqual(result['failure_kind'], expected)
            self.assertFalse(result['connect_accepted'])

    def test_tls_failure_happens_before_connect_credentials(self):
        with tempfile.TemporaryDirectory() as temp, \
                Proxy(b'HTTP/1.1 400 Plaintext\r\n\r\n', tls_garbage=True) as proxy:
            result = gateway.check({**config(proxy.port), 'scheme': 'https'}, 'example.org:443',
                                   Path(temp) / 'result', timeout=3)
        self.assertEqual(result['failure_kind'], 'tls')
        self.assertEqual(result['stage'], 'gateway_tls')
        self.assertNotIn(b'CONNECT', proxy.requests[0])
        self.assertNotIn(base64.b64encode(b'private-user:private-password'), proxy.requests[0])

    def test_tls_uses_verified_default_context_and_configured_identity(self):
        connection, raw = mock.Mock(), mock.Mock()
        context = ssl.create_default_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        with mock.patch.object(gateway.socket, 'create_connection', return_value=raw) as connect, \
                mock.patch.object(gateway.ssl, 'create_default_context', return_value=context), \
                mock.patch.object(context, 'wrap_socket', side_effect=ssl.SSLCertVerificationError('private-detail')) as wrap:
            gateway.probe_worker(connection, {**config(), 'scheme': 'https'}, 'example.org:443', 3)
        connect.assert_called_once_with(('127.0.0.1', 443), timeout=3)
        wrap.assert_called_once_with(raw, server_hostname='proxy.example.net')
        raw.sendall.assert_not_called()
        self.assertNotIn('private-detail', str(connection.send.call_args_list))

    def test_invalid_input_rejected_before_network_or_output(self):
        for replacement in [{'scheme': 'socks5'}, {'port': True}, {'port': 65536},
                            {'address': 'proxy.example.net'}, {'username': 'a:b'},
                            {'password': 'secret\r\n'}, {'scheme': 'https', 'host': 'a/b'}]:
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / 'result'
                with self.assertRaises(ValueError):
                    gateway.check({**config(), **replacement}, 'example.org:443', output)
                self.assertFalse(output.exists())
        for target in ['https://example.org', 'u:p@example.org:443', 'example.org:443/path',
                       'example.org:443?secret', 'example.org', 'example.org:0', 'x\r\ny:443']:
            with self.subTest(target=target), self.assertRaises(ValueError):
                gateway.authority(target)
        self.assertEqual(gateway.authority('[::1]:443'), '[::1]:443')

    def test_existing_failure_and_source_checkout_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'result'
            output.mkdir()
            (output / 'report.json').write_text('original failure')
            with self.assertRaises(FileExistsError):
                gateway.check(config(), 'example.org:443', output)
            self.assertEqual((output / 'report.json').read_text(), 'original failure')
        with self.assertRaises(ValueError):
            gateway.check(config(), 'example.org:443', gateway.ROOT / 'unwanted-output')

    def test_worker_exit_without_result_is_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            result = gateway.check(config(), 'example.org:443', Path(temp) / 'result', worker=silent_worker)
        self.assertEqual(result['failure_kind'], 'probe_process_exited')
        self.assertFalse(result['connect_accepted'])


if __name__ == '__main__':
    unittest.main()
