#!/usr/bin/env python3
"""Own an optional native policy that blocks Safari UDP while other apps stay direct."""
import argparse
import base64
import errno
import hashlib
import json
from pathlib import Path
import socket
import struct
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tungtungtungbrowser.guest import Guest, GuestError

PORT = 1345
SCRIPT_TIMEOUT_SECONDS = 120
# Cold administrative-session initialization can consume the old 50-second
# allowance before the executable starts. Allow one bounded launch and enough
# HTTP time for the daemon's terminal reply; this does not authorize a retry.
RPC_TIMEOUT_SECONDS = SCRIPT_TIMEOUT_SECONDS + 15
SOURCES = ('safari_udp_guard.c', 'safari_udp_guard_libSystem.tbd',
           'process_observer_entitlements.plist')


def identity(value, status='ready'):
    if (not isinstance(value, dict) or value.get('protocol') != 2
            or value.get('status') != status or value.get('port') != PORT
            or type(value.get('pid')) is not int or not 1 < value['pid'] <= 2147483647
            or not isinstance(value.get('boot_uuid'), str)
            or str(uuid.UUID(value['boot_uuid'])).lower() != value['boot_uuid'].lower()):
        raise GuestError('Invalid Safari UDP guard identity')
    return value


def same_process(value, expected):
    if any(value.get(k) != expected.get(k) for k in ('pid', 'boot_uuid', 'port')):
        raise GuestError('Safari UDP guard belongs to another process or guest boot')


def exchange(cid, command):
    if type(cid) is not int or not 3 <= cid < 2**32 - 1:
        raise ValueError('Invalid guest CID')
    with socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        try:
            sock.connect((cid, PORT))
        except ConnectionResetError as error:
            # Linux virtio-vsock also uses RST for an unopened guest port.
            # Only a reset during connect means refusal: no request was sent.
            raise ConnectionRefusedError(errno.ECONNREFUSED,
                'Guest rejected VSOCK before connection establishment') from error
        sock.sendall(command.encode('ascii') + b'\n')
        reply = bytearray()
        while not reply.endswith(b'\n'):
            part = sock.recv(4096 - len(reply))
            if not part or len(reply) + len(part) >= 4096:
                raise GuestError('Incomplete or oversized Safari UDP guard response')
            reply.extend(part)
    return json.loads(reply)


def read_build(directory):
    directory = Path(directory)
    if (directory / 'build.json').stat().st_size > 16384:
        raise ValueError('Oversized Safari UDP guard manifest')
    binary = directory / 'safari-udp-guard'
    if not 32 <= binary.stat().st_size <= 256 * 1024:
        raise ValueError('Unsupported Safari UDP guard binary size')
    data = binary.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    manifest = json.loads((directory / 'build.json').read_text())
    if (manifest.get('protocol') != 1 or manifest.get('binary') != binary.name
            or manifest.get('binary_sha256') != digest
            or struct.unpack_from('<4I', data) != (0xFEEDFACF, 0x100000C, 0, 2)
            or manifest.get('sources') != {
                name: hashlib.sha256((ROOT / 'native' / name).read_bytes()).hexdigest()
                for name in SOURCES}):
        raise ValueError('Safari UDP guard build does not match this checkout')
    return data, digest


def external(path):
    path = Path(path).resolve()
    if path.is_relative_to(ROOT):
        raise ValueError('Keep deployment evidence outside the checkout')
    return path


def native_log(guest, path):
    value = guest._rpc('files.read', path=path, limit=65536)
    if value.get('truncated') is not False or not isinstance(value.get('content'), str):
        raise GuestError('Native guard log is incomplete')
    text = value['content']
    return text, [json.loads(line) for line in text.splitlines() if line.startswith('{')]


def install(guest, data, digest, output):
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Helper hash mismatch')
    output = external(output)
    output.mkdir(mode=0o700, exist_ok=False)
    path = '/private/var/root/ttt-safari-udp-' + uuid.uuid4().hex
    report = {'protocol': 1, 'status': 'starting', 'cid': guest.cid,
              'helper_sha256': digest, 'guest_path': path, 'log_path': path + '.log',
              'spawn_requested': False, 'native_input_sent': False,
              'launch_budget_seconds': SCRIPT_TIMEOUT_SECONDS,
              'scope': 'Safari effective application UUID, UDP only'}

    def save():
        (output / 'deploy.json').write_text(json.dumps(report, indent=2) + '\n')

    save()
    try:
        try:
            report['existing_guard'] = exchange(guest.cid, 'health')
        except ConnectionRefusedError:
            pass
        else:
            raise GuestError('A guard already exists; stop its owning deployment first')
        value = guest._rpc('files.write', path=path, encoding='base64',
                           content=base64.b64encode(data).decode())
        if value.get('bytes') != len(data):
            raise GuestError('Helper upload was not acknowledged')
        value = guest._rpc('files.read', path=path, binary=True, limit=len(data))
        if value.get('truncated') is not False or base64.b64decode(value['content'], validate=True) != data:
            raise GuestError('Helper readback mismatch')
        guest._rpc('files.chmod', path=path, mode='700')
        report['spawn_requested'] = True
        save()
        try:
            value = guest._rpc('script.eval', source='proc.run(' + json.dumps(path) + ', '
                               + json.dumps(['spawn', report['log_path']]) + ', "")',
                               timeout=SCRIPT_TIMEOUT_SECONDS)
        except (GuestError, OSError) as error:
            # Reconcile this one launch from its exclusive log and live identity.
            # A transport error never authorizes another spawn.
            report['launch_error'] = f'{type(error).__name__}: {error}'
            save()
            text, rows = native_log(guest, report['log_path'])
            (output / 'launch-reconciliation.log').write_text(text)
            listeners = [row for row in rows if row.get('status') == 'listening']
            if len(listeners) != 1:
                raise GuestError('Uncertain launch has no owned listener evidence') from error
            expected = identity(listeners[0], 'listening')
            report['spawn_reconciled_from_owned_log'] = True
        else:
            (output / 'spawn.json').write_text(json.dumps(value, indent=2) + '\n')
            if value.get('ok') is not True or value.get('crashed') is not False:
                raise GuestError('Native guard launcher failed; launch was not repeated')
            process = json.loads(value['result'])
            if process.get('status') != 0 or process.get('signal') is not None:
                raise GuestError('Native guard launcher exited unsuccessfully')
            expected = identity(json.loads(process['stdout']), 'spawned')
            if expected.get('spawn_error') != 0:
                raise GuestError('Native guard spawn failed')
        report['spawn'] = expected
        save()
        deadline = time.monotonic() + 30
        while True:
            try:
                health = identity(exchange(guest.cid, 'health'))
                break
            except (ConnectionRefusedError, TimeoutError):
                # The listener binds before policy initialization. A cold guest
                # may accept a health connection before it can answer it.
                # Repeat only this read within the original startup deadline.
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        same_process(health, expected)
        report['health'] = health
        text, rows = native_log(guest, report['log_path'])
        (output / 'native.log').write_text(text)
        policies = [row for row in rows if row.get('status') == 'safari_udp_policy_applied']
        listeners = [row for row in rows if row.get('status') == 'listening']
        if len(policies) != 1 or len(listeners) != 1:
            raise GuestError('Native policy and listener evidence are missing')
        policy = policies[0]
        if (policy.get('protocol') != 17 or policy.get('priority') != 4
                or type(policy.get('policy_id')) is not int or policy['policy_id'] <= 0
                or policy.get('other_applications') != 'unchanged'):
            raise GuestError('Unexpected native policy scope')
        uuid.UUID(policy['application_uuid'])
        same_process(identity(listeners[0], 'listening'), health)
        report.update(status='complete', native_policy=policy)
        return report
    except BaseException as error:
        report.update(status='failed_no_retry', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        save()


def read_deployment(path, cid):
    path = Path(path)
    if path.stat().st_size > 65536:
        raise ValueError('Oversized deployment record')
    record = json.loads(path.read_text())
    if record.get('status') != 'complete' or record.get('cid') != cid:
        raise ValueError('A verified deployment for this guest CID is required')
    identity(record['health'])
    prefix = '/private/var/root/ttt-safari-udp-'
    name = record.get('guest_path', '')
    if (not name.startswith(prefix) or len(name.removeprefix(prefix)) != 32
            or any(c not in '0123456789abcdef' for c in name.removeprefix(prefix))
            or record.get('log_path') != name + '.log'):
        raise ValueError('Invalid owned helper path')
    return record


def status(record):
    health = identity(exchange(record['cid'], 'health'))
    same_process(health, record['health'])
    return health


def stop(guest, record, output):
    if guest.cid != record['cid']:
        raise ValueError('Guest differs from deployment CID')
    expected = identity(record['health'])
    output = external(output)
    report = {'status': 'checking', 'stop_requested': False, 'identity': expected}
    with output.open('x') as file:
        json.dump(report, file, indent=2)

    def save():
        output.write_text(json.dumps(report, indent=2) + '\n')

    try:
        report['health'] = status(record)
        report['stop_requested'] = True
        save()
        response = identity(exchange(guest.cid, f"stop {expected['pid']} {expected['boot_uuid']}"), 'stopping')
        same_process(response, expected)
        report['acknowledgment'] = response
        save()
        deadline = time.monotonic() + 15
        while True:
            _, rows = native_log(guest, record['log_path'])
            closed = [row for row in rows if row.get('status') == 'owned_policy_session_closed']
            if closed:
                if len(closed) != 1 or closed[0].get('removed') is not True or closed[0].get('applied') is not True:
                    raise GuestError('Native policy removal was not verified')
                report.update(status='owned_policy_session_closed', native_close=closed[0])
                return report
            if time.monotonic() >= deadline:
                raise GuestError('Stop acknowledged; native policy removal remains unverified')
            time.sleep(0.25)
    except BaseException as error:
        report.update(status='failed_no_retry', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('start', 'status', 'stop'))
    parser.add_argument('source', type=Path, help='Build directory for start; deploy.json otherwise')
    parser.add_argument('--cid', type=int, default=3)
    parser.add_argument('--output', type=Path, help='New directory for start; new JSON file for stop')
    args = parser.parse_args()
    if args.mode != 'status' and args.output is None:
        parser.error('--output is required for start and stop')
    guest = Guest(cid=args.cid, timeout=RPC_TIMEOUT_SECONDS)
    if args.mode == 'start':
        data, digest = read_build(args.source)
        result = install(guest, data, digest, args.output)
    else:
        record = read_deployment(args.source, args.cid)
        result = status(record) if args.mode == 'status' else stop(guest, record, args.output)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
