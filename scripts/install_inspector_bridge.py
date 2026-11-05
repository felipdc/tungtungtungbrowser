#!/usr/bin/env python3
"""Install the optional native Inspector bridge; retain a verified deployment record."""
import argparse
import asyncio
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
from tungtungtungbrowser.inspector_transport import InspectorTransport, vsock_connection

SOURCES = ('inspector_bridge.c', 'inspector_bridge_libSystem.tbd', 'inspector_bridge_entitlements.plist')


def read_build(directory):
    directory = Path(directory)
    manifest, binary = directory / 'build.json', directory / 'inspector-bridge'
    if manifest.stat().st_size > 16384 or not 32 <= binary.stat().st_size <= 256 * 1024:
        raise ValueError('Invalid Inspector bridge build size')
    build = json.loads(manifest.read_text())
    expected = {name: hashlib.sha256((ROOT / 'native' / name).read_bytes()).hexdigest() for name in SOURCES}
    data = binary.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if (build.get('protocol') != 1 or build.get('binary') != 'inspector-bridge'
            or build.get('sources') != expected or build.get('binary_sha256') != digest
            or struct.unpack_from('<4I', data) != (0xFEEDFACF, 0x100000C, 0, 2)):
        raise ValueError('Bridge build must match this source checkout and an arm64 Mach-O executable')
    return data, digest


async def health(cid):
    transport = InspectorTransport(cid=cid, timeout=5)
    sock = await asyncio.wait_for(vsock_connection(cid, 1343), 5)
    try:
        transport.reader, transport.writer = await asyncio.open_connection(sock=sock, limit=1024)
        return await transport._control('health', 'ready')
    finally:
        # No handoff was opened, so close only this health connection.
        await transport.close()


def install(guest, data, digest, output, health_check):
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Bridge binary digest mismatch')
    output = Path(output).resolve()
    if output.is_relative_to(ROOT):
        raise ValueError('Keep deployment evidence outside the repository')
    output.mkdir(mode=0o700, exist_ok=False, parents=True)
    token = uuid.uuid4().hex
    label = 'dev.tungtungtungbrowser.inspector-' + token
    executable = '/private/var/root/ttt-inspector-bridge-v1-' + digest
    plist = '/private/var/root/' + label + '.plist'
    log = '/private/var/root/' + label + '.log'
    report = {'status': 'installing', 'helper_sha256': digest, 'path': executable,
              'label': label, 'plist': plist, 'log': log, 'calls': [],
              'inspection_preferences_changed': False}

    def save():
        (output / 'deploy.json').write_text(json.dumps(report, indent=2) + '\n')

    def rpc(method, **params):
        row = {'method': method, 'started_monotonic': time.monotonic()}
        report['calls'].append(row)
        save()
        try:
            result = guest._rpc(method, **params)
            row['result'] = {k: v for k, v in result.items() if k != 'content'}
            return result
        except Exception as error:
            row['error'] = str(error)
            raise
        finally:
            row['finished_monotonic'] = time.monotonic()
            save()

    def put(path, content, mode):
        staging = path + '.staging-' + token
        result = rpc('files.write', path=staging, encoding='base64', content=base64.b64encode(content).decode())
        if result.get('bytes') != len(content):
            raise GuestError('Bridge upload was not acknowledged')
        result = rpc('files.read', path=staging, binary=True, limit=len(content))
        if (result.get('truncated') is not False or result.get('size') != len(content)
                or base64.b64decode(result['content'], validate=True) != content):
            raise GuestError('Bridge upload failed readback verification')
        rpc('files.chmod', path=staging, mode=mode)
        result = rpc('files.rename', **{'from': staging, 'to': path})
        if result.get('source') != staging or result.get('destination') != path:
            raise GuestError('Bridge installation was not acknowledged')

    save()
    try:
        try:
            existing = health_check()
        except ConnectionRefusedError:
            pass
        else:
            report['existing_bridge'] = existing
            raise GuestError('A bridge is already running; stop its owning deployment before replacing it')
        put(executable, data, '700')
        service = plistlib.dumps({'Label': label, 'ProgramArguments': [executable, 'serve'],
                                 'RunAtLoad': True, 'KeepAlive': False,
                                 'StandardOutPath': log, 'StandardErrorPath': log})
        (output / 'service.plist').write_bytes(service)
        put(plist, service, '600')
        loaded = rpc('services.load', paths=[plist], override=False)
        rows = [row for row in loaded.get('services', []) if row.get('label') == label]
        if len(rows) != 1 or not rows[0].get('running') or rows[0].get('program') != executable:
            raise GuestError('Native bridge service identity was not verified')
        deadline = time.monotonic() + 30
        while True:
            try:
                report['health'] = health_check()
                break
            except ConnectionRefusedError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        if report['health']['pid'] != rows[0]['pid']:
            raise GuestError('Bridge health belongs to another service process')
        report['status'] = 'complete'
        return report
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build_directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cid', type=int, default=3)
    args = parser.parse_args()
    try:
        data, digest = read_build(args.build_directory)
        result = install(Guest(cid=args.cid, timeout=30), data, digest, args.output,
                         lambda: asyncio.run(health(args.cid)))
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        parser.exit(1, f'Inspector installation failed: {error}. Deployment evidence is retained.\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
