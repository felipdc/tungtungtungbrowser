#!/usr/bin/env python3
"""Set Safari's HTTP(S) relay preferences while Safari is stopped; no app launch."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import plistlib
import struct
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tungtungtungbrowser.guest import Guest, GuestError

ENDPOINT = 'http://10.0.2.2:1080'
KEYS = ('WebKit2HTTPProxy', 'WebKit2HTTPSProxy')


def read_build(directory):
    directory = Path(directory)
    if (directory / 'build.json').stat().st_size > 16384 or not 32 <= (directory / 'safari-proxy').stat().st_size <= 256 * 1024:
        raise ValueError('Unsupported Safari proxy build size')
    build = json.loads((directory / 'build.json').read_text())
    data = (directory / 'safari-proxy').read_bytes()
    sources = {name: hashlib.sha256((ROOT / 'native' / name).read_bytes()).hexdigest()
               for name in ('safari_proxy.c', 'safari_proxy_libSystem.tbd', 'process_observer_entitlements.plist')}
    if (build.get('protocol') != 1 or build.get('binary') != 'safari-proxy'
            or build.get('sources') != sources or not 32 <= len(data) <= 256 * 1024
            or struct.unpack_from('<4I', data) != (0xFEEDFACF, 0x100000C, 0, 2)
            or build.get('binary_sha256') != hashlib.sha256(data).hexdigest()):
        raise ValueError('Native Safari proxy build does not match this checkout')
    return data, build['binary_sha256']


def configure(guest, data, digest, output, mode):
    if mode not in ('read', 'browser', 'direct') or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Invalid mode or helper hash')
    output = Path(output).resolve()
    if output.is_relative_to(ROOT):
        raise ValueError('Keep deployment evidence outside the checkout')
    output.mkdir(mode=0o700, exist_ok=False)
    path = '/private/var/root/ttt-safari-proxy-' + uuid.uuid4().hex
    report = {'status': 'starting', 'cid': guest.cid, 'mode': mode, 'helper_sha256': digest,
              'guest_path': path, 'calls': [], 'app_launch_requested': False,
              'page_javascript_injected': False, 'system_proxy_modified': False}

    def save():
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    def call(method, **params):
        row = {'method': method, 'status': 'requested'}
        report['calls'].append(row)
        save()
        result = guest._rpc(method, **params)
        row['status'] = 'received'
        save()
        return result

    def run(action):
        value = call('script.eval', source='proc.run(' + json.dumps(path) + ', ' + json.dumps([action]) + ', "")', timeout=40)
        if not isinstance(value, dict) or value.get('ok') is not True or value.get('crashed') is not False:
            raise GuestError('Native Safari proxy command failed')
        process = json.loads(value['result'])
        (output / (action + '-' + str(len(report['calls'])) + '-process.json')).write_text(json.dumps(process, indent=2))
        if process.get('status') != 0 or process.get('signal') is not None:
            raise GuestError('Native Safari proxy command refused or failed; inspect its retained process result')
        result = plistlib.loads(process['stdout'].encode())
        if result.get('status') != 'complete' or result.get('application') != 'com.apple.mobilesafari':
            raise GuestError('Unexpected Safari preference result')
        return result

    save()
    try:
        uploaded = call('files.write', path=path, encoding='base64', content=base64.b64encode(data).decode())
        if uploaded.get('bytes') != len(data):
            raise GuestError('Helper upload was not acknowledged')
        readback = call('files.read', path=path, binary=True, limit=len(data))
        if readback.get('truncated') is not False or base64.b64decode(readback['content'], validate=True) != data:
            raise GuestError('Helper readback mismatch')
        call('files.chmod', path=path, mode='700')
        report['before'] = run('read')
        save()
        if mode != 'read':
            action = 'enable' if mode == 'browser' else 'disable'
            report['mutation'] = run(action)
            save()
            report['after'] = run('read')
            expected = dict.fromkeys(KEYS, ENDPOINT) if mode == 'browser' else {}
            if report['after']['after'] != expected:
                raise GuestError('Separate-process preference readback mismatch')
        report['status'] = 'preferences_verified' if mode != 'read' else 'observed'
        report['browser_routing_verified'] = False
        return report
    except BaseException as error:
        report.update(status='failed_no_retry', error=str(error))
        raise
    finally:
        save()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('build_directory', type=Path)
    p.add_argument('--cid', type=int, default=3)
    p.add_argument('--mode', choices=['read', 'browser', 'direct'], default='read')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    data, digest = read_build(a.build_directory)
    print(json.dumps(configure(Guest(cid=a.cid, timeout=50), data, digest, a.output, a.mode), indent=2))


if __name__ == '__main__':
    main()
