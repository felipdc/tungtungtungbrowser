import json
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import time
import unittest
from unittest import mock
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import check_proxy_route as route


def slow_probe(connection, *args):
    time.sleep(10)


def successful_probe(connection, *args):
    connection.send({'status': 'passed', 'http_status': 200,
                     'probe_attempts': 1,
                     'observed': {'ip': '192.0.2.1', 'country_code': 'BR'}})
    connection.close()


def crashed_probe(connection, *args):
    connection.close()


def started_slow_probe(connection, *args):
    connection.send({'event': 'request_started', 'probe_attempts': 1,
                     'router_preflight': {'status': 'ready'}})
    time.sleep(10)


class ProxyRouteTest(unittest.TestCase):
    def test_uninitialized_router_never_builds_network_client(self):
        connection = mock.Mock()
        with mock.patch.object(route, 'router_readiness', return_value={'status': 'not_ready'}), \
                mock.patch.object(route.urllib.request, 'build_opener') as opener:
            route.probe_worker(connection, 'https://example.org/', 10, None, None)
        opener.assert_not_called()
        connection.send.assert_called_once_with({
            'status': 'failed', 'failure_kind': 'router_not_ready',
            'probe_attempts': 0, 'router_preflight': {'status': 'not_ready'}})
        connection.close.assert_called_once()

    def test_router_identity_rejects_entrypoint_and_other_network_namespace(self):
        with mock.patch.object(Path, 'read_bytes', return_value=b'python3\0/app/entrypoint.py\0'):
            with self.assertRaises(ValueError):
                route.router_identity()
        command = b'sing-box\0run\0-c\0/run/proxy-config.json\0'
        with mock.patch.object(Path, 'read_bytes', return_value=command), \
                mock.patch.object(route.os, 'readlink', side_effect=[
                    '/usr/local/bin/sing-box', 'net:[1]', 'net:[2]']):
            with self.assertRaises(ValueError):
                route.router_identity()

    def test_router_identity_reads_process_start_with_parentheses_in_name(self):
        stat = '1 (name (with) spaces) S ' + '0 ' * 18 + '123 0 0\n'
        with mock.patch.object(Path, 'read_bytes', return_value=b'sing-box\0run\0-c\0/run/proxy-config.json\0'), \
                mock.patch.object(Path, 'read_text', return_value=stat), \
                mock.patch.object(route.os, 'readlink', side_effect=[
                    '/usr/local/bin/sing-box', 'net:[1]', 'net:[1]']):
            self.assertEqual(route.router_identity(), {
                'pid': 1, 'start_ticks': 123, 'network_namespace': 'net:[1]'})

    def test_router_readiness_requires_both_family_policies_and_stable_identity(self):
        identity = {'pid': 1, 'start_ticks': 123, 'network_namespace': 'net:[1]'}
        policies = '\n'.join('-P ' + chain + ' DROP' for chain in ('INPUT', 'OUTPUT', 'FORWARD'))
        cases = [
            ([policies, policies], [identity, identity], 'ready'),
            ([policies.replace('OUTPUT DROP', 'OUTPUT ACCEPT'), policies], [identity], 'not_ready'),
            ([policies, policies.replace('OUTPUT DROP', 'OUTPUT ACCEPT')], [identity], 'not_ready'),
            ([policies, policies], [identity, {**identity, 'start_ticks': 124}], 'not_ready'),
        ]
        for rules, identities, expected in cases:
            with self.subTest(expected=expected, rules=rules), \
                    mock.patch.object(route, 'router_identity', side_effect=identities), \
                    mock.patch.object(route.socket, 'if_nametoindex', return_value=2), \
                    mock.patch.object(route.subprocess, 'run', side_effect=[
                        mock.Mock(stdout=value) for value in rules]) as run:
                self.assertEqual(route.router_readiness()['status'], expected)
                for call in run.call_args_list:
                    self.assertEqual(call.args[0][1:], ['-w', '1', '-S'])
                    self.assertEqual(call.kwargs['timeout'], 2)

    def test_missing_tunnel_and_firewall_errors_fail_before_network(self):
        for error in [OSError('private details'), route.subprocess.TimeoutExpired('iptables', 2)]:
            with self.subTest(error=type(error).__name__), \
                    mock.patch.object(route, 'router_identity', return_value={'pid': 1}), \
                    mock.patch.object(route.socket, 'if_nametoindex', side_effect=error):
                self.assertEqual(route.router_readiness(), {'status': 'not_ready'})

    def test_ready_probe_reports_attempt_before_https_and_does_not_retry(self):
        connection = mock.Mock()
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b'{"ip":"192.0.2.1","country_code":"BR"}'
        opener = mock.Mock()
        def opened(*args, **kwargs):
            self.assertEqual(connection.send.call_args.args[0]['event'], 'request_started')
            return response
        opener.open.side_effect = opened
        with mock.patch.object(route, 'router_readiness', return_value={'status': 'ready'}), \
                mock.patch.object(route.urllib.request, 'build_opener', return_value=opener):
            route.probe_worker(connection, 'https://example.org/', 10, '192.0.2.1', 'BR')
        opener.open.assert_called_once()
        self.assertEqual(connection.send.call_count, 2)
        self.assertEqual(connection.send.call_args.args[0]['status'], 'passed')

    def test_started_request_deadline_preserves_attempt_and_preflight(self):
        with tempfile.TemporaryDirectory() as temp:
            result = route.check('https://example.org/', Path(temp) / 'evidence',
                                 timeout=1, worker=started_slow_probe)
            self.assertEqual(result['failure_kind'], 'deadline')
            self.assertEqual(result['probe_attempts'], 1)
            self.assertEqual(result['router_preflight']['status'], 'ready')
            self.assertNotIn('event', result)

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
