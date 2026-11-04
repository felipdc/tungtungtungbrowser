#!/usr/bin/env python3
"""Check one HTTPS JSON route endpoint from the caller's network namespace.

Run inside the egress container. This does not inspect Safari, alter routing,
retry, follow redirects, or fall back to a direct connection.
"""
import argparse
import ipaddress
import json
import multiprocessing
from pathlib import Path
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 16384


def endpoint_origin(url):
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ValueError('Endpoint must be an HTTPS URL without whitespace or controls')
    value = urllib.parse.urlsplit(url)
    if (value.scheme != 'https' or not value.hostname or value.username is not None
            or value.password is not None or value.fragment):
        raise ValueError('Endpoint must use HTTPS without credentials or a fragment')
    port = value.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Endpoint port is out of range')
    host = value.hostname.encode('idna').decode('ascii')
    if ':' in host:
        host = '[' + host + ']'
    return 'https://' + host + (':' + str(port) if port is not None else '')


def observed_route(data, expected_ip=None, expected_country=None):
    if not isinstance(data, dict) or not isinstance(data.get('ip'), str):
        raise ValueError('Response must be a JSON object with an IP string')
    address = str(ipaddress.ip_address(data['ip']))
    country = data.get('country_code')
    if country is not None:
        if not isinstance(country, str) or len(country) != 2 or not country.isascii() or not country.isalpha():
            raise ValueError('Country must be a two-letter code')
        country = country.upper()
    if data.get('success') is False:
        raise ValueError('Endpoint reported failure')
    result = {'ip': address, 'country_code': country}
    mismatch = []
    if expected_ip is not None and address != expected_ip:
        mismatch.append('ip')
    if expected_country is not None and country != expected_country:
        mismatch.append('country_code')
    return result, mismatch


def failure_kind(error):
    if isinstance(error, urllib.error.HTTPError):
        return {'failure_kind': 'http_status', 'http_status': error.code}
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, socket.gaierror):
        return {'failure_kind': 'dns', 'errno': reason.errno}
    if isinstance(reason, ssl.SSLError):
        return {'failure_kind': 'tls'}
    if isinstance(reason, TimeoutError):
        return {'failure_kind': 'timeout'}
    if isinstance(reason, (ConnectionError, OSError)):
        return {'failure_kind': 'connection'}
    return {'failure_kind': 'request'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


def probe_worker(connection, url, timeout, expected_ip, expected_country):
    try:
        # Environment proxies could bypass the TUN route being measured.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        request = urllib.request.Request(url, headers={
            'Accept': 'application/json', 'User-Agent': 'tungtungtungbrowser-route-check/1'})
        try:
            with opener.open(request, timeout=timeout) as response:
                status = response.status
                if not 200 <= status < 300:
                    connection.send({'status': 'failed', 'failure_kind': 'http_status', 'http_status': status})
                    return
                body = response.read(MAX_BODY + 1)
        except Exception as error:
            connection.send({'status': 'failed', **failure_kind(error)})
            return
        if len(body) > MAX_BODY:
            connection.send({'status': 'failed', 'failure_kind': 'response_too_large', 'http_status': status})
            return
        try:
            observed, mismatch = observed_route(json.loads(body), expected_ip, expected_country)
        except (ValueError, UnicodeError):
            connection.send({'status': 'failed', 'failure_kind': 'invalid_route_response', 'http_status': status})
            return
        result = {'status': 'failed' if mismatch else 'passed', 'http_status': status, 'observed': observed}
        if mismatch:
            result.update(failure_kind='route_mismatch', mismatched_fields=mismatch)
        connection.send(result)
    finally:
        connection.close()


def check(url, output, *, timeout=20, expected_ip=None, expected_country=None, worker=probe_worker):
    origin = endpoint_origin(url)
    if not 1 <= timeout <= 120:
        raise ValueError('Timeout must be between 1 and 120 seconds')
    if expected_ip is not None:
        expected_ip = str(ipaddress.ip_address(expected_ip))
    if expected_country is not None:
        if len(expected_country) != 2 or not expected_country.isascii() or not expected_country.isalpha():
            raise ValueError('Expected country must be a two-letter code')
        expected_country = expected_country.upper()
    output = Path(output).absolute()
    if output.resolve().is_relative_to(ROOT):
        raise ValueError('Keep route evidence outside the source checkout')
    output.mkdir(mode=0o700, exist_ok=False)
    report = {'status': 'checking', 'started_unix': time.time(), 'endpoint_origin': origin,
              'timeout_seconds': timeout, 'probe_attempts': 1, 'redirects_followed': False,
              'environment_proxies_used': False, 'routing_modified': False,
              'native_browser_verified': False, 'future_availability_verified': False,
              'expected': {'ip': expected_ip, 'country_code': expected_country}}

    def save():
        path = output / 'report.partial'
        with path.open('w', encoding='utf-8') as file:
            json.dump(report, file, indent=2)
            file.write('\n')
        path.chmod(0o600)
        path.replace(output / 'report.json')

    save()
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=worker, args=(child, url, timeout, expected_ip, expected_country))
    started = time.monotonic()
    try:
        process.start()
        child.close()
        remaining = max(0, timeout - (time.monotonic() - started))
        if parent.poll(remaining):
            try:
                report.update(parent.recv())
            except EOFError:
                report.update(status='failed', failure_kind='probe_process_exited')
        else:
            report.update(status='failed', failure_kind='deadline')
    except BaseException:
        report.update(status='failed', failure_kind='probe_interrupted')
        raise
    finally:
        child.close()
        parent.close()
        if process.pid is not None:
            process.join(timeout=0.5)
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            report['probe_process_exit_code'] = process.exitcode
        report['elapsed_seconds'] = time.monotonic() - started
        report['finished_unix'] = time.time()
        save()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='HTTPS endpoint returning JSON ip and optional country_code')
    parser.add_argument('--output', type=Path, required=True, help='New external evidence directory')
    parser.add_argument('--timeout', type=float, default=20, help='Total probe deadline in seconds')
    parser.add_argument('--expect-ip')
    parser.add_argument('--expect-country')
    args = parser.parse_args(argv)
    try:
        report = check(args.url, args.output, timeout=args.timeout,
                       expected_ip=args.expect_ip, expected_country=args.expect_country)
    except (OSError, ValueError):
        parser.exit(2, 'Invalid route-check configuration or output path; existing evidence was preserved.\n')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
