"""Optional reduced-service configuration for an owned disposable browser VM.

Configure overrides once, verify a distinct clean boot, then restore enablement.
The caller owns boot/shutdown orchestration and must supply fresh host identity
observations. This module never stops services, sends signals or reboots a VM.
"""
import json
import math
import os
from pathlib import Path
import time
from types import MappingProxyType

CANDIDATES = MappingProxyType({
    'com.apple.biomed': '/System/Library/PrivateFrameworks/BiomeStreams.framework/Support/biomed',
    'com.apple.dasd': '/usr/libexec/dasd',
    'com.apple.healthappd': '/System/Library/PrivateFrameworks/HealthPluginHost.framework/healthappd',
    'com.apple.momentsd': '/usr/libexec/momentsd',
})


def _owner(value):
    required = ('container_id', 'host_boot_id', 'host_start_ticks')
    if (not isinstance(value, dict)
            or any(not isinstance(value.get(k), str) or not value[k] for k in required)
            or not value['host_start_ticks'].isdigit() or int(value['host_start_ticks']) <= 0
            or type(value.get('cid')) is not int or not 3 <= value['cid'] < 2**32 - 1
            or type(value.get('host_pid')) is not int or value['host_pid'] <= 1
            or type(value.get('launch_started_unix')) not in (int, float)
            or not math.isfinite(value['launch_started_unix']) or value['launch_started_unix'] <= 0):
        raise ValueError('Require a complete live guest and host-process identity')
    return dict(value)


class MinimalServicePolicy:
    """Three explicit lifecycle stages; the caller owns the guest's clean reboot.

    ``ownership`` must freshly observe the running container, host PID/start
    ticks, host boot ID, guest CID and launch timestamp on each invocation.
    The policy never infers a new boot from a timeout or automatically retries.
    """
    def __init__(self, directory, *, guest, ownership, closing=lambda: False):
        self.directory, self.guest = Path(directory), guest
        self.ownership, self.closing = ownership, closing

    def _arguments(self):
        owner = _owner(self.ownership())
        if self.guest.cid != owner['cid']:
            raise ValueError('Guest transport does not match the observed owned CID')
        return dict(rpc=self.guest._rpc, owner=owner, ownership=self.ownership, closing=self.closing)

    def configure(self):
        return configure_for_boot(self.directory, **self._arguments())

    def verify(self, clean_exit):
        return verify_after_boot(self.directory, stopped=clean_exit, **self._arguments())

    def restore(self):
        return restore_enablement(self.directory, **self._arguments())


def _write(path, value, *, exclusive=False):
    temporary = path if exclusive else path.with_suffix('.next')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(value, allow_nan=False, indent=2) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    if not exclusive:
        temporary.replace(path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _disabled(value):
    labels = value['disabled']
    # The native API returns an override mapping, including explicit false
    # entries. Its count is the number of entries, not the disabled total.
    if (not isinstance(labels, dict) or any(not isinstance(x, str) for x in labels)
            or any(type(v) is not bool for v in labels.values())
            or type(value['count']) is not int or value['count'] != len(labels)):
        raise ValueError('Malformed service override observation')
    return {label for label, disabled in labels.items() if disabled}


def _same_unrelated(before, after):
    return ({k: v for k, v in before['disabled'].items() if k not in CANDIDATES}
            == {k: v for k, v in after['disabled'].items() if k not in CANDIDATES})


def _service(label, value):
    if (value.get('label') != label or value.get('program') != CANDIDATES[label]
            or any(type(value.get(k)) is not bool for k in ('enabled', 'running', 'loaded'))
            or value['loaded'] is not True
            or type(value.get('pid')) is not int
            or (value['running'] and value['pid'] <= 1)
            or (not value['running'] and value['pid'] != 0)):
        raise ValueError('Unexpected service identity or state')
    return value


def _observe(rpc, *, disabling=True):
    profile = rpc('services.profile')
    if (profile.get('supported') is not True or profile.get('ios_major') != 26
            or not isinstance(profile.get('never_disable'), list)
            or (disabling and set(CANDIDATES).intersection(profile['never_disable']))):
        raise ValueError('Unsupported profile or protected candidate service')
    overrides = rpc('services.disabled')
    _disabled(overrides)
    services = {label: _service(label, rpc('services.status', label=label)) for label in CANDIDATES}
    return {'profile': profile, 'overrides': overrides, 'services': services}


class _Operation:
    def __init__(self, path, state, rpc, owner, ownership, closing):
        self.path, self.state, self.rpc = path, state, rpc
        self.owner, self.ownership, self.closing = owner, ownership, closing

    def check(self):
        if self.closing() or self.ownership() != self.owner:
            raise RuntimeError('Service operation no longer owns a usable guest')

    def call(self, method, label):
        self.check()
        row = {'method': method, 'label': label, 'status': 'requested', 'started_unix': time.time()}
        self.state['operations'].append(row)
        _write(self.path, self.state)
        # These services were observed in this exact guest. Never force a
        # protected service, repeat an unknown outcome, or act on another CID.
        params = {'label': label}
        if method == 'services.disable':
            params['force'] = True
        row['parameters'] = params
        _write(self.path, self.state)
        value = self.rpc(method, **params)
        row.update(response=value, status='response_received', finished_unix=time.time())
        _write(self.path, self.state)
        if not isinstance(value, dict) or value.get('errors'):
            raise ValueError('Native service operation did not return a valid acknowledgement')
        self.check()


def require_clean_exit(owner, stopped):
    owner = _owner(owner)
    clock = stopped.get('clock_maintenance') or {}
    shutdown = stopped.get('shutdown') or {}
    if (stopped.get('started_unix') != owner['launch_started_unix']
            or stopped.get('exit_code') != 0
            or shutdown.get('forced_termination') is not False
            or (shutdown.get('native_response') or {}).get('accepted') is not True
            or clock.get('status') != 'stopped'
            or clock.get('launch_started_unix') != owner['launch_started_unix']
            or type(clock.get('offers')) is not int or clock['offers'] < 1
            or type(stopped.get('finished_unix')) not in (int, float)
            or not math.isfinite(stopped['finished_unix'])):
        raise ValueError('Original preparation boot did not close cleanly')


def configure_for_boot(directory, *, rpc, owner, ownership, closing=lambda: False):
    owner = _owner(owner)
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    path = directory / 'state.json'
    state = {'status': 'observing', 'owner': owner, 'operations': [],
             'started_unix': time.time(), 'native_input_sent': False,
             'signals_sent': 0, 'mutations_replayed': 0}
    op = _Operation(path, state, rpc, owner, ownership, closing)
    _write(path, state, exclusive=True)
    try:
        op.check()
        before = state['before'] = _observe(rpc)
        if (_disabled(before['overrides']).intersection(CANDIDATES)
                or any(not x['enabled'] for x in before['services'].values())):
            raise ValueError('Require initially enabled original jobs')
        _write(path, state)
        for label in CANDIDATES:
            op.call('services.disable', label)
        after = state['after'] = _observe(rpc)
        if (_disabled(after['overrides']) != _disabled(before['overrides']) | set(CANDIDATES)
                or not _same_unrelated(before['overrides'], after['overrides'])
                or any(x['enabled'] for x in after['services'].values())):
            raise ValueError('Prepared override policy was not verified')
        op.check()
        state['status'] = 'configured_requires_clean_boot'
        return state
    except BaseException as error:
        state.update(status='failed_no_replay', failure_type=type(error).__name__)
        raise
    finally:
        state['finished_unix'] = time.time()
        _write(path, state)


def _boot_services(rpc, *, require_disabled=True):
    profile = rpc('services.profile')
    if (profile.get('supported') is not True or profile.get('ios_major') != 26
            or not isinstance(profile.get('never_disable'), list)
            or (require_disabled and set(CANDIDATES).intersection(profile['never_disable']))):
        raise ValueError('Unsupported or protected policy after boot')
    overrides = rpc('services.disabled')
    _disabled(overrides)
    rows = {}
    for label, program in CANDIDATES.items():
        row = rows[label] = rpc('services.status', label=label)
        if (row.get('label') != label
                or any(type(row.get(k)) is not bool for k in ('enabled', 'loaded', 'running'))
                or (row['loaded'] and row.get('program') != program)
                or (not row['loaded'] and row.get('program') not in (None, program))):
            raise ValueError('Unexpected post-boot service identity')
        pid = row.get('pid')
        if ((row['running'] and (not row['loaded'] or type(pid) is not int or pid <= 1))
                or (not row['running'] and not (pid is None or type(pid) is int and pid == 0))):
            raise ValueError('Inconsistent post-boot process state')
        if require_disabled and (row['enabled'] or row['running']):
            raise ValueError('Prepared service is enabled or running after boot')
    return {'profile': profile, 'overrides': overrides, 'services': rows}


def verify_after_boot(directory, *, rpc, owner, ownership, stopped, closing=lambda: False):
    owner = _owner(owner)
    directory = Path(directory)
    prepared = json.loads((directory / 'state.json').read_text())
    old = prepared['owner']
    if prepared['status'] != 'configured_requires_clean_boot':
        raise ValueError('No completed preparation to verify')
    require_clean_exit(old, stopped)
    if (owner['cid'] != old['cid'] or owner['host_boot_id'] != old['host_boot_id']
            or owner['container_id'] != old['container_id']
            or owner['launch_started_unix'] <= stopped['finished_unix']
            or (owner['host_pid'], owner['host_start_ticks']) == (old['host_pid'], old['host_start_ticks'])):
        raise ValueError('Require a new owned boot after the verified original exit')
    path = directory / 'verification.json'
    state = {'status': 'observing', 'owner': owner, 'original_owner': old,
             'clean_exit': stopped, 'operations': [], 'started_unix': time.time()}
    op = _Operation(path, state, rpc, owner, ownership, closing)
    _write(path, state, exclusive=True)
    try:
        op.check()
        after = state['after'] = _boot_services(rpc)
        if after['overrides'] != prepared['after']['overrides']:
            raise ValueError('Prepared overrides changed across boot')
        op.check()
        state['status'] = 'verified_after_clean_boot_requires_product_validation'
        return state
    except BaseException as error:
        state.update(status='failed_no_replay', failure_type=type(error).__name__)
        raise
    finally:
        state['finished_unix'] = time.time()
        _write(path, state)


def restore_enablement(directory, *, rpc, owner, ownership, closing=lambda: False):
    owner = _owner(owner)
    directory = Path(directory)
    prepared = json.loads((directory / 'state.json').read_text())
    verified = json.loads((directory / 'verification.json').read_text())
    if (verified['status'] != 'verified_after_clean_boot_requires_product_validation'
            or verified['owner'] != owner):
        raise ValueError('No completed policy verified on this boot')
    path = directory / 'restore.json'
    state = {'status': 'observing', 'owner': owner, 'operations': [],
             'started_unix': time.time(), 'original_processes_restored': False}
    op = _Operation(path, state, rpc, owner, ownership, closing)
    _write(path, state, exclusive=True)
    try:
        op.check()
        before = state['before'] = _boot_services(rpc)
        if before['overrides'] != prepared['after']['overrides']:
            raise ValueError('Applied policy drifted before restoration')
        for label in CANDIDATES:
            op.call('services.enable', label)
        after = state['after'] = _boot_services(rpc, require_disabled=False)
        if (_disabled(after['overrides']) != _disabled(prepared['before']['overrides'])
                or not _same_unrelated(prepared['before']['overrides'], after['overrides'])
                or any(not row['enabled'] for row in after['services'].values())):
            raise ValueError('Original enablement policy was not restored')
        op.check()
        state.update(status='enablement_configuration_restored',
                     explicit_enabled_override_entries_may_remain=True)
        return state
    except BaseException as error:
        state.update(status='failed_no_replay', failure_type=type(error).__name__)
        raise
    finally:
        state['finished_unix'] = time.time()
        _write(path, state)
