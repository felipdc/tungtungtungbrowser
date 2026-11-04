#!/usr/bin/env python3
"""Sample a running container's entire cgroup, including renderer subprocesses.

Run on the Linux Docker host. Memory is cgroup usage, not summed process RSS
(which counts shared guest RAM more than once). CPU cores used is CPU seconds
divided by elapsed wall seconds. No guest or page code is executed.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time


def counters(path):
    return {key: int(value) for key, value in
            (line.split() for line in path.read_text().splitlines())}


def read_pressure(path, *, required=('some', 'full')):
    """Read cumulative PSI stall microseconds; unavailable is not zero."""
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return None
    result = {}
    for line in lines:
        kind, *fields = line.split()
        values = dict(field.split('=', 1) for field in fields)
        total = int(values['total'])
        if kind not in ('some', 'full') or kind in result or total < 0:
            raise ValueError(f'Malformed {path.name} counters')
        result[kind] = total
    if not set(required) <= result.keys():
        raise ValueError(f'Missing {path.name} counters')
    return {key: result[key] for key in required}


def memory_pressure(group):
    return read_pressure(group / 'memory.pressure')


def host_memory(proc_root):
    """Read host headroom separately from the container's memory accounting."""
    wanted = {'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'}
    memory = {}
    for line in (proc_root / 'meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        if key in wanted:
            amount, unit = value.split()
            if unit != 'kB' or key in memory or int(amount) < 0:
                raise ValueError('Malformed host memory counters')
            memory[key] = int(amount) * 1024
    if memory.keys() != wanted:
        raise ValueError('Missing host memory counters')
    oom_kills = counters(proc_root / 'vmstat')['oom_kill']
    if oom_kills < 0:
        raise ValueError('Malformed host OOM counter')
    return {'memory_bytes': memory, 'oom_kills': oom_kills,
            'pressure_usec': read_pressure(proc_root / 'pressure/memory')}


def runtime_limits(group):
    """Keep kernel values distinct from Docker's requested configuration."""
    result = {}
    for name in ('cpu.max', 'cpuset.cpus.effective', 'memory.max', 'memory.swap.max'):
        try:
            result[name] = (group / name).read_text().strip()
        except FileNotFoundError:
            result[name] = None
    return result


def collect(container, output_path, *, seconds, interval, include_host_memory=False,
            include_cpu_pressure=False,
            proc_root=Path('/proc'), cgroup_root=Path('/sys/fs/cgroup')):
    if not 0 < interval <= seconds <= 3600:
        raise ValueError('Require 0 < interval <= seconds <= 3600')
    inspect = json.loads(subprocess.check_output(
        ['docker', 'inspect', '--type', 'container', container], text=True))[0]
    if not inspect['State']['Running']:
        raise ValueError('Container is not running')
    pid = inspect['State']['Pid']
    proc = proc_root / str(pid)
    start_ticks = proc.joinpath('stat').read_text().rsplit(')', 1)[1].split()[19]
    cgroup = next((line[3:] for line in proc.joinpath('cgroup').read_text().splitlines()
                   if line.startswith('0::')), None)
    if cgroup is None:
        raise ValueError('A cgroup v2 Docker host is required')
    group = cgroup_root.joinpath(cgroup.lstrip('/'))
    limits = runtime_limits(group)
    host_boot_id = proc_root.joinpath('sys/kernel/random/boot_id').read_text().strip()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Recordings can contain host/container identifiers; keep new files private.
    # Exclusive creation also preserves evidence when an output path is reused.
    fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        def emit(value):
            output.write(json.dumps(value) + '\n')
            output.flush()

        started_ns = time.monotonic_ns()
        emit({'kind': 'configuration', 'container_id': inspect['Id'],
              'image': inspect['Image'], 'started_at': inspect['State']['StartedAt'],
              'memory_limit_bytes': inspect['HostConfig']['Memory'],
              'cpu_quota_nanocpus': inspect['HostConfig']['NanoCpus'],
              'cgroup_limits': limits,
              'cgroup': cgroup, 'requested_seconds': seconds,
              'interval_seconds': interval, 'host_boot_id': host_boot_id,
              'host_pid': pid, 'pid_start_ticks': start_ticks,
              'clock': 'CLOCK_MONOTONIC', 'started_monotonic_ns': started_ns,
              'include_host_memory': include_host_memory,
              'include_cpu_pressure': include_cpu_pressure})
        previous = None
        maximum = 0
        while True:
            if proc.joinpath('stat').read_text().rsplit(')', 1)[1].split()[19] != start_ticks:
                raise RuntimeError('Container process changed during measurement')
            if runtime_limits(group) != limits:
                raise RuntimeError('Container resource limits changed during measurement')
            now_ns = time.monotonic_ns()
            memory = int(group.joinpath('memory.current').read_text())
            cpu = counters(group / 'cpu.stat')
            sample = {'kind': 'sample', 'at': datetime.now(timezone.utc).isoformat(),
                      'monotonic_ns': now_ns,
                      'elapsed_seconds': (now_ns - started_ns) / 1e9,
                      'memory_bytes': memory,
                      'memory_stat': counters(group / 'memory.stat'), 'cpu_stat': cpu,
                      'memory_pressure_usec': memory_pressure(group),
                      'memory_events': counters(group / 'memory.events')}
            if include_host_memory:
                sample['host_memory'] = host_memory(proc_root)
            if include_cpu_pressure:
                sample['cpu_pressure_usec'] = {
                    'container': read_pressure(group / 'cpu.pressure'),
                    # System-level CPU full is undefined. Older kernels may
                    # expose only some; newer kernels report full as zero.
                    'host': read_pressure(proc_root / 'pressure/cpu', required=('some',)),
                }
            peak = group / 'memory.peak'
            if peak.exists():
                sample['peak_bytes_since_container_created'] = int(peak.read_text())
            if previous:
                sample['cpu_cores_used'] = ((cpu['usage_usec'] - previous[1]) / 1e6 /
                                            ((now_ns - previous[0]) / 1e9))
            emit(sample)
            maximum = max(maximum, memory)
            previous = now_ns, cpu['usage_usec']
            remaining = seconds - (time.monotonic_ns() - started_ns) / 1e9
            if remaining <= 0:
                break
            time.sleep(min(interval, remaining))
        if runtime_limits(group) != limits:
            raise RuntimeError('Container resource limits changed during measurement')
        emit({'kind': 'summary', 'sampled_peak_bytes': maximum,
              'elapsed_seconds': (time.monotonic_ns() - started_ns) / 1e9,
              'navigation_verified': False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('container')
    parser.add_argument('--seconds', type=float, default=120)
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--host-memory', action='store_true',
                        help='Also record host memory headroom, swap, OOM and pressure')
    parser.add_argument('--cpu-pressure', action='store_true',
                        help='Also record container and host CPU scheduling pressure')
    args = parser.parse_args()
    if not 0 < args.interval <= args.seconds <= 3600:
        parser.error('Require 0 < interval <= seconds <= 3600')
    collect(args.container, args.output, seconds=args.seconds, interval=args.interval,
            include_host_memory=args.host_memory, include_cpu_pressure=args.cpu_pressure)
    print(args.output)


if __name__ == '__main__':
    main()
