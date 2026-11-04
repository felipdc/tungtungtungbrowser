#!/usr/bin/env python3
"""Check one saved Safari probe tap against an explicitly calibrated contact.

This reads a serve_probe.py input callback; it sends no device or page commands.
A pass covers the recorded tap only, not physical-device equivalence.
"""
import argparse
import json
import math
from pathlib import Path
import sys


def finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def json_number(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Input contains a nonfinite number')
    return number


def check_probe(document, *, radius, force=0, pressure=0, require_property_reads=False):
    if not finite(radius) or radius <= 0:
        raise ValueError('radius must be a finite positive number in CSS pixels')
    if any(not finite(v) or not 0 <= v <= 1 for v in (force, pressure)):
        raise ValueError('expected force and pressure must be between 0 and 1')
    report = {'status': 'failed', 'expected': {'radius': radius, 'force': force,
              'pressure': pressure}, 'observed': {}, 'property_read_paths_verified': False,
              'errors': [], 'scope': 'One calibrated Safari tap; no device-equivalence claim'}
    errors = report['errors']

    def matches(label, actual, expected):
        if not finite(actual) or not math.isclose(actual, expected, rel_tol=0, abs_tol=0.0001):
            errors.append(f'{label} does not match {expected}')

    observation = document.get('observation') if isinstance(document, dict) else None
    if not isinstance(observation, dict) or not isinstance(observation.get('events'), list):
        errors.append('Expected a saved probe input callback with observation.events')
        return report
    events = observation['events']
    if any(isinstance(e, dict) and e.get('type') in ('touchcancel', 'pointercancel') for e in events):
        errors.append('Contact was cancelled')
    if observation.get('type') != 'click' or observation.get('isTrusted') is not True:
        errors.append('Callback is not a trusted click')
    kinds = ('pointerdown', 'touchstart', 'pointerup', 'touchend', 'click')
    selected = {}
    positions = []
    for kind in kinds:
        found = [(i, e) for i, e in enumerate(events) if isinstance(e, dict) and e.get('type') == kind]
        if len(found) != 1:
            errors.append(f'Expected exactly one {kind}, found {len(found)}')
            continue
        index, event = found[0]
        selected[kind] = event
        positions.append(index)
        if event.get('isTrusted') is not True:
            errors.append(f'{kind} is not trusted')
    if len(selected) != len(kinds):
        return report
    if positions != sorted(positions):
        errors.append('Pointer, touch and click events are out of order')

    for kind in ('pointerdown', 'pointerup'):
        event = selected[kind]
        report['observed'][kind] = {k: event.get(k) for k in ('width', 'height', 'pressure')}
        if event.get('pointerType') != 'touch':
            errors.append(f'{kind} does not describe a touch pointer')
        for key in ('width', 'height'):
            matches(f'{kind}.{key}', event.get(key), 2 * radius if kind == 'pointerdown' else 0)
        matches(f'{kind}.pressure', event.get('pressure'), pressure if kind == 'pointerdown' else 0)
    down, up = selected['pointerdown'], selected['pointerup']
    if not finite(down.get('timeStamp')) or not finite(up.get('timeStamp')) or up['timeStamp'] < down['timeStamp']:
        errors.append('Invalid or reversed contact timestamps')
    else:
        report['observed']['contact_milliseconds'] = up['timeStamp'] - down['timeStamp']
    identifier = down.get('pointerId')
    if type(identifier) is not int or type(up.get('pointerId')) is not int or up['pointerId'] != identifier:
        errors.append('Pointer contact identifiers do not match')

    readers_verified = 0
    for kind in ('touchstart', 'touchend'):
        changed = selected[kind].get('changedTouches')
        if not isinstance(changed, list) or len(changed) != 1 or not isinstance(changed[0], dict):
            errors.append(f'{kind} must contain one changed touch')
            continue
        touch = changed[0]
        fields = ('radiusX', 'radiusY', 'force', 'rotationAngle')
        report['observed'][kind] = {key: touch.get(key) for key in fields}
        if type(touch.get('identifier')) is not int or touch['identifier'] != identifier:
            errors.append(f'{kind} contact identifier does not match the pointer')
        for key in ('radiusX', 'radiusY'):
            matches(f'{kind}.{key}', touch.get(key), radius)
        matches(f'{kind}.force', touch.get('force'), force if kind == 'touchstart' else 0)
        if not finite(touch.get('rotationAngle')):
            errors.append(f'{kind}.rotationAngle is not finite')
        reads = touch.get('propertyReads')
        if reads is None and not require_property_reads:
            continue
        try:
            values = [reads[key]['values'] for key in ('direct', 'indexedForward', 'indexedReverse')]
            values.append({key: reads['prototypeGetters'][key]['values'] for key in fields})
            for reader in values:
                for key in fields:
                    if not finite(reader[key]) or reader[key] != touch.get(key):
                        raise ValueError('Field mismatch')
        except (TypeError, KeyError, ValueError):
            errors.append(f'{kind} property readers are missing, failed or inconsistent')
        else:
            readers_verified += 1
    report['property_read_paths_verified'] = readers_verified == 2
    if not errors:
        report['status'] = 'passed'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='input-*.json saved by serve_probe.py')
    parser.add_argument('--radius', type=float, required=True, help='Expected CSS radius on both axes')
    parser.add_argument('--force', type=float, default=0, help='Expected touch-start force (default: 0)')
    parser.add_argument('--pressure', type=float, default=0, help='Expected pointer-down pressure (default: 0)')
    parser.add_argument('--require-property-reads', action='store_true')
    args = parser.parse_args(argv)
    try:
        with args.input.open('rb') as stream:
            raw = stream.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError('Input exceeds 4 MiB')
        document = json.loads(raw, parse_constant=json_number, parse_float=json_number)
        result = check_probe(document, radius=args.radius, force=args.force,
                             pressure=args.pressure, require_property_reads=args.require_property_reads)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
