#!/usr/bin/env python3
"""Map perf primary IP samples to QEMU guest TB names using timed JIT loads.

Run after QEMU has exited and flushed its jitdump, or use --live to require
a complete timestamped record beyond the final sample. Capture perf with
--clockid mono and export with perf script --ns -F pid,tid,time,ip.
Names identify a translated block's first guest instruction, not necessarily
the exact instruction sampled. Only buckets containing sample IPs are retained.
"""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import struct

BUCKET = 65536
SAMPLE = re.compile(r'^\s*(\d+)/(\d+)\s+(\d+)\.(\d{1,9}):\s*([0-9a-fA-F]+)?\s*$')
FRAME = re.compile(r'^\s+([0-9a-fA-F]+)(?:\s.*)?$')


def read_samples(text):
    samples = []
    pending = None
    for line in text.splitlines():
        match = SAMPLE.match(line)
        if match:
            if pending is not None:
                raise ValueError('Perf sample has no primary IP')
            pid, tid, seconds, fraction, ip = match.groups()
            pending = {'pid': int(pid), 'tid': int(tid),
                       'time_ns': int(seconds) * 10**9 + int(fraction.ljust(9, '0'))}
            if ip:
                samples.append(pending | {'ip': int(ip, 16)})
                pending = None
        elif pending is not None:
            frame = FRAME.match(line)
            if frame:
                samples.append(pending | {'ip': int(frame[1], 16)})
                pending = None
            elif line.strip():
                raise ValueError('Unrecognized primary IP line')
        elif line.strip() and not line.lstrip().startswith('#'):
            if not samples or not FRAME.match(line):
                raise ValueError('Unrecognized perf sample line')
    if pending is not None or not samples:
        raise ValueError('Incomplete or empty perf samples')
    return samples


def map_samples(jit_path, samples, *, live=False):
    if not samples or len({s['pid'] for s in samples}) != 1:
        raise ValueError('Select samples from exactly one host process')
    wanted = {s['ip'] // BUCKET for s in samples}
    last_sample = max(s['time_ns'] for s in samples)
    buckets = defaultdict(list)
    loads = 0
    records = 0
    last_timestamp = 0
    barrier = False
    with Path(jit_path).open('rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        header = stream.read(40)
        if len(header) != 40:
            raise ValueError('Truncated JIT header')
        magic, version, header_size, machine, _, pid, created, flags = struct.unpack('<IIIIIIQQ', header)
        if magic != 0x4A695444 or version != 1 or header_size != 40 or flags != 0:
            raise ValueError('Unsupported JIT header or clock')
        if min(s['time_ns'] for s in samples) < created:
            raise ValueError('Samples precede JIT creation; verify the producer and clock')
        while stream.tell() < size:
            offset = stream.tell()
            prefix = stream.read(16)
            if len(prefix) != 16:
                raise ValueError('Truncated JIT record prefix')
            kind, length, timestamp = struct.unpack('<IIQ', prefix)
            if length < 16 or offset + length > size or timestamp < last_timestamp:
                raise ValueError('Truncated, invalid or out-of-order JIT record')
            records += 1
            last_timestamp = timestamp
            # QEMU serializes records before executing their code. A complete
            # later record proves that all loads relevant to this sample window
            # have left the writer's buffer. Appended records are unnecessary.
            if live and timestamp > last_sample:
                barrier = True
                break
            if kind == 1 or kind not in (0, 2, 3, 4):
                raise ValueError('Unsupported JIT record; code moves are not modeled')
            if kind == 0:
                if length < 57:
                    raise ValueError('Invalid code-load record')
                record_pid, tid, vma, address, code_size, index = struct.unpack('<IIQQQQ', stream.read(40))
                name_size = length - 56 - code_size
                if record_pid != pid or not 1 <= name_size <= 1048576 or not 0 < code_size <= 2**24:
                    raise ValueError('Invalid JIT code dimensions or PID')
                name = stream.read(name_size)
                if not name.endswith(b'\0') or b'\0' in name[:-1]:
                    raise ValueError('Invalid JIT symbol')
                loads += 1
                if timestamp <= last_sample:
                    relevant = range(address // BUCKET, (address + code_size - 1) // BUCKET + 1)
                    for bucket in relevant:
                        if bucket in wanted:
                            buckets[bucket].append((timestamp, address, address + code_size,
                                                    name[:-1].decode('utf-8'), index))
            stream.seek(offset + length)
    if live and not barrier:
        raise ValueError('Live JIT file has no complete record beyond the final sample')
    times = {key: [load[0] for load in values] for key, values in buckets.items()}
    counts = Counter()
    by_thread = defaultdict(Counter)
    for sample in samples:
        key = sample['ip'] // BUCKET
        entries = buckets.get(key, [])
        pos = bisect_right(times.get(key, []), sample['time_ns']) - 1
        symbol = None
        while pos >= 0:
            timestamp, start, end, candidate, index = entries[pos]
            if start <= sample['ip'] < end:
                symbol = candidate
                break
            pos -= 1
        counts[symbol] += 1
        by_thread[sample['tid']][symbol] += 1
    def top(counter):
        return [{'guest_block': name, 'samples': count} for name, count in counter.most_common(30)]
    return {'samples': len(samples), 'jit_records': records, 'jit_code_loads': loads,
            'jit_header_pid': pid, 'elf_machine': machine, 'jit_created_ns': created,
            'last_jit_timestamp_ns': last_timestamp,
            'sample_first_ns': min(s['time_ns'] for s in samples), 'sample_last_ns': last_sample,
            'mapped_samples': len(samples) - counts[None], 'unmapped_samples': counts[None],
            'top_guest_blocks': top(counts),
            'threads': {str(tid): top(counter) for tid, counter in by_thread.items()},
            'mapping': 'latest preceding code-load record covering the sampled host IP',
            'scope': 'guest translation-block start; not exact guest instruction or guest process',
            'timestamp_barrier_verified': barrier,
            'producer_exit_must_be_verified_separately': not live}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('jitdump', type=Path)
    parser.add_argument('samples', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--live', action='store_true', help='Require a complete record newer than the final sample')
    parser.add_argument('--host-pid', type=int, required=True,
                        help='Host QEMU PID used by perf; excludes inherited child samples')
    args = parser.parse_args(argv)
    if args.host_pid <= 0:
        parser.error('host-pid must be positive')
    samples = read_samples(args.samples.read_text())
    selected = [s for s in samples if s['pid'] == args.host_pid]
    if not selected:
        parser.error('No samples for the selected host PID')
    result = map_samples(args.jitdump, selected, live=args.live)
    result['host_pid'] = args.host_pid
    result['excluded_pid_samples'] = len(samples) - len(selected)
    with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as report:
        json.dump(result, report, indent=2)
    print(json.dumps({key: result[key] for key in ['samples', 'mapped_samples', 'unmapped_samples']}))


if __name__ == '__main__':
    main()
