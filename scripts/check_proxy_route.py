#!/usr/bin/env python3
"""Check one HTTPS JSON route endpoint from the caller's network namespace.

Run inside the egress container. This does not inspect Safari, alter routing,
retry, follow redirects, or fall back to a direct connection.
"""
import argparse
import ipaddress
import json
import multiprocessing
import os
from pathlib import Path
import socket
import ssl
import subprocess
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


def router_identity():
    """Require this process to share the initialized router's PID/net namespace."""
    if Path('/proc/1/cmdline').read_bytes().split(b'\0') != [
            b'sing-box', b'run', b'-c', b'/run/proxy-config.json', b'']:
        raise ValueError('Router has not replaced its initialization process')
    if Path(os.readlink('/proc/1/exe')).name != 'sing-box':
        raise ValueError('Unexpected router executable')
    network = os.readlink('/proc/self/ns/net')
    if network != os.readlink('/proc/1/ns/net'):
        raise ValueError('Probe and router network namespaces differ')
    stat = Path('/proc/1/stat').read_text()
    fields = stat[stat.rfind(')') + 2:].split()
    start = int(fields[19])
    if start <= 0 or fields[0] in {'Z', 'X'}:
        raise ValueError('Invalid router process identity')
    return {'pid': 1, 'start_ticks': start, 'network_namespace': network}


def router_readiness():
    """Read local initialization state without sending network traffic.

    The entrypoint execs sing-box only after installing its firewall. These
    checks reject premature docker exec calls and ordinary host execution;
    they are not a general firewall audit or a guarantee of upstream service.
    """
    try:
        before = router_identity()
        if socket.if_nametoindex('ttt0') <= 0:
            raise ValueError('Tunnel is absent')
        for family in ('iptables', 'ip6tables'):
            result = subprocess.run([family, '-w', '1', '-S'], check=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, timeout=2)
            rules = set(result.stdout.splitlines())
            if not all('-P ' + chain + ' DROP' in rules
                       for chain in ('INPUT', 'OUTPUT', 'FORWARD')):
                raise ValueError('Drop policies are absent')
        if router_identity() != before:
            raise ValueError('Router changed during initialization check')
        return {'status': 'ready', **before}
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return {'status': 'not_ready'}


def probe_worker(connection, url, timeout, expected_ip, expected_country):
    try:
        readiness = router_readiness()
        if readiness['status'] != 'ready':
            connection.send({'status': 'failed', 'failure_kind': 'router_not_ready',
                             'probe_attempts': 0, 'router_preflight': readiness})
            return
        # Environment proxies could bypass the TUN route being measured.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        request = urllib.request.Request(url, headers={
            'Accept': 'application/json', 'User-Agent': 'tungtungtungbrowser-route-check/1'})
        connection.send({'event': 'request_started', 'probe_attempts': 1,
                         'router_preflight': readiness})
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
              'timeout_seconds': timeout, 'probe_attempts': 0, 'redirects_followed': False,
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
        while True:
            remaining = max(0, timeout - (time.monotonic() - started))
            if not parent.poll(remaining):
                report.update(status='failed', failure_kind='deadline')
                break
            try:
                message = parent.recv()
            except EOFError:
                report.update(status='failed', failure_kind='probe_process_exited')
                break
            if message.get('event') == 'request_started':
                report.update({key: value for key, value in message.items() if key != 'event'})
                save()
                continue
            report.update(message)
            break
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
