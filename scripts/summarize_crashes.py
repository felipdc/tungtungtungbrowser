#!/usr/bin/env python3
"""Attribute saved iOS JSON crash reports to an explicit guest boot UUID.

Offline only. File modification times, incident identifiers and PIDs do not
establish boot identity. This tool reports evidence, not browser readiness.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import uuid

MAX_REPORT_BYTES = 8 * 1024 * 1024
UUID_PATTERN = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'


def boot_uuid(value):
    if not isinstance(value, str) or not re.fullmatch(UUID_PATTERN, value):
        raise ValueError('Boot UUID must use the canonical hyphenated format')
    return str(uuid.UUID(value)).upper()


class ReportObject(dict):
    def __init__(self, pairs):
        super().__init__()
        self.duplicate_keys = []
        self.duplicate_total = 0
        for key, value in pairs:
            if key in self:
                self.duplicate_keys.append(key)
                self.duplicate_total += 1
            self.duplicate_total += duplicate_count(value)
            self[key] = value


def duplicate_count(value):
    pending = [value]
    count = 0
    while pending:
        item = pending.pop()
        if isinstance(item, ReportObject):
            count += item.duplicate_total
        elif isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return count


def reject_constant(value):
    raise ValueError(f'Nonfinite JSON value: {value}')


def finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        reject_constant(value)
    return result


def parse_report(raw):
    """Accept a JSON body, or the usual metadata object followed by its body."""
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError('Crash report exceeds the 8 MiB input limit')
    source = raw.decode('utf-8')
    decoder = json.JSONDecoder(object_pairs_hook=ReportObject,
                               parse_constant=reject_constant, parse_float=finite_float)
    records = []
    position = 0
    while position < len(source):
        while position < len(source) and source[position].isspace():
            position += 1
        if position == len(source):
            break
        value, position = decoder.raw_decode(source, position)
        if not isinstance(value, dict):
            raise ValueError('Crash report records must be JSON objects')
        if value.duplicate_keys:
            raise ValueError('Duplicate key in top-level crash record')
        records.append(value)
        if len(records) > 2:
            raise ValueError('Expected one body or metadata followed by one body')
    if not records:
        raise ValueError('Empty crash report')
    if len(records) == 2 and not {'bug_type', 'app_name'} & records[0].keys():
        raise ValueError('First record is not recognized crash metadata')
    return records[-1]


def summarize(raw, expected_boot_uuid):
    expected = boot_uuid(expected_boot_uuid)
    body = parse_report(raw)
    identities = []
    if 'bootSessionUUID' in body:
        identities.append(boot_uuid(body['bootSessionUUID']))
    panic = body.get('panicString')
    if isinstance(panic, str):
        for value in re.findall(r'^Boot session UUID:\s*([^\r\n]+)', panic, re.MULTILINE):
            identities.append(boot_uuid(value.strip()))
    distinct = set(identities)
    if not distinct:
        attribution = 'unknown_boot'
    elif len(distinct) > 1:
        attribution = 'conflicting_boot_identity'
    else:
        attribution = 'matching_boot' if expected in distinct else 'different_boot'

    termination = body.get('termination', {})
    if not isinstance(termination, dict):
        raise ValueError('Termination must be an object when present')
    # Some native panic logs repeat IDs inside their large task/thread maps.
    # They are not used here. Ambiguity in fields we actually return is an error.
    selected = {key: body.get(key) for key in
                ('procName', 'pid', 'captureTime', 'incident', 'termination')}
    if duplicate_count(selected):
        raise ValueError('Duplicate key in selected crash details')
    reasons = termination.get('reasons', [])
    if not isinstance(reasons, list) or not all(isinstance(x, str) for x in reasons):
        raise ValueError('Termination reasons must be a list of strings')
    code = termination.get('code')
    if isinstance(code, str) and re.fullmatch(r'(?:0[xX][0-9a-fA-F]+|[0-9]+)', code):
        code = int(code, 16 if code.lower().startswith('0x') else 10)
    is_watchdog = type(code) is int and code == 0x8BADF00D
    event = next((x.partition(':')[2].strip() for x in reasons
                  if x.startswith('WatchdogEvent:')), None)
    allowance = re.search(r'time allowance of ([0-9]+(?:\.[0-9]+)?) seconds',
                          '\n'.join(reasons))
    seconds = float(allowance[1]) if is_watchdog and allowance else None
    if seconds is not None and not math.isfinite(seconds):
        raise ValueError('Watchdog allowance must be finite')
    return {
        'sha256': hashlib.sha256(raw).hexdigest(),
        'bytes': len(raw),
        'attribution': attribution,
        'reported_boot_uuids': sorted(distinct),
        'duplicate_keys_in_unselected_objects': duplicate_count(body),
        'process': body.get('procName'),
        'pid': body.get('pid'),
        'capture_time': body.get('captureTime'),
        'incident': body.get('incident'),
        'watchdog_termination': is_watchdog,
        'watchdog_event': event if is_watchdog else None,
        'wall_clock_allowance_seconds': seconds,
        'termination': termination,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', type=Path, nargs='+')
    parser.add_argument('--boot-uuid', type=boot_uuid, required=True,
                        help='Guest boot UUID, not the host boot ID or a crash incident ID')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = {'expected_boot_uuid': args.boot_uuid, 'reports': [], 'errors': [],
              'browser_readiness_verified': False,
              'scope': 'Attribution of supplied files; absence of a report does not prove absence of a crash'}
    for path in args.reports:
        row = {'path': str(path)}
        try:
            with path.open('rb') as source:
                raw = source.read(MAX_REPORT_BYTES + 1)
            row.update(summarize(raw, args.boot_uuid))
            result['reports'].append(row)
        except (OSError, ValueError, RecursionError) as exc:
            result['errors'].append({'path': str(path), 'error': str(exc)})
    result['counts'] = {key: sum(row['attribution'] == key for row in result['reports'])
                        for key in ('matching_boot', 'different_boot', 'unknown_boot',
                                    'conflicting_boot_identity')}
    result['inspection_complete'] = not result['errors']
    try:
        with args.output.open('x') as output:
            json.dump(result, output, indent=2, allow_nan=False)
            output.write('\n')
    except OSError as exc:
        print(f'Cannot create report: {exc}', file=sys.stderr)
        return 1
    return 0 if result['inspection_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
