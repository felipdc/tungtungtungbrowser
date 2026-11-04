#!/usr/bin/env python3
"""Replay metal2vulkan memory logs without contacting a runtime or freeing memory.

The pinned unmodified logger omits pooled placements smaller than 4 MiB.
Unaccounted pool bytes therefore are an upper bound, not reclaimable memory.
Even full placement logging does not measure residency or prove capture completeness.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

PREFIX = 'metal2vulkan: memory: '
SPARSE_MINIMUM = 4 << 20
FULL_MARKER = 'placement coverage: minimum 1 bytes; protocol 1'
BLOCK = re.compile(
    r'(.+), memory (0x[0-9a-fA-F]+) of type (\d+): (\d+) bytes, '
    r'device address (?:unknown|0x[0-9a-fA-F]+ to 0x[0-9a-fA-F]+)$')
PLACEMENT = re.compile(
    r'(placed a (Shared|WriteCombined|Private|Managed|Memoryless) (buffer|image)|'
    r'returned an? (buffer|image)) of (\d+) bytes at offset (0x[0-9a-fA-F]+) '
    r'of memory (0x[0-9a-fA-F]+) of type (\d+)$')
CREATED = {
    'made a pooled block of buffers': 'pooled_buffers',
    'made a pooled block of images': 'pooled_images',
    'made a dedicated block of a buffer': 'dedicated_buffer',
    'made a dedicated block of an image': 'dedicated_image',
    'imported fresh pages for a buffer': 'imported_fresh_pages',
}
IMMEDIATE = 'made a pooled block and freed it at once, its pool having room'
RETIRED = 'a block goes, freed once the batches in flight complete'
TEARDOWN = 'freed a block as the device is destroyed'


def unsigned(text, *, base=10, bits=64, positive=False):
    value = int(text, base)
    if not int(positive) <= value < 1 << bits:
        raise ValueError('Integer outside the logged native range')
    return value


def pooled(block):
    return block['category'].startswith('pooled_')


def summarize(lines):
    """Validate and replay one process's ordered log prefix, retaining its tail state."""
    blocks = {}
    counts = Counter()
    minimum = SPARSE_MINIMUM
    marker_seen = False
    memory_events = 0
    retired_bytes = teardown_placements = 0
    pool_bytes = placement_bytes = peak_pool = peak_placement = 0
    last_event_line = 0
    for number, line in enumerate(lines, 1):
        if PREFIX not in line:
            continue
        try:
            if not line.endswith('\n'):
                raise ValueError('Incomplete final memory-log line')
            event = line.split(PREFIX, 1)[1].rstrip('\r\n')
            if event == FULL_MARKER:
                if memory_events and not marker_seen:
                    raise ValueError('Coverage declaration appeared after memory events')
                marker_seen = True
                minimum = 1
                counts['coverage_markers'] += 1
                continue
            memory_events += 1
            last_event_line = number
            placement = PLACEMENT.fullmatch(event)
            block_event = BLOCK.fullmatch(event)
            if placement:
                verb, storage, placed_kind, returned_kind, size, offset, handle, memory_type = placement.groups()
                size = unsigned(size, positive=True)
                offset = unsigned(offset, base=16)
                handle = unsigned(handle, base=16, positive=True)
                memory_type = unsigned(memory_type, bits=32)
                block = blocks.get(handle)
                kind = placed_kind or returned_kind
                if block is None or not pooled(block):
                    raise ValueError('Placement names an unknown or non-pooled block')
                if block['memory_type'] != memory_type or block['category'] != 'pooled_' + kind + 's':
                    raise ValueError('Placement type or resource kind differs from its block')
                if size < minimum or offset + size > block['bytes']:
                    raise ValueError('Placement falls outside its block or declared coverage')
                live = block['placements']
                if verb.startswith('placed'):
                    if any(offset < at + value['bytes'] and at < offset + size
                           for at, value in live.items()):
                        raise ValueError('Overlapping live placements')
                    live[offset] = {'bytes': size, 'storage': storage}
                    placement_bytes += size
                    counts['placements'] += 1
                else:
                    if offset not in live or live[offset]['bytes'] != size:
                        raise ValueError('Returned placement has no matching live allocation')
                    del live[offset]
                    placement_bytes -= size
                    counts['returns'] += 1
            elif block_event:
                verb, handle, memory_type, size = block_event.groups()
                handle = unsigned(handle, base=16, positive=True)
                memory_type = unsigned(memory_type, bits=32)
                size = unsigned(size, positive=True)
                category = CREATED.get(verb)
                if re.fullmatch(r'imported host memory 0x[0-9a-fA-F]+', verb):
                    category = 'imported_host_memory'
                if category or verb == IMMEDIATE:
                    if handle in blocks:
                        raise ValueError('Memory handle reused before its prior lifetime ended')
                    if verb == IMMEDIATE:
                        counts['immediate_allocate_free'] += 1
                    else:
                        block = {'handle': hex(handle), 'category': category,
                                 'memory_type': memory_type, 'bytes': size,
                                 'created_line': number, 'placements': {}}
                        blocks[handle] = block
                        if pooled(block):
                            pool_bytes += size
                        counts['block_creations'] += 1
                elif verb in {'freed a block', RETIRED, TEARDOWN}:
                    block = blocks.get(handle)
                    if block is None:
                        raise ValueError('End of lifetime names an unknown memory handle')
                    if block['memory_type'] != memory_type or block['bytes'] != size:
                        raise ValueError('End of lifetime differs from the allocation metadata')
                    if block['placements'] and verb != TEARDOWN:
                        raise ValueError('Block released with logged live placements')
                    if pooled(block):
                        pool_bytes -= size
                    if verb == TEARDOWN:
                        teardown_placements += len(block['placements'])
                        placement_bytes -= sum(v['bytes'] for v in block['placements'].values())
                    if verb == RETIRED:
                        retired_bytes += size
                        counts['retirement_notifications'] += 1
                    else:
                        counts['block_frees'] += 1
                    del blocks[handle]
                else:
                    raise ValueError('Unsupported memory lifecycle event')
            else:
                raise ValueError('Unrecognized or incomplete memory event')
            peak_pool = max(peak_pool, pool_bytes)
            peak_placement = max(peak_placement, placement_bytes)
        except ValueError as exc:
            # Do not echo a captured line: surrounding logs can contain private data.
            raise ValueError(f'Memory-log line {number}: {exc}') from exc
    if not memory_events:
        raise ValueError('No memory events found')

    rows = []
    for block in blocks.values():
        if not pooled(block):
            continue
        end = largest_gap = 0
        placements = []
        for offset, value in sorted(block['placements'].items()):
            largest_gap = max(largest_gap, offset - end)
            end = offset + value['bytes']
            placements.append({'offset': offset, **value})
        largest_gap = max(largest_gap, block['bytes'] - end)
        live_bytes = sum(v['bytes'] for v in placements)
        rows.append({k: v for k, v in block.items() if k != 'placements'} | {
            'placements': placements, 'logged_live_placement_bytes': live_bytes,
            'unaccounted_bytes': block['bytes'] - live_bytes,
            'largest_unaccounted_range_bytes': largest_gap,
        })
    categories = {}
    for block in blocks.values():
        group = categories.setdefault(block['category'], {'blocks': 0, 'bytes': 0})
        group['blocks'] += 1
        group['bytes'] += block['bytes']
    return {
        'schema': 1, 'status': 'consistent_logged_prefix',
        'memory_events': memory_events, 'last_event_line': last_event_line,
        'coverage': {'minimum_pooled_placement_bytes': minimum,
                     'declared_by_log': marker_seen, 'capture_completeness_verified': False},
        'events': dict(counts), 'live_categories': categories,
        'pooled': {'blocks': len(rows), 'bytes': pool_bytes,
                   'logged_live_placement_bytes': placement_bytes,
                   'unaccounted_bytes': pool_bytes - placement_bytes,
                   'blocks_without_logged_placements': sum(not row['placements'] for row in rows),
                   'rows': sorted(rows, key=lambda row: (-row['bytes'], row['handle']))},
        'peaks': {'pooled_bytes': peak_pool, 'logged_live_placement_bytes': peak_placement},
        'retired_bytes_total': retired_bytes,
        'placements_discarded_at_device_teardown': teardown_placements,
        'reclaimable_bytes': None,
        'notes': [
            'This is one process log prefix, not a resident-memory or whole-container measurement.',
            'Sparse unaccounted ranges can contain unlogged live placements; they are not free ranges.',
            'Full placement logging still needs a complete ordered capture and matching source/build evidence.',
            'Retirement notifications do not record when the driver actually releases those bytes.',
            'Peaks may occur at different events and must not be added.',
            'No Vulkan memory, native device state or browser content is modified by this analyzer.',
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    digest = hashlib.sha256()

    def lines():
        with args.log.open('rb') as source:
            for raw in source:
                digest.update(raw)
                yield raw.decode('utf-8', errors='replace')

    report = summarize(lines())
    report['log_sha256'] = digest.hexdigest()
    payload = json.dumps(report, indent=2, allow_nan=False) + '\n'
    with tempfile.NamedTemporaryFile(mode='w', dir=args.output.parent,
                                     prefix=args.output.name + '.', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(payload)
            stream.close()
            os.link(temporary, args.output)
        finally:
            temporary.unlink(missing_ok=True)
    print(json.dumps({'status': report['status'], 'output': str(args.output),
                      'pooled_bytes': report['pooled']['bytes'],
                      'unaccounted_bytes': report['pooled']['unaccounted_bytes'],
                      'reclaimable_bytes': None}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
