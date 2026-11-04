#!/usr/bin/env python3
"""Install and validate the optional read-only process observer; send no UI input."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import plistlib
import struct
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tungtungtungbrowser.guest import Guest, GuestError
from tungtungtungbrowser.process_observer import ProcessObserver, ProcessObserverError, process_identity

SOURCES = ('process_observer.c', 'process_observer_libSystem.tbd', 'process_observer_entitlements.plist')


def read_build(directory):
    directory = Path(directory)
    manifest, binary = directory / 'build.json', directory / 'process-observer'
    if manifest.stat().st_size > 16384 or not 32 <= binary.stat().st_size <= 256 * 1024:
        raise ValueError('Unsupported observer build size')
    with manifest.open() as stream:
        text = stream.read(16385)
    if len(text) > 16384:
        raise ValueError('Observer manifest exceeds 16 KiB')
    build = json.loads(text)
    sources = {name: hashlib.sha256((ROOT / 'native' / name).read_bytes()).hexdigest() for name in SOURCES}
    if (not isinstance(build, dict) or type(build.get('protocol')) is not int
            or build['protocol'] != 1 or build.get('binary') != 'process-observer'
            or build.get('sources') != sources):
        raise ValueError('Observer build does not match this source checkout')
    with binary.open('rb') as stream:
        data = stream.read(256 * 1024 + 1)
    if not 32 <= len(data) <= 256 * 1024 or struct.unpack_from('<4I', data) != (0xFEEDFACF, 0x100000C, 0, 2):
        raise ValueError('Expected an arm64 Mach-O observer executable')
    digest = hashlib.sha256(data).hexdigest()
    if digest != build.get('binary_sha256'):
        raise ValueError('Observer binary does not match its manifest')
    return data, digest


def install(guest, observer, data, digest, destination):
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Observer binary digest mismatch')
    destination = Path(destination)
    if destination.resolve().is_relative_to(ROOT):
        raise ValueError('Keep observer evidence outside the repository')
    destination.mkdir(mode=0o700, exist_ok=False)
    executable = '/private/var/root/ttt-process-observer-v1-' + digest
    label = 'dev.tungtungtungbrowser.process-observer-v1'
    plist = '/private/var/root/' + label + '-' + uuid.uuid4().hex + '.plist'
    log = '/private/var/root/' + label + '.log'
    report = {'status': 'installing', 'helper_sha256': digest, 'path': executable,
              'label': label, 'plist': plist, 'log': log, 'calls': [],
              'observations': [], 'native_ui_input_sent': False, 'foreground_verified': False}

    def save():
        (destination / 'deploy.json').write_text(json.dumps(report, indent=2) + '\n')

    def rpc(method, **params):
        row = {'method': method, 'params': {k: v for k, v in params.items() if k != 'content'},
               'started_monotonic': time.monotonic()}
        report['calls'].append(row)
        save()
        try:
            result = guest._rpc(method, **params)
            row['result'] = {k: v for k, v in result.items() if k != 'content'}
            return result
        except Exception as exc:
            row['error'] = str(exc)
            if isinstance(exc, ProcessObserverError):
                row['response'] = exc.response
                row['phase'] = exc.phase
                row['partial_base64'] = base64.b64encode(exc.partial).decode()
            raise
        finally:
            row['finished_monotonic'] = time.monotonic()
            save()

    def put(path, content, mode):
        staging = path + '.staging-' + uuid.uuid4().hex
        result = rpc('files.write', path=staging, encoding='base64', content=base64.b64encode(content).decode())
        if result.get('path') != staging or result.get('bytes') != len(content):
            raise GuestError('Observer upload was not acknowledged')
        result = rpc('files.read', path=staging, binary=True, limit=len(content))
        if (result.get('path') != staging or result.get('encoding') != 'base64'
                or result.get('truncated') is not False or result.get('size') != len(content)
                or base64.b64decode(result['content'], validate=True) != content):
            raise GuestError('Observer file readback failed')
        rpc('files.chmod', path=staging, mode=mode)
        result = rpc('files.rename', **{'from': staging, 'to': path})
        if result.get('source') != staging or result.get('destination') != path:
            raise GuestError('Observer installation was not acknowledged')

    def observe(name, operation):
        row = {'operation': name, 'started_monotonic': time.monotonic()}
        report['observations'].append(row)
        save()
        try:
            row['result'] = operation()
            return row['result']
        except Exception as exc:
            row['error'] = str(exc)
            if isinstance(exc, ProcessObserverError):
                row['response'] = exc.response
                row['phase'] = exc.phase
                row['partial_base64'] = base64.b64encode(exc.partial).decode()
            raise
        finally:
            row['finished_monotonic'] = time.monotonic()
            save()

    save()
    try:
        put(executable, data, '700')
        service = plistlib.dumps({'Label': label, 'ProgramArguments': [executable, 'serve'],
                                 'RunAtLoad': True, 'KeepAlive': False,
                                 'StandardOutPath': log, 'StandardErrorPath': log})
        (destination / 'service.plist').write_bytes(service)
        put(plist, service, '600')
        report['status'] = 'load_requested'
        save()
        report['load'] = rpc('services.load', paths=[plist], override=False)
        deadline = time.monotonic() + 60
        while True:
            try:
                health = observe('health', observer.health)
                break
            except ProcessObserverError as exc:
                # Poll only a refused/reset connection while the original load
                # starts. A timeout or malformed reply is retained as failure.
                cause = exc.__cause__
                if (exc.phase != 'connect' or not isinstance(cause, OSError)
                        or isinstance(cause, TimeoutError) or cause.errno not in (111, 104)
                        or time.monotonic() >= deadline):
                    raise
                time.sleep(0.5)
        report['health'] = health
        own = observe('own_process', lambda: observer.observe(health['pid']))
        report['service_identity'] = process_identity(
            own, pid=health['pid'], boot_uuid=health['boot_uuid'], executable=executable)
        missing = observe('absent_process', lambda: observer.observe(2147483647))
        if missing['status'] != 'absent' or missing['boot_uuid'] != health['boot_uuid']:
            raise ProcessObserverError('Observer negative control failed', response=missing)
        report.update(status='complete', negative_control=missing)
        return report
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        raise
    finally:
        save()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build_directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cid', type=int, default=3)
    args = parser.parse_args(argv)
    try:
        data, digest = read_build(args.build_directory)
        result = install(Guest(cid=args.cid, timeout=60), ProcessObserver(cid=args.cid), data, digest, args.output)
    except (OSError, ValueError, GuestError, ProcessObserverError, KeyError) as exc:
        parser.exit(1, f'Observer installation failed: {exc}. Evidence is retained; no UI input was sent.\n')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
