#!/usr/bin/env python3
"""Build verification and explicit lifecycle for a bounded native browser session."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tungtungtungbrowser.guest import Guest, GuestError
from tungtungtungbrowser.resident import PORTS, PROTOCOLS, SESSION_SECONDS
from tungtungtungbrowser.resident import exchange, identity, lease_scope, read_json, same_process

BINARIES = {'lease': ('guardian', 'controller'), 'focus': ('server',), 'control': ('server',)}
COMMON = ('focus_guard.h', 'focus_guard_trace.h', 'focus_deadline.h',
          'address_field.h', 'focus_resolution.h', 'focus_attributes.h')


def external(path):
    path = Path(path).resolve()
    if path.is_relative_to(ROOT):
        raise ValueError('Keep generated builds and session evidence outside the checkout')
    return path


def dump(path, value):
    with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix=path.name + '.', delete=False) as file:
        json.dump(value, file, indent=2); file.write('\n')
        temporary = Path(file.name)
    temporary.replace(path)


def sources():
    paths = [ROOT / 'native' / name for name in COMMON]
    paths += sorted((ROOT / 'native/resident').rglob('*'))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths if p.is_file()}


def binary_bytes(path):
    if not 32 <= path.stat().st_size <= 512 * 1024:
        raise ValueError('Unsupported resident binary size')
    data = path.read_bytes()
    if struct.unpack_from('<4I', data) != (0xFEEDFACF, 0x100000C, 0, 2):
        raise ValueError('Resident binary must be an ARM64 Mach-O executable')
    return data


def manifest(directory):
    directory = external(directory)
    value = {'version': 1, 'name': 'browser-resident', 'sources': sources(), 'binaries': {}}
    for kind, names in BINARIES.items():
        value['binaries'][kind] = {name: hashlib.sha256(binary_bytes(directory / kind / name)).hexdigest()
                                   for name in names}
    with (directory / 'build.json').open('x') as file:
        json.dump(value, file, indent=2); file.write('\n')
    return value


def read_build(directory):
    directory = external(directory)
    value = read_json(directory / 'build.json')
    if (value.get('version') != 1 or value.get('name') != 'browser-resident'
            or value.get('sources') != sources() or set(value.get('binaries', {})) != set(BINARIES)):
        raise ValueError('Resident build does not match this checkout')
    data = {}
    for kind, names in BINARIES.items():
        if set(value['binaries'][kind]) != set(names):
            raise ValueError('Incomplete resident build')
        data[kind] = {}
        for name in names:
            contents = binary_bytes(directory / kind / name)
            if hashlib.sha256(contents).hexdigest() != value['binaries'][kind][name]:
                raise ValueError('Resident binary hash mismatch')
            data[kind][name] = contents
    return value, data


def log_rows(guest, state, directory):
    value = guest._rpc('files.read', path=state['log_path'], limit=65536)
    if value.get('truncated') is not False or not isinstance(value.get('content'), str):
        raise GuestError('Incomplete resident helper log')
    text = value['content']
    (directory / 'native.log').write_text(text)
    return [json.loads(line) for line in text.splitlines() if line.startswith('{')]


def process_result(value):
    if (not isinstance(value, dict) or value.get('ok') is not True or value.get('crashed') is not False
            or value.get('output') != '' or not isinstance(value.get('result'), str)
            or len(value['result']) > 65536):
        raise GuestError('Native resident launcher failed; launch is not repeated')
    process = json.loads(value['result'])
    if (type(process.get('status')) is not int or process['status'] != 0
            or process.get('signal', 0) is not None or process.get('stderr') != ''):
        raise GuestError('Native resident launcher exited unsuccessfully')
    return json.loads(process['stdout'])


def start_helper(guest, kind, data, hashes, directory):
    directory.mkdir(mode=0o700)
    out = directory / 'deploy.json'
    state = {'version': 1, 'kind': kind, 'cid': guest.cid, 'status': 'starting',
             'binaries': hashes, 'paths': {}, 'spawn_requested': False, 'native_input_sent': False}
    dump(out, state)
    try:
        try:
            exchange(guest.cid, kind, 'health', timeout=5)
        except ConnectionRefusedError:
            pass
        else:
            raise GuestError('A resident listener already exists; use its owning session')
        for name, contents in data.items():
            parent = '/private/var/tmp' if kind == 'focus' else '/private/var/root'
            path = parent + '/ttt-resident-' + kind + '-' + name + '-' + uuid.uuid4().hex
            state['paths'][name] = path
            dump(out, state)
            answer = guest._rpc('files.write', path=path, encoding='base64',
                                content=base64.b64encode(contents).decode())
            if answer.get('bytes') != len(contents):
                raise GuestError('Resident upload not acknowledged')
            answer = guest._rpc('files.read', path=path, binary=True, limit=len(contents))
            if answer.get('truncated') is not False or base64.b64decode(answer['content'], validate=True) != contents:
                raise GuestError('Resident upload readback mismatch')
            guest._rpc('files.chmod', path=path, mode='755' if kind == 'focus' else '700')
        entry = state['paths']['controller' if kind == 'lease' else 'server']
        argument = state['paths']['guardian'] if kind == 'lease' else entry
        state['log_path'] = '/private/var/root/ttt-resident-' + kind + '-' + uuid.uuid4().hex + '.log'
        state.update(spawn_requested=True, launch_requested_unix=time.time())
        dump(out, state)
        try:
            answer = guest._rpc('script.eval', source='proc.run(' + json.dumps(entry) + ', '
                                + json.dumps(['spawn', state['log_path'], argument]) + ', "")', timeout=120)
        except (GuestError, OSError) as error:
            # Read-only reconciliation of this exclusive log, never a second spawn.
            state['launch_error'] = f'{type(error).__name__}: {error}'
            dump(out, state)
            rows = log_rows(guest, state, directory)
            listeners = [r for r in rows if r.get('status') == 'listening']
            if len(listeners) != 1:
                raise GuestError('Uncertain launch lacks owned listener evidence') from error
            expected = identity(listeners[0], kind, 'listening')
            state['spawn_reconciled'] = True
            state['spawn'] = {**expected, 'protocol': 2, 'status': 'spawned', 'spawn_error': 0}
        else:
            dump(directory / 'spawn.json', answer)
            expected = identity(process_result(answer), kind, 'spawned')
            if any(type(expected.get(k)) is not int or expected[k] != 0 for k in
                   ('spawn_error', 'actions_destroy_error', 'attributes_destroy_error')):
                raise GuestError('Native resident spawn did not complete cleanly')
            state['spawn'] = expected
        dump(out, state)
        deadline = time.monotonic() + 45
        while True:
            try:
                live = identity(exchange(guest.cid, kind, 'health', timeout=5), kind)
                break
            except (ConnectionRefusedError, TimeoutError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.25)
        same_process(live, expected)
        rows = log_rows(guest, state, directory)
        listeners = [r for r in rows if r.get('status') == 'listening']
        if len(listeners) != 1:
            raise GuestError('Owned resident listener log missing')
        same_process(identity(listeners[0], kind, 'listening'), live)
        if kind == 'lease':
            enabled = [r for r in rows if r.get('status') == 'enabled']
            if (len(enabled) != 1 or enabled[0].get('after') != {'application': 1, 'automation': 1}
                    or any(type(enabled[0].get('before', {}).get(k)) is not int
                           or enabled[0]['before'][k] not in (0, 1) for k in ('application', 'automation'))):
                raise GuestError('Accessibility lease activation not verified')
            same_process(enabled[0], live)
            state['enabled'] = enabled[0]
        state.update(status='complete', health=live, finished_unix=time.time())
        return state
    except BaseException as error:
        state.update(status='failed_no_retry', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        dump(out, state)


def owned_state(directory, kind, cid):
    state = read_json(directory / 'deploy.json')
    if state.get('kind') != kind or state.get('cid') != cid:
        raise ValueError('Resident deployment CID or kind mismatch')
    if state.get('spawn_requested'):
        pattern = r'/private/var/root/ttt-resident-' + kind + r'-[0-9a-f]{32}\.log'
        if not re.fullmatch(pattern, state.get('log_path', '')):
            raise ValueError('Invalid owned resident log path')
    return state


def closed_record(rows, state, kind):
    closed = [r for r in rows if r.get('status') == 'closed']
    if not closed:
        return None
    if len(closed) != 1:
        raise GuestError('Ambiguous resident closure evidence')
    value = closed[0]
    same_process(value, state['spawn'])
    if kind == 'lease':
        before = state.get('enabled', {}).get('before')
        if (before is None or value.get('restored') is not True
                or value.get('before') != before or value.get('after') != before):
            raise GuestError('Accessibility lease restoration not verified')
    if kind == 'focus':
        pool = value.get('guard_pool', {})
        if pool.get('remaining') != 0 or pool.get('failed') != 0:
            raise GuestError('Resident focus guardians did not close cleanly')
    return value


def stop_helper(guest, directory, kind):
    state = owned_state(directory, kind, guest.cid)
    out = directory / 'stop.json'
    result = read_json(out) if out.exists() else {'status': 'checking', 'stop_requested': False}
    if result.get('status') in ('restored', 'listener_closed', 'not_started'):
        return result
    if not state.get('spawn_requested'):
        result['status'] = 'not_started'; dump(out, result); return result
    rows = log_rows(guest, state, directory)
    if 'spawn' not in state:
        listeners = [r for r in rows if r.get('status') == 'listening']
        if len(listeners) != 1:
            raise GuestError('Cannot establish ownership of uncertain native launch')
        state['spawn'] = identity(listeners[0], kind, 'listening')
    if kind == 'lease' and 'enabled' not in state:
        enabled = [r for r in rows if r.get('status') == 'enabled']
        if len(enabled) != 1:
            raise GuestError('Cannot establish lease restoration baseline')
        state['enabled'] = enabled[0]
    closed = closed_record(rows, state, kind)
    if closed is None and not result['stop_requested']:
        live = identity(exchange(guest.cid, kind, 'health', timeout=5), kind)
        same_process(live, state['spawn'])
        result.update(status='stopping', stop_requested=True, identity=live)
        dump(out, result)
        try:
            ack = identity(exchange(guest.cid, kind, f"stop {live['pid']} {live['boot_uuid']}"), kind, 'stopping')
            same_process(ack, live); result['ack'] = ack
        except (GuestError, OSError) as error:
            result['stop_error'] = f'{type(error).__name__}: {error}'
        dump(out, result)
    deadline = time.monotonic() + 45
    while closed is None:
        if time.monotonic() >= deadline:
            raise TimeoutError('Native resident closure not yet verified; stop is not repeated')
        time.sleep(.25)
        closed = closed_record(log_rows(guest, state, directory), state, kind)
    result.update(status='restored' if kind == 'lease' else 'listener_closed',
                  native=closed, finished_unix=time.time())
    dump(out, result)
    return result


def stop(directory, *, guest=None):
    directory = external(directory)
    session = read_json(directory / 'session.json')
    guest = guest or Guest(cid=session['cid'], timeout=135)
    if guest.cid != session['cid']:
        raise ValueError('Guest differs from resident session CID')
    out = directory / 'stop.json'
    result = {'status': 'closing', 'helpers': {}, 'errors': []}
    dump(out, result)  # SDK refuses all new calls as soon as closure starts.
    for kind in reversed(PORTS):
        if not (directory / kind / 'deploy.json').exists():
            continue
        try:
            result['helpers'][kind] = stop_helper(guest, directory / kind, kind)
        except Exception as error:
            result['errors'].append({'kind': kind, 'error': f'{type(error).__name__}: {error}'})
        dump(out, result)
    result.update(status='closed' if not result['errors'] else 'closure_unverified', finished_unix=time.time())
    dump(out, result)
    return result


def start(build, directory, *, cid=3, guest=None, accessibility_scope='session'):
    if accessibility_scope not in ('session', 'navigation'):
        raise ValueError('accessibility_scope must be session or navigation')
    manifest, data = read_build(build)  # Verify every binary before the first guest operation.
    directory = external(directory)
    guest = guest or Guest(cid=cid, timeout=135)
    if guest.cid != cid:
        raise ValueError('Guest differs from requested CID')
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    out = directory / 'session.json'
    state = {'version': 2 if accessibility_scope == 'navigation' else 1,
             'accessibility_scope': accessibility_scope,
             'name': 'browser-resident', 'status': 'starting', 'cid': cid,
             'started_unix': time.time(), 'helpers': {}, 'native_input_sent': False,
             'maximum_native_lifetime_seconds': 1800, 'submission_budget_seconds': SESSION_SECONDS}
    dump(out, state)
    try:
        # Preflight all ports before enabling the accessibility lease.
        for kind in PORTS:
            try:
                exchange(cid, kind, 'health', timeout=5)
            except ConnectionRefusedError:
                continue
            raise GuestError('An existing native listener owns this CID')
        for kind in PORTS:
            state['helpers'][kind] = start_helper(guest, kind, data[kind], manifest['binaries'][kind], directory / kind)
            dump(out, state)
        if accessibility_scope == 'navigation':
            deployment = state['helpers']['lease']
            owner = deployment['health']
            lease_scope(owner, deployment, active=True, status='ready')
            state['pause_requested'] = True
            dump(out, state)  # An uncertain transition is never replayed.
            paused = exchange(cid, 'lease', f"pause {owner['pid']} {owner['boot_uuid']}")
            state['initial_pause'] = lease_scope(paused, deployment, active=False)
        state.update(status='complete', finished_unix=time.time())
        return state
    except BaseException as error:
        state.update(status='failed_no_retry', error=f'{type(error).__name__}: {error}')
        dump(out, state)
        state['cleanup'] = stop(directory, guest=guest)
        raise
    finally:
        dump(out, state)


def status(directory):
    from tungtungtungbrowser.resident import ResidentSession
    directory = external(directory)
    record = read_json(directory / 'session.json')
    session = ResidentSession(directory, cid=record['cid'])
    observations = {}
    for kind, state in session.helpers.items():
        live = identity(exchange(session.cid, kind, 'health', timeout=5), kind)
        same_process(live, state['health']); observations[kind] = live
    return {'status': 'ready', 'cid': session.cid, 'accept_until_unix': session.accept_until,
            'helpers': observations, 'native_input_sent': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    make = commands.add_parser('manifest'); make.add_argument('build', type=Path)
    begin = commands.add_parser('start'); begin.add_argument('build', type=Path)
    begin.add_argument('--cid', type=int, default=3); begin.add_argument('--output', type=Path, required=True)
    begin.add_argument('--accessibility-scope', choices=('session', 'navigation'), default='session',
                       help='Experimental navigation scope restores settings between native actions')
    for name in ('status', 'stop'):
        commands.add_parser(name).add_argument('directory', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'manifest': result = manifest(args.build)
        elif args.command == 'start':
            result = start(args.build, args.output, cid=args.cid, accessibility_scope=args.accessibility_scope)
        else: result = globals()[args.command](args.directory)
        print(json.dumps(result, indent=2))
        if result.get('status') == 'closure_unverified': return 1
    except (ValueError, OSError, GuestError, KeyError) as error:
        parser.exit(1, f'Resident operation stopped: {error}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
