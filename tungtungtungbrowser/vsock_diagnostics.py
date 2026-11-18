"""Read the original QEMU VSOCK endpoint and rings without contacting vphoned.

The caller supplies fresh Linux process ownership observations and retains the
returned report. Observations never authorize retries, boot or browser work.
"""
import math
import os
import platform
import re
import socket
import struct
import time

from tungtungtungbrowser.display import _Monitor
from tungtungtungbrowser.guest import GuestError
from tungtungtungbrowser.minimal_services import _owner


def _integer(value, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('Invalid VSOCK integer field')
    return value


def _ring_words(value, address):
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError('Invalid bounded ring read')
    match = re.fullmatch(r'\s*([0-9a-fA-F]+):\s+0x([0-9a-fA-F]{1,4})\s+0x([0-9a-fA-F]{1,4})\s*', value)
    if match is None or int(match[1], 16) != address:
        raise ValueError('Ring response does not identify the requested address')
    return {'flags': int(match[2], 16), 'index': int(match[3], 16)}


def inspect_vsock(qmp_socket, *, expected_cid, ownership, timeout=3):
    """Return complete or partial read-only observations from one QMP session.

    ``ownership`` returns the same fields as MinimalServicePolicy, with
    ``host_pid``/``host_start_ticks`` identifying QEMU itself. It must freshly
    check that process, its owning container, host boot and launch record.
    The QMP socket's peer PID must match. Ownership is checked before and after
    each query. The timeout bounds QMP I/O; the caller must bound its ownership
    callback and any surrounding process. Samples are sequential, not atomic.

    Read at most three queue descriptions and four bytes at each initialized
    avail/used address. No payload descriptors, guest RPC, control command,
    native input or recovery is issued. Query failures remain in the report.
    """
    if (type(expected_cid) is not int or not 3 <= expected_cid < 2**32 - 1
            or type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not 0 < timeout <= 10 or not callable(ownership)):
        raise ValueError('Require a valid CID, ownership callback and 0–10 second timeout')
    started = time.monotonic()
    report = {'status': 'observing', 'started_unix': time.time(),
              'timeout_seconds': timeout, 'expected_cid': expected_cid,
              'host_kernel': platform.release(), 'commands': [], 'queues': [],
              'guest_rpcs': 0, 'native_input': False, 'recovery': False,
              'atomic_snapshot': False, 'startup_authorized': False}
    owner = None

    def check():
        if _owner(ownership()) != owner:
            raise RuntimeError('Original QEMU ownership changed')

    try:
        owner = _owner(ownership())
        if owner['cid'] != expected_cid:
            raise ValueError('Owned launch CID differs from requested endpoint')
        report['owner'] = owner
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            monitor = _Monitor(connection, started + timeout)
            monitor.remaining()
            connection.connect(os.fspath(qmp_socket))
            peer = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            report['qmp_peer'] = dict(zip(('pid', 'uid', 'gid'), peer))
            if peer[0] != owner['host_pid']:
                raise ValueError('QMP peer is not the original QEMU process')
            check()
            if 'QMP' not in monitor.receive():
                raise GuestError('QMP greeting missing')

            def call(command, arguments=None):
                check()
                row = {'command': command, 'arguments': arguments,
                       'started_unix': time.time(), 'status': 'requested'}
                report['commands'].append(row)
                try:
                    value = monitor.call(command, arguments)
                    row.update(status='returned', result=value)
                    check()
                    return value
                except Exception as error:
                    row.update(status='failed', error_type=type(error).__name__, error=str(error))
                    raise
                finally:
                    row['finished_unix'] = time.time()

            call('qmp_capabilities')
            devices = call('x-query-virtio')
            if not isinstance(devices, list) or len(devices) > 128 or any(not isinstance(x, dict) for x in devices):
                raise ValueError('Invalid virtio device list')
            matches = [x for x in devices if x.get('name') in ('vhost-vsock', 'vhost-vsock-device')]
            if len(matches) != 1:
                raise ValueError('Require exactly one VSOCK device')
            path = matches[0].get('path')
            if not isinstance(path, str) or not path.startswith('/') or len(path) > 1024 or any(ord(x) < 32 for x in path):
                raise ValueError('Invalid VSOCK object path')
            report['device'] = matches[0]
            cid = call('qom-get', {'path': path, 'property': 'guest-cid'})
            if type(cid) is not int or cid != expected_cid:
                raise ValueError('QEMU VSOCK CID differs from owned launch')
            report['device_status'] = call('x-query-virtio-status', {'path': path})
            for queue in range(3):
                value = call('x-query-virtio-queue-status', {'path': path, 'queue': queue})
                if not isinstance(value, dict):
                    raise ValueError('Invalid queue description')
                row = {'queue': queue, 'description': value, 'rings': {}}
                report['queues'].append(row)
                count = _integer(value.get('vring-num'), 32768)
                for key in ('vring-desc', 'vring-avail', 'vring-used'):
                    _integer(value.get(key), 2**64 - 1)
                if not count or not all(value[key] for key in ('vring-desc', 'vring-avail', 'vring-used')):
                    row['status'] = 'uninitialized_no_memory_read'
                    continue
                for name in ('avail', 'used'):
                    address = value['vring-' + name]
                    if address % 2 or address > 2**64 - 4:
                        raise ValueError('Unaligned or overflowing ring address')
                    # Only canonical integers can enter the fixed read command.
                    raw = call('human-monitor-command', {'command-line': f'xp /2hx 0x{address:x}'})
                    row['rings'][name] = _ring_words(raw, address)
                row['status'] = 'observed'
            check()
            report['status'] = 'observed'
    except Exception as error:
        report.update(status='failed_observation', error_type=type(error).__name__, error=str(error))
    finally:
        report.update(finished_unix=time.time(), elapsed_seconds=time.monotonic() - started)
    return report
