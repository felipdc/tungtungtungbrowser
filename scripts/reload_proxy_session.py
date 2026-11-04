#!/usr/bin/env python3
"""Explicitly reload proxy credentials inside the existing egress container.

Read the replacement private proxy JSON from stdin. Preserve gateway, routes,
firewall and namespace; send one SIGHUP to the verified router process. This
requests a reload, not successful upstream connectivity or browser rendering.
"""
import argparse
import copy
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path('/run/proxy-config.json')
MAX_CONFIG = 65536


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def read_json(data):
    if not data or len(data) > MAX_CONFIG:
        raise ValueError('Expected a JSON object of at most 64 KiB')
    def reject_constant(_):
        raise ValueError('Non-finite JSON value')

    result = json.loads(data, object_pairs_hook=unique_object, parse_constant=reject_constant)
    if not isinstance(result, dict):
        raise ValueError('Expected a JSON object')
    return result


def replace_credentials(configuration, secret):
    """Change only credentials of the original sole upstream outbound."""
    candidate = copy.deepcopy(configuration)
    outbounds = candidate.get('outbounds')
    if not isinstance(outbounds, list) or len(outbounds) != 1 or not isinstance(outbounds[0], dict):
        raise ValueError('Expected one upstream outbound')
    outbound = outbounds[0]
    scheme = secret.get('scheme')
    address = str(ipaddress.IPv4Address(secret.get('address')))
    port = secret.get('port')
    if type(port) is not int or not 1 <= port <= 65535 or scheme not in {'http', 'https', 'socks5'}:
        raise ValueError('Unsupported proxy protocol or port')
    expected_type = 'socks' if scheme == 'socks5' else 'http'
    tls = outbound.get('tls', {})
    if (outbound.get('tag') != 'upstream' or outbound.get('type') != expected_type
            or outbound.get('server') != address or outbound.get('server_port') != port
            or not isinstance(tls, dict)
            or bool(tls.get('enabled', False)) != (scheme == 'https')
            or (scheme == 'https' and tls.get('server_name') != secret.get('host'))
            or (scheme == 'socks5' and outbound.get('version') != '5')):
        raise ValueError('Gateway, protocol and TLS identity must remain unchanged')
    for key in ('username', 'password'):
        value = secret.get(key)
        if (not isinstance(value, str) or not 1 <= len(value.encode('utf-8')) <= 4096
                or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise ValueError('Invalid proxy credential')
        outbound[key] = value
    return candidate


def process_identity(config):
    command = Path('/proc/1/cmdline').read_bytes().rstrip(b'\0').split(b'\0')
    expected = [b'run', b'-c', os.fsencode(config)]
    if not command or Path(os.fsdecode(command[0])).name != 'sing-box' or command[1:] != expected:
        raise ValueError('Run inside the egress container with sing-box as PID 1')
    stat = Path('/proc/1/stat').read_text().rsplit(')', 1)[1].split()
    if stat[0] in {'Z', 'X'}:
        raise ValueError('Router process is not running')
    return {'pid': 1, 'start_ticks': stat[19],
            'network_namespace': os.readlink('/proc/1/ns/net'),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def private_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())


def reload_session(secret, output, *, config=CONFIG):
    output, config = Path(output), Path(config)
    if output.resolve().is_relative_to(ROOT):
        raise ValueError('Keep reload evidence and its private backup outside the repository')
    output.mkdir(mode=0o700, exist_ok=False)
    report_path = output / 'report.json'
    report = {'status': 'preparing', 'started_unix': time.time(), 'reload_requests': 0,
              'configuration_replaced': False, 'upstream_verified': False,
              'browser_verified': False, 'automatic_retry': False}
    pidfd = None
    lock = None

    def save():
        temporary = output / 'report.next'
        private_write(temporary, json.dumps(report, indent=2).encode() + b'\n')
        temporary.replace(report_path)

    save()
    try:
        lock_fd = os.open(config.with_name(config.name + '.reload.lock'),
                          os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        lock = os.fdopen(lock_fd, 'r+b')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = process_identity(config)
        report['process_before'] = before
        pidfd = os.pidfd_open(1)
        if config.is_symlink() or not config.is_file() or config.stat().st_size > MAX_CONFIG:
            raise ValueError('Expected a bounded regular router configuration')
        original = config.read_bytes()
        candidate = replace_credentials(read_json(original), secret)
        encoded = json.dumps(candidate, allow_nan=False).encode() + b'\n'
        private_write(output / 'previous-config.json', original)
        staging = output / 'candidate-config.json'
        private_write(staging, encoded)
        report.update(previous_sha256=hashlib.sha256(original).hexdigest(),
                      candidate_sha256=hashlib.sha256(encoded).hexdigest())
        checked = subprocess.run(['sing-box', 'check', '-c', str(staging)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        report['syntax_check_exit'] = checked.returncode
        save()
        if checked.returncode != 0:
            raise ValueError('Candidate configuration rejected')
        if process_identity(config) != before or config.read_bytes() != original:
            raise ValueError('Router identity or original configuration changed')
        # Atomic replacement must stay on the same filesystem. A cross-device
        # error leaves the original untouched; no copy fallback or signal.
        staging.replace(config)
        report['configuration_replaced'] = True
        save()
        report['reload_requests'] = 1
        report['signal_requested_monotonic'] = time.monotonic()
        save()
        signal.pidfd_send_signal(pidfd, signal.SIGHUP)
        report.update(status='reload_requested', finished_unix=time.time())
        save()
    except Exception as error:
        # Native exception text may include private input; retain only its type
        # and the completed stage, never credentials or subprocess output.
        report.update(status='failed', failure_type=type(error).__name__, finished_unix=time.time())
        save()
    finally:
        if pidfd is not None:
            os.close(pidfd)
        if lock is not None:
            lock.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True,
                        help='New private evidence directory on the /run filesystem')
    args = parser.parse_args()
    try:
        secret = read_json(sys.stdin.buffer.read(MAX_CONFIG + 1))
        report = reload_session(secret, args.output)
    except Exception as error:
        print(json.dumps({'status': 'failed', 'failure_type': type(error).__name__,
                          'reload_requests': None, 'outcome_unknown': True}))
        return 1
    print(json.dumps(report))
    return 0 if report['status'] == 'reload_requested' else 1


if __name__ == '__main__':
    raise SystemExit(main())
