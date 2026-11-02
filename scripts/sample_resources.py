#!/usr/bin/env python3
"""Sample a running container's entire cgroup, including renderer subprocesses.

Run on the Linux Docker host. Memory is cgroup usage, not summed process RSS
(which counts shared guest RAM more than once). CPU cores used is CPU seconds
divided by elapsed wall seconds. No guest or page code is executed.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time


def counters(path):
    return {key: int(value) for key, value in
            (line.split() for line in path.read_text().splitlines())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('container')
    parser.add_argument('--seconds', type=float, default=120)
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if not 0 < args.interval <= args.seconds <= 3600:
        parser.error('Require 0 < interval <= seconds <= 3600')
    inspect = json.loads(subprocess.check_output(
        ['docker', 'inspect', '--type', 'container', args.container], text=True))[0]
    if not inspect['State']['Running']:
        parser.error('Container is not running')
    pid = inspect['State']['Pid']
    proc = Path(f'/proc/{pid}')
    start_ticks = proc.joinpath('stat').read_text().rsplit(')', 1)[1].split()[19]
    cgroup = next((line[3:] for line in proc.joinpath('cgroup').read_text().splitlines()
                   if line.startswith('0::')), None)
    if cgroup is None:
        parser.error('A cgroup v2 Docker host is required')
    group = Path('/sys/fs/cgroup').joinpath(cgroup.lstrip('/'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive output prevents accidental overwriting of a previous measurement.
    with args.output.open('x') as output:
        def emit(value):
            output.write(json.dumps(value) + '\n')
            output.flush()

        emit({'kind': 'configuration', 'container_id': inspect['Id'],
              'image': inspect['Image'], 'started_at': inspect['State']['StartedAt'],
              'memory_limit_bytes': inspect['HostConfig']['Memory'],
              'cpu_quota_nanocpus': inspect['HostConfig']['NanoCpus'],
              'cgroup': cgroup, 'requested_seconds': args.seconds,
              'interval_seconds': args.interval})
        started = time.monotonic()
        previous = None
        maximum = 0
        while True:
            if proc.joinpath('stat').read_text().rsplit(')', 1)[1].split()[19] != start_ticks:
                raise RuntimeError('Container process changed during measurement')
            now = time.monotonic()
            memory = int(group.joinpath('memory.current').read_text())
            cpu = counters(group / 'cpu.stat')
            sample = {'kind': 'sample', 'at': datetime.now(timezone.utc).isoformat(),
                      'elapsed_seconds': now - started, 'memory_bytes': memory,
                      'memory_stat': counters(group / 'memory.stat'), 'cpu_stat': cpu,
                      'memory_events': counters(group / 'memory.events')}
            peak = group / 'memory.peak'
            if peak.exists():
                sample['peak_bytes_since_container_created'] = int(peak.read_text())
            if previous:
                sample['cpu_cores_used'] = ((cpu['usage_usec'] - previous[1]) / 1e6 /
                                            (now - previous[0]))
            emit(sample)
            maximum = max(maximum, memory)
            previous = now, cpu['usage_usec']
            remaining = args.seconds - (time.monotonic() - started)
            if remaining <= 0:
                break
            time.sleep(min(args.interval, remaining))
        emit({'kind': 'summary', 'sampled_peak_bytes': maximum,
              'elapsed_seconds': time.monotonic() - started,
              'navigation_verified': False})
    print(args.output)


if __name__ == '__main__':
    main()
