#!/usr/bin/env python3
"""Install the optional native Inspector bridge; retain a verified deployment record."""
import argparse
import asyncio
import base64
import errno
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
SCRIPT_TIMEOUT_SECONDS = 30
# The daemon stops and reaps a timed-out script before returning its error.
# An equal HTTP deadline can discard that terminal reply and leave the launch
# outcome uncertain. Allow bounded time for cleanup and delivery; never replay.
RPC_TIMEOUT_SECONDS = SCRIPT_TIMEOUT_SECONDS + 15


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
    try:
        sock = await asyncio.wait_for(vsock_connection(cid, 1343), 5)
    except ConnectionResetError as error:
        # Linux virtio-vsock reports a peer's RST during connection setup as
        # ECONNRESET, including when the guest port has no listener. No control
        # request has been sent. Normalize only this connect-stage rejection;
        # a reset after connection must not authorize another deployment.
        raise ConnectionRefusedError(errno.ECONNREFUSED,
            'Guest rejected the VSOCK connection before establishment') from error
    try:
        transport.reader, transport.writer = await asyncio.open_connection(sock=sock, limit=1024)
        return await transport._control('health', 'ready')
    finally:
        # No handoff was opened, so close only this health connection.
        await transport.close()


def install(guest, data, digest, output, health_check, *, launch_mode='service'):
    if launch_mode not in ('service', 'process'):
        raise ValueError('launch_mode must be service or process')
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Bridge binary digest mismatch')
    output = Path(output).resolve()
    if output.is_relative_to(ROOT):
        raise ValueError('Keep deployment evidence outside the repository')
    output.mkdir(mode=0o700, exist_ok=False, parents=True)
    token = uuid.uuid4().hex
    label = 'dev.tungtungtungbrowser.inspector-' + token
    # A stopped deployment can leave its executable behind across a guest boot.
    # The guest's atomic rename refuses an existing destination. Give each
    # deployment its own executable as well as its own launchd label; never
    # overwrite a binary that another deployment may still have mapped.
    executable = '/private/var/root/ttt-inspector-bridge-v1-' + digest + '-' + token
    plist = '/private/var/root/' + label + '.plist'
    log = '/private/var/root/' + label + '.log'
    report = {'status': 'installing', 'launch_mode': launch_mode, 'helper_sha256': digest, 'path': executable,
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

    def capture_failure_diagnostics():
        # launchd can report a PID while its job is still in xpcproxy, before
        # the executable has initialized or opened its log. Preserve that state
        # without restarting input, replacing a process, or masking the failure.
        diagnostics = report['failure_diagnostics'] = {}
        if launch_mode == 'process':
            # This exact, unique log belongs to the one requested spawn. Keep
            # uncertain launches visible; do not start or kill another process.
            try:
                diagnostics['log'] = rpc('files.read', path=log, limit=16384)
            except Exception as error:
                diagnostics['log_error'] = f'{type(error).__name__}: {error}'
            return
        try:
            status = rpc('services.status', label=label)
            diagnostics['service'] = status
            if (not isinstance(status, dict) or status.get('label') != label
                    or status.get('program') != executable):
                diagnostics['identity_verified'] = False
                return
            diagnostics['identity_verified'] = True
        except Exception as error:
            diagnostics['service_error'] = f'{type(error).__name__}: {error}'
            return
        for name, method, params in (
                ('launchd', 'services.print', {'label': label}),
                ('log', 'files.read', {'path': log, 'limit': 16384})):
            try:
                diagnostics[name] = rpc(method, **params)
            except Exception as error:
                diagnostics[name + '_error'] = f'{type(error).__name__}: {error}'

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
        if launch_mode == 'service':
            service = plistlib.dumps({'Label': label, 'ProgramArguments': [executable, 'serve'],
                                     'RunAtLoad': True, 'KeepAlive': False,
                                     'StandardOutPath': log, 'StandardErrorPath': log})
            (output / 'service.plist').write_bytes(service)
            put(plist, service, '600')
            report['service_load_requested'] = True
            loaded = rpc('services.load', paths=[plist], override=False)
            rows = [row for row in loaded.get('services', []) if row.get('label') == label]
            if len(rows) != 1 or not rows[0].get('running') or rows[0].get('program') != executable:
                raise GuestError('Native bridge service identity was not verified')
            expected_pid = rows[0]['pid']
        else:
            report['process_spawn_requested'] = True
            source = 'proc.run(' + json.dumps(executable) + ', ' + json.dumps(['spawn', log]) + ', "")'
            answer = rpc('script.eval', source=source, timeout=SCRIPT_TIMEOUT_SECONDS)
            try:
                if (not isinstance(answer, dict) or answer.get('ok') is not True
                        or answer.get('crashed') is not False or answer.get('output') != ''
                        or not isinstance(answer.get('result'), str) or len(answer['result']) > 65536):
                    raise ValueError('Native launcher did not complete')
                process = json.loads(answer['result'])
                if (not isinstance(process, dict) or type(process.get('status')) is not int
                        or process['status'] != 0 or process.get('signal', 0) is not None
                        or process.get('stderr') != '' or not isinstance(process.get('stdout'), str)):
                    raise ValueError('Native launcher exited unsuccessfully')
                spawned = report['spawn'] = json.loads(process['stdout'])
                if (not isinstance(spawned, dict) or type(spawned.get('protocol')) is not int
                        or spawned['protocol'] != 2 or spawned.get('status') != 'spawned'
                        or type(spawned.get('pid')) is not int or not 1 < spawned['pid'] <= 2147483647
                        or type(spawned.get('port')) is not int or spawned['port'] != 1343
                        or type(spawned.get('spawn_error')) is not int or spawned['spawn_error'] != 0
                        or not isinstance(spawned.get('boot_uuid'), str)):
                    raise ValueError('Invalid native spawn identity')
                uuid.UUID(spawned['boot_uuid'])
                expected_pid = spawned['pid']
            except (KeyError, TypeError, ValueError) as error:
                raise GuestError('Native bridge spawn was not verified; launch was not repeated') from error
        deadline = time.monotonic() + 30
        while True:
            try:
                report['health'] = health_check()
                break
            except ConnectionRefusedError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        if report['health']['pid'] != expected_pid:
            raise GuestError('Bridge health belongs to another process')
        if launch_mode == 'process' and report['health'].get('boot_uuid') != spawned['boot_uuid']:
            raise GuestError('Bridge health belongs to another guest boot')
        report['status'] = 'complete'
        return report
    except Exception as error:
        report.update(status='failed', error=str(error))
        if report.get('service_load_requested') or report.get('process_spawn_requested'):
            capture_failure_diagnostics()
        raise
    finally:
        save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build_directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cid', type=int, default=3)
    parser.add_argument('--launch-mode', choices=('service', 'process'), default='service',
                        help='Use launchd (default) or an explicitly owned process; neither retries a failed launch')
    args = parser.parse_args()
    try:
        data, digest = read_build(args.build_directory)
        result = install(Guest(cid=args.cid, timeout=RPC_TIMEOUT_SECONDS), data, digest, args.output,
                         lambda: asyncio.run(health(args.cid)), launch_mode=args.launch_mode)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        parser.exit(1, f'Inspector installation failed: {error}. Deployment evidence is retained.\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
