import json
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import time
import unittest
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import check_proxy_route as route


def slow_probe(connection, *args):
    time.sleep(10)


def successful_probe(connection, *args):
    connection.send({'status': 'passed', 'http_status': 200,
                     'observed': {'ip': '192.0.2.1', 'country_code': 'BR'}})
    connection.close()


def crashed_probe(connection, *args):
    connection.close()


class ProxyRouteTest(unittest.TestCase):
    def test_endpoint_requires_https_and_redacts_path_query(self):
        self.assertEqual(route.endpoint_origin('https://example.org:8443/private?token=secret'),
                         'https://example.org:8443')
        for url in ['http://example.org', 'https://u:p@example.org', 'https://example.org/#secret',
                    'https://example.org/\n', 'https:///path', 'https://example.org:99999']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                route.endpoint_origin(url)

    def test_invalid_route_is_not_connectivity_success(self):
        for value in [{}, [], {'ip': 'not-an-ip'}, {'ip': '192.0.2.1', 'success': False},
                      {'ip': '192.0.2.1', 'country_code': 'Brazil'}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                route.observed_route(value)

    def test_country_and_exit_must_match_when_requested(self):
        observed, mismatch = route.observed_route({'ip': '2001:db8::1', 'country_code': 'br'},
                                                  '192.0.2.1', 'US')
        self.assertEqual(observed, {'ip': '2001:db8::1', 'country_code': 'BR'})
        self.assertEqual(mismatch, ['ip', 'country_code'])
        self.assertEqual(route.observed_route({'ip': '192.0.2.1'}, expected_country='BR')[1],
                         ['country_code'])

    def test_transport_categories_do_not_include_exception_secrets(self):
        for error, kind in [
            (urllib.error.URLError(socket.gaierror(-3, 'secret')), 'dns'),
            (urllib.error.URLError(ssl.SSLError('secret')), 'tls'),
            (TimeoutError('secret'), 'timeout'),
            (ConnectionResetError('secret'), 'connection'),
            (urllib.error.HTTPError('https://example.org/?secret', 410, 'secret', {}, None), 'http_status'),
        ]:
            result = route.failure_kind(error)
            self.assertEqual(result['failure_kind'], kind)
            self.assertNotIn('secret', json.dumps(result))

    def test_redirect_is_never_followed(self):
        self.assertIsNone(route.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.net/'))

    def test_existing_output_is_preserved_before_any_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'evidence'
            output.mkdir()
            (output / 'report.json').write_text('previous failure')
            with self.assertRaises(FileExistsError):
                route.check('https://example.org/', output, worker=successful_probe)
            self.assertEqual((output / 'report.json').read_text(), 'previous failure')

    def test_report_omits_query_and_marks_native_scope_unverified(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'evidence'
            result = route.check('https://example.org/private?token=secret', output, worker=successful_probe)
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(result['probe_attempts'], 1)
            self.assertFalse(result['native_browser_verified'])
            self.assertFalse(result['routing_modified'])
            self.assertNotIn('secret', (output / 'report.json').read_text())
            self.assertEqual((output / 'report.json').stat().st_mode & 0o777, 0o600)

    def test_total_deadline_bounds_slow_probe_and_preserves_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'evidence'
            start = time.monotonic()
            result = route.check('https://example.org/', output, timeout=1, worker=slow_probe)
            self.assertLess(time.monotonic() - start, 4)
            self.assertEqual(result['failure_kind'], 'deadline')
            self.assertEqual(result['status'], 'failed')
            self.assertIsNotNone(result['probe_process_exit_code'])
            self.assertEqual(json.loads((output / 'report.json').read_text())['status'], 'failed')

    def test_worker_exit_without_result_is_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            result = route.check('https://example.org/', Path(temp) / 'evidence', worker=crashed_probe)
            self.assertEqual(result['failure_kind'], 'probe_process_exited')
            self.assertEqual(result['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
