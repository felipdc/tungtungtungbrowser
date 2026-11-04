#!/usr/bin/env python3
"""Summarize sample_resources.py JSONL without contacting Docker or the guest.

Memory is whole-cgroup usage. Component peaks need not occur together and must
not be added. A missing terminal summary leaves measurement_complete false.
"""
import argparse
import json
import math
from pathlib import Path
import sys


def number(value, name, *, integer=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0
            or (integer and not isinstance(value, int))):
        raise ValueError(f'{name} must be a nonnegative finite {"integer" if integer else "number"}')
    return value


def validate_host_memory(host, prior):
    if not isinstance(host, dict):
        raise ValueError('Missing requested host memory sample')
    memory = host.get('memory_bytes')
    if not isinstance(memory, dict) or memory.keys() != {
            'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'}:
        raise ValueError('Host memory needs total, available and swap counters')
    for key, value in memory.items():
        number(value, f'host_memory.{key}', integer=True)
    number(host.get('oom_kills'), 'host_memory.oom_kills', integer=True)
    if memory['MemAvailable'] > memory['MemTotal'] or memory['SwapFree'] > memory['SwapTotal']:
        raise ValueError('Host available memory or swap exceeds total')
    pressure = host.get('pressure_usec')
    if pressure is not None:
        if not isinstance(pressure, dict) or pressure.keys() != {'some', 'full'}:
            raise ValueError('Host pressure needs some and full counters')
        for key, value in pressure.items():
            number(value, f'host_memory.pressure_usec.{key}', integer=True)
    if prior is not None:
        if memory['MemTotal'] != prior['memory_bytes']['MemTotal']:
            raise ValueError('Host total memory changed during measurement')
        if host['oom_kills'] < prior['oom_kills']:
            raise ValueError('Host OOM counter decreased; cannot combine runs')
        before = prior.get('pressure_usec')
        if (pressure is None) != (before is None):
            raise ValueError('Host memory pressure availability changed during measurement')
        if pressure is not None and any(value < before[key] for key, value in pressure.items()):
            raise ValueError('Host pressure counter decreased; cannot combine runs')


def validate_cpu_pressure(pressure, prior):
    if not isinstance(pressure, dict) or pressure.keys() != {'container', 'host'}:
        raise ValueError('CPU pressure needs container and host observations')
    for scope, required in [('container', {'some', 'full'}), ('host', {'some'})]:
        value = pressure[scope]
        if value is not None:
            if not isinstance(value, dict) or value.keys() != required:
                raise ValueError(f'Invalid {scope} CPU pressure counters')
            for key, counter in value.items():
                number(counter, f'{scope} CPU pressure {key}', integer=True)
        if prior is not None:
            before = prior[scope]
            if (value is None) != (before is None):
                raise ValueError(f'{scope} CPU pressure availability changed')
            if value is not None and any(counter < before[key] for key, counter in value.items()):
                raise ValueError(f'{scope} CPU pressure counter decreased; cannot combine runs')


def summarize(records):
    configuration = first = previous = terminal = None
    count = 0
    sampled_peak = container_peak = maximum_gap = 0
    component_peaks = {}
    minimum_host_available = None
    maximum_host_swap_used = 0
    # These are separate memory.stat counters, not independent allocations.
    components = ('anon', 'file', 'shmem', 'kernel', 'pagetables', 'slab')
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('Every record must be a JSON object')
        kind = record.get('kind')
        if terminal is not None:
            raise ValueError('Unexpected record after the terminal summary')
        if kind == 'configuration':
            if configuration is not None or count:
                raise ValueError('Configuration must appear once, before samples')
            configuration = record
            if not isinstance(record.get('include_host_memory', False), bool):
                raise ValueError('include_host_memory must be a boolean')
            if not isinstance(record.get('include_cpu_pressure', False), bool):
                raise ValueError('include_cpu_pressure must be a boolean')
        elif kind == 'sample':
            if configuration is None:
                raise ValueError('Missing configuration before samples')
            if configuration.get('include_host_memory', False):
                host = record.get('host_memory')
                validate_host_memory(host, previous.get('host_memory') if previous else None)
                available = host['memory_bytes']['MemAvailable']
                minimum_host_available = (available if minimum_host_available is None else
                                          min(minimum_host_available, available))
                maximum_host_swap_used = max(maximum_host_swap_used,
                    host['memory_bytes']['SwapTotal'] - host['memory_bytes']['SwapFree'])
            elif 'host_memory' in record:
                raise ValueError('Host memory sample was not declared in configuration')
            if configuration.get('include_cpu_pressure', False):
                validate_cpu_pressure(record.get('cpu_pressure_usec'),
                                      previous['cpu_pressure_usec'] if previous else None)
            elif 'cpu_pressure_usec' in record:
                raise ValueError('CPU pressure sample was not declared in configuration')
            elapsed = number(record.get('elapsed_seconds'), 'elapsed_seconds')
            memory = number(record.get('memory_bytes'), 'memory_bytes', integer=True)
            cpu = record.get('cpu_stat')
            events = record.get('memory_events')
            if not isinstance(cpu, dict) or not isinstance(events, dict):
                raise ValueError('Sample needs cpu_stat and memory_events counters')
            number(cpu.get('usage_usec'), 'cpu_stat.usage_usec', integer=True)
            for group in (cpu, events):
                for key, value in group.items():
                    number(value, key, integer=True)
            pressure = record.get('memory_pressure_usec')
            if pressure is not None:
                if not isinstance(pressure, dict) or pressure.keys() != {'some', 'full'}:
                    raise ValueError('Memory pressure needs some and full counters')
                for key, value in pressure.items():
                    number(value, f'memory_pressure_usec.{key}', integer=True)
            if previous is not None:
                gap = elapsed - previous['elapsed_seconds']
                if gap <= 0:
                    raise ValueError('Sample times must strictly increase')
                maximum_gap = max(maximum_gap, gap)
                for group in ('cpu_stat', 'memory_events'):
                    if record[group].keys() != previous[group].keys():
                        raise ValueError(f'{group} counters changed during measurement')
                    if any(value < previous[group][key] for key, value in record[group].items()):
                        raise ValueError(f'{group} counter decreased; cannot combine runs')
                prior_pressure = previous.get('memory_pressure_usec')
                if (pressure is None) != (prior_pressure is None):
                    raise ValueError('Memory pressure availability changed during measurement')
                if pressure is not None and any(
                        value < prior_pressure[key] for key, value in pressure.items()):
                    raise ValueError('Memory pressure counter decreased; cannot combine runs')
            stats = record.get('memory_stat', {})
            if not isinstance(stats, dict):
                raise ValueError('memory_stat must be an object')
            for key in components:
                if key in stats:
                    value = number(stats[key], f'memory_stat.{key}', integer=True)
                    component_peaks[key] = max(component_peaks.get(key, 0), value)
            if 'peak_bytes_since_container_created' in record:
                peak = number(record['peak_bytes_since_container_created'], 'container peak', integer=True)
                if peak < container_peak:
                    raise ValueError('Container peak decreased during measurement')
                container_peak = peak
            sampled_peak = max(sampled_peak, memory)
            first = first or record
            previous = record
            count += 1
        elif kind == 'summary':
            if not count:
                raise ValueError('Terminal summary has no samples')
            if record.get('sampled_peak_bytes') != sampled_peak:
                raise ValueError('Terminal peak does not match recorded samples')
            if number(record.get('elapsed_seconds'), 'summary elapsed_seconds') < previous['elapsed_seconds']:
                raise ValueError('Terminal time precedes the final sample')
            terminal = record
        else:
            raise ValueError(f'Unknown resource record kind: {kind!r}')
    if first is None:
        raise ValueError('No resource samples found')
    duration = previous['elapsed_seconds'] - first['elapsed_seconds']
    cpu_delta = {key: value - first['cpu_stat'][key]
                 for key, value in previous['cpu_stat'].items()}
    event_delta = {key: value - first['memory_events'][key]
                   for key, value in previous['memory_events'].items()}
    pressure_delta = None if first.get('memory_pressure_usec') is None else {
        key: value - first['memory_pressure_usec'][key]
        for key, value in previous['memory_pressure_usec'].items()}
    host_summary = {'recorded': False}
    cpu_pressure_summary = {'recorded': False}
    if configuration.get('include_cpu_pressure', False):
        cpu_pressure_summary = {'recorded': True}
        for scope in ('container', 'host'):
            before, after = first['cpu_pressure_usec'][scope], previous['cpu_pressure_usec'][scope]
            delta = None if before is None else {key: value - before[key] for key, value in after.items()}
            cpu_pressure_summary[scope] = {
                'available': delta is not None,
                'counter_deltas_usec': delta,
                'stall_percent': None if delta is None or not duration else {
                    key: value / 1e6 / duration * 100 for key, value in delta.items()},
            }
    if minimum_host_available is not None:
        first_host, last_host = first['host_memory'], previous['host_memory']
        host_pressure = None if first_host.get('pressure_usec') is None else {
            key: value - first_host['pressure_usec'][key]
            for key, value in last_host['pressure_usec'].items()}
        host_summary = {
            'recorded': True,
            'total_bytes': first_host['memory_bytes']['MemTotal'],
            'minimum_sampled_available_bytes': minimum_host_available,
            'maximum_sampled_swap_used_bytes': maximum_host_swap_used,
            'oom_kills_at_start': first_host['oom_kills'],
            'oom_kills_delta': last_host['oom_kills'] - first_host['oom_kills'],
            'pressure_counter_deltas_usec': host_pressure,
            'pressure_stall_percent': None if host_pressure is None or not duration else {
                key: value / 1e6 / duration * 100 for key, value in host_pressure.items()},
        }
    return {
        'measurement_complete': terminal is not None,
        'configuration': configuration,
        'samples': count,
        'observed_seconds': duration,
        'maximum_sample_gap_seconds': maximum_gap if count > 1 else None,
        'memory': {
            'first_bytes': first['memory_bytes'],
            'last_bytes': previous['memory_bytes'],
            'sampled_peak_bytes': sampled_peak,
            'peak_bytes_since_container_created': container_peak or None,
            'component_sampled_peaks_bytes': component_peaks,
        },
        'cpu': {
            'counter_deltas': cpu_delta,
            'average_cores_used': cpu_delta['usage_usec'] / 1e6 / duration if duration else None,
        },
        'memory_events': {'at_start': first['memory_events'], 'deltas': event_delta},
        'memory_pressure': {
            'available': pressure_delta is not None,
            'counter_deltas_usec': pressure_delta,
            'stall_percent': None if pressure_delta is None or not duration else {
                key: value / 1e6 / duration * 100 for key, value in pressure_delta.items()},
        },
        'host_memory': host_summary,
        'cpu_pressure': cpu_pressure_summary,
        'navigation_verified': False,
        'notes': [
            'Container lifetime peak may predate this observation window.',
            'Memory component peaks are not additive; file includes shmem.',
            'CPU and event deltas cover the first through last recorded sample.',
            'Memory PSI some/full stall times overlap and must not be added.',
            'Host memory, swap, OOM and pressure include all host workloads; '
            'sampled headroom can miss shorter dips.',
            'CPU pressure measures host scheduler stalls, not scheduling within the guest. '
            'Host CPU full is undefined and omitted; container some/full times overlap.',
        ] + ([] if terminal else ['No terminal summary: measurement ended incompletely.']),
    }


def read_records(stream):
    for line_number, line in enumerate(stream, 1):
        if line.strip():
            try:
                yield json.loads(line)
            except ValueError as exc:
                raise ValueError(f'Invalid JSON at line {line_number}') from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, help='Exclusive JSON output; defaults to stdout')
    args = parser.parse_args()
    try:
        with args.input.open() as source:
            result = summarize(read_records(source))
        encoded = json.dumps(result, indent=2, allow_nan=False) + '\n'
        if args.output:
            with args.output.open('x') as output:
                output.write(encoded)
        else:
            sys.stdout.write(encoded)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'{parser.prog}: {exc}\n')
    return 0 if result['measurement_complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
