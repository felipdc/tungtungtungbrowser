"""Optional native process identity diagnostics over a host-only VSOCK channel.

The resident helper reads kernel process metadata. These observations do not
establish foreground state, rendered content, or future process liveness.
"""
import json
import math
import re
import socket
import time

PORT = 1342
MAX_RESPONSE = 32768
UUID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')


class ProcessObserverError(RuntimeError):
    def __init__(self, message, *, phase=None, response=None, partial=b''):
        super().__init__(message)
        self.phase = phase
        self.response = response
        self.partial = partial


def positive_pid(value):
    if type(value) is not int or not 0 < value <= 2147483647:
        raise ValueError('PID must be a positive signed 32-bit integer')
    return value


def boot_identity(value):
    if not isinstance(value, str) or not UUID.fullmatch(value):
        raise ValueError('Expected a native boot UUID')
    return value.upper()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate process observer field')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('Non-finite JSON value: ' + value)


class ProcessObserver:
    def __init__(self, cid=3, timeout=5, connection_factory=None):
        if type(cid) is not int or not 2 < cid < 0xffffffff:
            raise ValueError('Guest CID must be between 3 and 4294967294')
        if (isinstance(timeout, bool) or not isinstance(timeout, (float, int))
                or not math.isfinite(timeout) or not 0 < timeout <= 30):
            raise ValueError('Observer timeout must be greater than 0 and at most 30 seconds')
        self.cid = cid
        self.timeout = timeout
        self.connection_factory = connection_factory or (
            lambda: socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM))

    def _request(self, command):
        deadline = time.monotonic() + self.timeout
        phase, data = 'connect', b''
        try:
            with self.connection_factory() as sock:
                def remaining():
                    value = deadline - time.monotonic()
                    if value <= 0:
                        raise TimeoutError('Original process observation exceeded its deadline')
                    sock.settimeout(value)

                remaining()
                sock.connect((self.cid, PORT))
                phase = 'send_request'
                remaining()
                sock.sendall(command.encode('ascii') + b'\n')
                phase = 'read_response'
                while True:
                    remaining()
                    chunk = sock.recv(min(4096, MAX_RESPONSE + 1 - len(data)))
                    if not chunk:
                        break
                    data += chunk
                    if len(data) > MAX_RESPONSE:
                        raise ValueError('Process observer response exceeds 32 KiB')
            if not data.endswith(b'\n') or data.count(b'\n') != 1:
                raise ValueError('Expected one complete process observation')
            result = json.loads(data, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
            if (not isinstance(result, dict) or type(result.get('protocol')) is not int
                    or result['protocol'] != 1):
                raise ValueError('Invalid process observer protocol')
            return result
        except (OSError, ValueError) as exc:
            raise ProcessObserverError(str(exc), phase=phase, partial=data) from exc

    def health(self):
        result = self._request('health')
        try:
            positive_pid(result.get('pid'))
            boot_identity(result.get('boot_uuid'))
            if (result.get('status') != 'ready' or result.get('read_only') is not True
                    or type(result.get('port')) is not int or result['port'] != PORT):
                raise ValueError('Invalid observer health response')
        except ValueError as exc:
            raise ProcessObserverError(str(exc), response=result) from exc
        return result

    def observe(self, pid):
        """Read one PID; retain absent, changed and failed native observations."""
        positive_pid(pid)
        result = self._request('pid ' + str(pid))
        try:
            boot_identity(result.get('boot_uuid'))
            for key in ('requested_pid', 'pid', 'start_seconds', 'start_microseconds',
                        'kernel_state', 'kernel_flags', 'elapsed_nanoseconds', 'errno'):
                if type(result.get(key)) is not int or result[key] < 0:
                    raise ValueError('Invalid process integer field: ' + key)
            if (result['requested_pid'] != pid or result.get('foreground_verified') is not False
                    or result.get('status') not in ('observed', 'absent', 'changed', 'not_alive', 'failed')
                    or not isinstance(result.get('stage'), str)
                    or not isinstance(result.get('executable'), str)):
                raise ValueError('Invalid process observation')
            if result['status'] == 'absent' and result['errno'] != 3:
                raise ValueError('Absent process must have the native ESRCH result')
        except ValueError as exc:
            raise ProcessObserverError(str(exc), response=result) from exc
        return result

    def stop(self, *, pid, boot_uuid):
        """Stop this diagnostic helper only, after checking its service identity."""
        positive_pid(pid)
        boot_uuid = boot_identity(boot_uuid)
        health = self.health()
        if health['pid'] != pid or boot_identity(health['boot_uuid']) != boot_uuid:
            raise ProcessObserverError('Observer service identity changed; stop not sent', response=health)
        result = self._request('stop')
        if (result.get('status') != 'stopping' or type(result.get('pid')) is not int
                or result['pid'] != pid or not isinstance(result.get('boot_uuid'), str) or result['boot_uuid'].upper() != boot_uuid):
            raise ProcessObserverError('Observer stop was not acknowledged; request not repeated', response=result)
        return result


def process_identity(result, *, pid, boot_uuid, executable=None):
    """Require a live, stable identity; this is not a foreground assertion."""
    positive_pid(pid)
    boot_uuid = boot_identity(boot_uuid)
    error = ProcessObserverError('Native process identity is absent, changed or incomplete', response=result)
    if (not isinstance(result, dict) or type(result.get('protocol')) is not int or result['protocol'] != 1
            or result.get('status') != 'observed' or result.get('stage') != 'complete'):
        raise error
    for key in ('requested_pid', 'pid', 'start_seconds', 'start_microseconds', 'kernel_state', 'kernel_flags', 'errno'):
        if type(result.get(key)) is not int or result[key] < 0:
            raise error
    try:
        observed_boot = boot_identity(result.get('boot_uuid'))
    except ValueError as exc:
        raise error from exc
    path = result.get('executable')
    if (observed_boot != boot_uuid or result['requested_pid'] != pid or result['pid'] != pid
            or result['start_seconds'] <= 0 or not 0 <= result['start_microseconds'] < 1000000
            or result['kernel_state'] not in (2, 3, 4) or result['kernel_flags'] & 4
            or result['errno'] != 0 or result.get('foreground_verified') is not False
            or not isinstance(path, str) or not path.startswith('/') or '\0' in path
            or (executable is not None and path != executable)):
        raise error
    return {'pid': pid, 'start_seconds': result['start_seconds'],
            'start_microseconds': result['start_microseconds'],
            'boot_uuid': boot_uuid, 'executable': path}
