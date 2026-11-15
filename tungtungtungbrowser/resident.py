"""Opt-in native helpers for a bounded, warmed Safari session.

Configuration sends no input. Reads remain fresh; keyboard dispatches never
retry or fall back to another transport after an uncertain result.
"""
import errno
import json
import math
from pathlib import Path
import re
import socket
import threading
import time
import uuid

from .guest import Guest, GuestError
from .native_keyboard import KEYS, validate_key_result

PORTS = {'lease': 1346, 'focus': 1347, 'control': 1348}
PROTOCOLS = {'lease': 1, 'focus': 2, 'control': 2}
SESSION_SECONDS = 1650  # Stop submitting before the native 1800-second deadline.


def read_json(path, limit=256 * 1024):
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError('Oversized resident helper record')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Resident helper record must be an object')
    return value


def identity(value, kind, status='ready'):
    protocol = 2 if status == 'spawned' else PROTOCOLS[kind]
    if (not isinstance(value, dict) or type(value.get('protocol')) is not int
            or value['protocol'] != protocol or value.get('status') != status
            or type(value.get('port')) is not int or value['port'] != PORTS[kind]
            or type(value.get('pid')) is not int or not 1 < value['pid'] < 2**31
            or not isinstance(value.get('boot_uuid'), str)):
        raise GuestError('Invalid resident helper identity')
    try:
        if str(uuid.UUID(value['boot_uuid'])).lower() != value['boot_uuid'].lower():
            raise ValueError()
    except ValueError as error:
        raise GuestError('Invalid resident helper boot identity') from error
    return value


def same_process(value, expected, *, pid_key='pid'):
    if (not isinstance(value, dict) or type(value.get(pid_key)) is not int
            or value[pid_key] != expected['pid']
            or value.get('boot_uuid') != expected['boot_uuid']):
        raise GuestError('Resident helper belongs to another process or guest boot')


def exchange(cid, kind, command, *, timeout=45):
    if type(cid) is not int or not 3 <= cid < 2**32 - 1 or kind not in PORTS:
        raise ValueError('Invalid resident helper endpoint')
    if (not isinstance(command, str) or not command or len(command) > 126
            or any(ord(c) < 32 or ord(c) > 126 for c in command)):
        raise ValueError('Invalid resident helper command')
    with socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((cid, PORTS[kind]))
        except ConnectionResetError as error:
            # Only a reset before connection establishment means no listener.
            raise ConnectionRefusedError(errno.ECONNREFUSED, 'Guest VSOCK listener absent') from error
        sock.sendall(command.encode('ascii') + b'\n')
        data = bytearray()
        deadline = time.monotonic() + timeout
        while len(data) < 65536:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Resident response deadline exceeded; request is not repeated')
            sock.settimeout(remaining)
            chunk = sock.recv(min(4096, 65536 - len(data)))
            if not chunk:
                raise GuestError('Incomplete resident helper response')
            data.extend(chunk)
            if data.endswith(b'\n'):
                try:
                    value = json.loads(data)
                except (ValueError, UnicodeDecodeError):
                    # Nested focus JSON contains its own newline; TCP/VSOCK may
                    # split the outer envelope exactly at that inner boundary.
                    continue
                if not isinstance(value, dict):
                    raise GuestError('Resident helper returned a non-object')
                return value
        raise GuestError('Oversized resident helper response')


class ResidentSession:
    """Use a session started by scripts/resident_browser.py; never starts one."""
    def __init__(self, directory, *, cid):
        self.directory = Path(directory).resolve()
        self.record = read_json(self.directory / 'session.json')
        if (type(self.record.get('version')) is not int or self.record['version'] != 1
                or self.record.get('status') != 'complete'
                or self.record.get('name') != 'browser-resident'
                or type(cid) is not int or type(self.record.get('cid')) is not int
                or self.record['cid'] != cid):
            raise ValueError('A complete resident session for this guest CID is required')
        self.cid = cid
        self.helpers = {}
        self._locks = {kind: threading.Lock() for kind in PORTS}
        for kind in PORTS:
            state = read_json(self.directory / kind / 'deploy.json')
            if (state.get('status') != 'complete' or type(state.get('cid')) is not int
                    or state['cid'] != cid or state.get('kind') != kind):
                raise ValueError('Incomplete or mismatched resident helper deployment')
            hashes = state.get('binaries')
            names = {'guardian', 'controller'} if kind == 'lease' else {'server'}
            if (not isinstance(hashes, dict) or set(hashes) != names
                    or any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)
                           for value in hashes.values())):
                raise ValueError('Resident deployment has invalid binary hashes')
            identity(state['health'], kind)
            same_process(state['health'], identity(state['spawn'], kind, 'spawned'))
            self.helpers[kind] = state
        starts = [s.get('launch_requested_unix') for s in self.helpers.values()]
        if any(type(t) not in (int, float) or not math.isfinite(t) for t in starts):
            raise ValueError('Missing resident helper launch time')
        self.accept_until = min(starts) + SESSION_SECONDS
        self.assert_active()

    def assert_active(self):
        if time.time() >= self.accept_until or (self.directory / 'stop.json').exists():
            raise GuestError('Resident session expired or is closing; start a new owned session')

    def _query(self, kind, command):
        with self._locks[kind]:
            self.assert_active()
            started = time.monotonic()
            row = {'started_unix': time.time(), 'command': command.split(' ', 1)[0]}
            try:
                result = exchange(self.cid, kind, command)
                row['response'] = result
                return result
            except BaseException as error:
                row['error'] = f'{type(error).__name__}: {error}'
                raise
            finally:
                row['seconds'] = time.monotonic() - started
                with (self.directory / kind / 'requests.jsonl').open('a') as file:
                    file.write(json.dumps(row) + '\n')

    def focus(self, pid):
        if type(pid) is not int or not 1 < pid < 2**31:
            raise ValueError('Focus query requires a native process ID')
        value = self._query('focus', 'pid ' + str(pid))
        same_process(value, self.helpers['focus']['health'])
        if (type(value.get('protocol')) is not int or value['protocol'] != 2
                or value.get('status') != 'query' or type(value.get('exit_code')) is not int
                or value['exit_code'] != 0 or not isinstance(value.get('result'), dict)):
            raise GuestError('Resident focus query failed')
        # NativeFocusReader applies its existing guard, PID, attribute and
        # completeness checks, including the bounded read-only retry policy.
        return value['result']

    def foreground(self):
        value = self._query('control', 'inspect')
        same_process(value, self.helpers['control']['health'], pid_key='helper_pid')
        if (type(value.get('protocol')) is not int or value['protocol'] != 2
                or value.get('status') != 'observed' or type(value.get('verified')) is not bool
                or value.get('strict_ambiguity') is not True
                or value.get('source') != 'runningboard_unique_focal_process'
                or type(value.get('focal_process_count')) is not int
                or type(value.get('candidate_count')) is not int
                or type(value.get('pid')) is not int or not isinstance(value.get('bundle_id'), str)
                or type(value.get('screen_state_valid')) is not bool
                or any(type(value.get(k)) is not int for k in ('locked', 'screen_off', 'process_start'))):
            raise GuestError('Invalid resident foreground evidence')
        if value['verified'] and (value['focal_process_count'] != 1 or value['candidate_count'] < 1
                or not 1 < value['pid'] < 2**31 or value['process_start'] <= 0
                or value['bundle_id'] != 'com.apple.mobilesafari'
                or not value['screen_state_valid'] or value['locked'] != 0 or value['screen_off'] != 0):
            raise GuestError('Inconsistent resident foreground state')
        return value

    def key(self, name):
        if not isinstance(name, str) or name not in KEYS:
            raise ValueError('Unsupported native navigation key')
        state = self.helpers['control']
        owner = state['health']
        value = self._query('control', f"key {owner['pid']} {owner['boot_uuid']} {name}")
        same_process(value, owner, pid_key='helper_pid')
        if (type(value.get('protocol')) is not int or value['protocol'] != 2
                or value.get('status') != 'key' or type(value.get('exit_code')) is not int
                or value['exit_code'] != 0):
            raise GuestError('Resident key dispatch failed; input is not repeated')
        result = validate_key_result(name, value.get('result'), state['binaries']['server'])
        result['method'] = 'native_resident_keyboard'
        return result


class ResidentGuest(Guest):
    """Keep the ordinary guest transport for other calls, including paste."""
    def __init__(self, guest, session):
        if guest.cid != session.cid:
            raise ValueError('Resident session and Guest target different CIDs')
        super().__init__(cid=guest.cid, connection_factory=guest.connection_factory,
                         touch_helper_sha256=guest.touch_helper_sha256,
                         keyboard_helper_sha256=guest.keyboard_helper_sha256)
        self.base = guest
        self.resident = session

    def _rpc(self, method, **params):
        return self.base._rpc(method, **params)

    def call(self, method, **params):
        self.resident.assert_active()
        if method == 'input.key':
            if set(params) != {'name'}:
                raise ValueError('Native navigation key requires only name')
            return self.resident.key(params['name'])
        if method == 'apps.foreground':
            if params:
                raise ValueError('Foreground inspection takes no arguments')
            return self.resident.foreground()
        return self.base.call(method, **params)
