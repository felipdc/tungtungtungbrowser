#!/usr/bin/env python3
"""Summarize one process's optional display trace; never assert visual readiness."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import statistics


EVENT = re.compile(r'\b(apple_gfx_frame_\S+)\s+(.*)$')
INTEGER = re.compile(r'\d+')
POINTER = re.compile(r'0x[0-9a-fA-F]+')
FIELDS = {
    'state': {'ns', 'stage', 'display', 'job', 'dirty', 'rendering'},
    'job': {'ns', 'display', 'job', 'target', 'width', 'height'},
    'stage': {'ns', 'stage', 'display', 'job', 'status'},
    'copy': {'ns', 'display', 'job', 'encoded', 'same_size', 'same_target'},
}
STAGES = [f'{stage}-{edge}' for stage in ('encode', 'commit', 'wait', 'read')
          for edge in ('begin', 'end')]


def parse(line, number):
    match = EVENT.search(line.rstrip('\n'))
    if match is None:
        return None
    kind = match[1].removeprefix('apple_gfx_frame_')
    tokens = match[2].split()
    if any(token.count('=') != 1 for token in tokens):
        raise ValueError(f'Line {number}: malformed display trace fields')
    fields = dict(token.split('=', 1) for token in tokens)
    if kind not in FIELDS or len(tokens) != len(fields) or fields.keys() != FIELDS[kind]:
        raise ValueError(f'Line {number}: unexpected or duplicate display trace fields')
    row = {'event': kind, 'line': number}
    for key, value in fields.items():
        if key in ('display', 'target'):
            if POINTER.fullmatch(value) is None:
                raise ValueError(f'Line {number}: invalid {key}')
            row[key] = value.lower()
        elif key == 'stage':
            choices = STAGES if kind == 'stage' else (
                'request', 'present', 'completion', 'request-no-render')
            if value not in choices:
                raise ValueError(f'Line {number}: unknown stage')
            row[key] = value
        else:
            pattern = r'-?\d+' if key == 'status' else INTEGER.pattern
            if re.fullmatch(pattern, value) is None:
                raise ValueError(f'Line {number}: invalid {key}')
            row[key] = int(value)
            lower, upper = (-2**31, 2**31-1) if key == 'status' else (
                0, 2**63-1 if key == 'ns' else 2**64-1 if key == 'job' else 2**32-1)
            if not lower <= row[key] <= upper:
                raise ValueError(f'Line {number}: out-of-range {key}')
            if key in ('dirty', 'rendering', 'encoded', 'same_size', 'same_target') and row[key] not in (0, 1):
                raise ValueError(f'Line {number}: invalid boolean {key}')
    return row


def distribution(values):
    if not values:
        return {'count': 0}
    return {'count': len(values), 'minimum': min(values),
            'median': statistics.median(values), 'maximum': max(values)}


def analyze(lines):
    events = []
    ignored = 0
    for number, line in enumerate(lines, 1):
        row = parse(line, number)
        if row is None:
            ignored += 1
        else:
            events.append(row)
    if not events:
        raise ValueError('No display trace events found')
    groups = defaultdict(list)
    for row in events:
        if row['event'] != 'state':
            groups[(row['display'], row['job'])].append(row)
    jobs = []
    timings = defaultdict(list)
    for (display, job), rows in sorted(groups.items()):
        ordered = sorted(rows, key=lambda r: (r['ns'], r['line']))
        labels = defaultdict(list)
        for row in ordered:
            labels[row.get('stage', row['event'])].append(row)
        issues = []
        durations = {}

        def duration(name, begin, end):
            if len(labels[begin]) == len(labels[end]) == 1:
                value = labels[end][0]['ns'] - labels[begin][0]['ns']
                if value < 0:
                    issues.append(f'negative duration: {name}')
                else:
                    durations[name] = value / 1e9
                    timings[name].append(value / 1e9)

        for stage in ('encode', 'commit', 'wait', 'read'):
            duration(stage, stage + '-begin', stage + '-end')
        duration('queue', 'job', 'encode-begin')
        duration('read_end_to_copy', 'read-end', 'copy')
        duration('job_to_copy', 'job', 'copy')
        expected = ['job', *STAGES, 'copy']
        if len(labels['encode-end']) == 1 and labels['encode-end'][0]['status'] != 0:
            expected = ['job', 'encode-begin', 'encode-end', 'copy']
        actual = [r.get('stage', r['event']) for r in ordered]
        if actual != expected:
            issues.append('partial, duplicated or out-of-order job lifecycle')
        jobs.append({'display': display, 'job': job, 'fully_observed': not issues,
                     'issues': issues, 'durations_seconds': durations,
                     'events': ordered})
    errors = [r for r in events if r['event'] == 'stage'
              and r['stage'].endswith('-end') and r['status'] != 0]
    copies = [r for r in events if r['event'] == 'copy']
    return {
        'scope': 'One QEMU process and host clock; job numbers and pointers are not portable identities',
        'visual_readiness_established': False,
        'performance_comparison_established': False,
        'event_count': len(events), 'ignored_lines': ignored,
        'events_by_kind': dict(Counter(r['event'] for r in events)),
        'states': dict(Counter(r['stage'] for r in events if r['event'] == 'state')),
        'first_ns': min(r['ns'] for r in events), 'last_ns': max(r['ns'] for r in events),
        'file_timestamp_inversions': sum(b['ns'] < a['ns'] for a, b in zip(events, events[1:])),
        'jobs_observed': len(jobs),
        'fully_observed_jobs': sum(j['fully_observed'] for j in jobs),
        'partial_or_inconsistent_jobs': sum(not j['fully_observed'] for j in jobs),
        'nonzero_stage_results': errors,
        'copies_with_false_flags': [r for r in copies if not all(r[k] for k in ('encoded', 'same_size', 'same_target'))],
        'duration_seconds': {k: distribution(v) for k, v in timings.items()},
        'jobs': jobs,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path)
    parser.add_argument('--output', type=Path, required=True, help='New external JSON file')
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        parser.error('Output already exists; preserve the original report')
    try:
        with args.trace.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        with args.trace.open(encoding='utf-8') as stream:
            result = analyze(stream)
        with args.trace.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise ValueError('Trace changed during analysis; use a stable copy')
        result['trace_sha256'] = digest
        with args.output.open('x') as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write('\n')
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps({k: v for k, v in result.items() if k != 'jobs'}, indent=2))


if __name__ == '__main__':
    main()
