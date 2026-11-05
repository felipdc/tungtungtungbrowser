#!/usr/bin/env python3
"""Attempt one HTTP CONNECT through a pinned HTTP(S) proxy before starting iOS.

Read private proxy JSON from stdin. No destination TLS/application data is sent.
This checks gateway acceptance, not the isolated router, geography or Safari.
"""
import argparse
import base64
import ipaddress
import json
import multiprocessing
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import time
import urllib.parse

from reload_proxy_session import read_json

ROOT = Path(__file__).resolve().parents[1]
MAX_HEADERS = 16384


def hostname(value):
    if not isinstance(value, str) or not value or len(value) > 253:
        raise ValueError('Invalid hostname')
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        encoded = value.encode('idna').decode('ascii')
        if not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', part)
                   for part in encoded.rstrip('.').split('.')):
            raise ValueError('Invalid hostname')
        return encoded


def authority(value):
    if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ValueError('Expected host:port')
    parsed = urllib.parse.urlsplit('//' + value)
    if (parsed.username is not None or parsed.password is not None or parsed.path
            or parsed.query or parsed.fragment or parsed.port is None
            or not 1 <= parsed.port <= 65535):
        raise ValueError('Expected host:port')
    host = hostname(parsed.hostname)
    return ('[' + host + ']' if ':' in host else host) + ':' + str(parsed.port)


def configuration(secret):
    if not isinstance(secret, dict) or secret.get('scheme') not in {'http', 'https'}:
        raise ValueError('Gateway check supports HTTP and HTTPS CONNECT')
    port = secret.get('port')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Invalid gateway port')
    result = {'scheme': secret['scheme'], 'address': str(ipaddress.IPv4Address(secret.get('address'))),
              'port': port}
    if result['scheme'] == 'https':
        result['host'] = hostname(secret.get('host'))
    for key in ('username', 'password'):
        value = secret.get(key)
        if (not isinstance(value, str) or not 1 <= len(value.encode('utf-8')) <= 4096
                or any(ord(c) < 32 or ord(c) == 127 for c in value)
                or (key == 'username' and ':' in value)):
            raise ValueError('Invalid proxy credential')
        result[key] = value
    return result


def probe_worker(connection, config, target, timeout):
    sock = None
    stage = 'gateway_tcp'

    def progress(value):
        nonlocal stage
        stage = value
        connection.send({'event': 'stage', 'stage': stage})

    try:
        progress('gateway_tcp')
        sock = socket.create_connection((config['address'], config['port']), timeout=timeout)
        if config['scheme'] == 'https':
            progress('gateway_tls')
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=config['host'])
        credentials = base64.b64encode((config['username'] + ':' + config['password']).encode()).decode('ascii')
        progress('connect_write')
        sock.sendall(('CONNECT ' + target + ' HTTP/1.1\r\nHost: ' + target
                      + '\r\nProxy-Authorization: Basic ' + credentials + '\r\n\r\n').encode('ascii'))
        progress('connect_response')
        # Do not consume a response body or forward any bytes into the tunnel.
        headers = bytearray()
        while not headers.endswith(b'\r\n\r\n'):
            if len(headers) >= MAX_HEADERS:
                connection.send({'status': 'failed', 'failure_kind': 'headers_too_large'})
                return
            byte = sock.recv(1)
            if not byte:
                connection.send({'status': 'failed', 'failure_kind': 'incomplete_response'})
                return
            headers.extend(byte)
        match = re.fullmatch(rb'HTTP/1\.[01] ([1-5][0-9]{2})(?: [\x20-\x7e\x80-\xff]*)?',
                             bytes(headers).split(b'\r\n', 1)[0])
        if not match:
            connection.send({'status': 'failed', 'failure_kind': 'invalid_response'})
            return
        status = int(match[1])
        result = {'status': 'passed' if 200 <= status < 300 else 'failed',
                  'http_status': status, 'connect_accepted': 200 <= status < 300}
        if not result['connect_accepted']:
            result['failure_kind'] = 'proxy_http_status'
        connection.send(result)
    except Exception as error:
        kind = ('tls' if isinstance(error, ssl.SSLError) else
                'timeout' if isinstance(error, TimeoutError) else 'connection')
        try:
            connection.send({'status': 'failed', 'failure_kind': kind, 'stage': stage})
        except OSError:
            # The parent may already have retained its deadline failure and
            # closed the pipe. Do not replace it or emit a worker traceback.
            pass
    finally:
        if sock is not None:
            sock.close()
        connection.close()


def check(secret, target, output, *, timeout=15, worker=probe_worker):
    config, target = configuration(secret), authority(target)
    if not 1 <= timeout <= 120:
        raise ValueError('Timeout must be between 1 and 120 seconds')
    output = Path(output).absolute()
    if output.resolve().is_relative_to(ROOT):
        raise ValueError('Keep evidence outside the source checkout')
    output.mkdir(mode=0o700, exist_ok=False)
    report = {'status': 'checking', 'started_unix': time.time(), 'target': target,
              'gateway_protocol': config['scheme'], 'timeout_seconds': timeout,
              'stage': 'not_started', 'connect_accepted': False,
              'automatic_retry': False, 'direct_destination_connection': False,
              'destination_tls_or_http_sent': False, 'routing_modified': False,
              'geography_verified': False, 'native_browser_verified': False,
              'future_availability_verified': False}

    def save():
        partial = output / 'report.partial'
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as file:
            json.dump(report, file, indent=2)
            file.write('\n')
        partial.replace(output / 'report.json')

    save()
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=worker, args=(child, config, target, timeout))
    started = time.monotonic()
    try:
        process.start()
        child.close()
        while True:
            if not parent.poll(max(0, timeout - (time.monotonic() - started))):
                report.update(status='failed', failure_kind='deadline')
                break
            try:
                message = parent.recv()
            except EOFError:
                report.update(status='failed', failure_kind='probe_process_exited')
                break
            report.update({key: value for key, value in message.items() if key != 'event'})
            save()
            if message.get('event') != 'stage':
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
        report.update(elapsed_seconds=time.monotonic() - started, finished_unix=time.time())
        save()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True, help='Destination authority, for example example.org:443')
    parser.add_argument('--output', type=Path, required=True, help='New private evidence directory')
    parser.add_argument('--timeout', type=float, default=15)
    args = parser.parse_args(argv)
    try:
        report = check(read_json(sys.stdin.buffer.read(65537)), args.target, args.output, timeout=args.timeout)
    except (OSError, ValueError):
        parser.exit(2, 'Invalid gateway-check configuration or output path; existing evidence was preserved.\n')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
