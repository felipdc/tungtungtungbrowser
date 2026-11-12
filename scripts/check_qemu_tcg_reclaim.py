#!/usr/bin/env python3
"""Disposable QEMU correctness/residency experiment; no device data or network."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import socket
import struct
import subprocess
import time


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class QMP:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect(str(path))
        self.file = self.sock.makefile('rb')
        assert 'QMP' in json.loads(self.file.readline())
        self.serial = 0
        self.call('qmp_capabilities')

    def call(self, method, args=None):
        self.serial += 1
        value = {'execute': method, 'id': self.serial}
        if args is not None:
            value['arguments'] = args
        self.sock.sendall(json.dumps(value).encode() + b'\n')
        while True:
            line = self.file.readline()
            if not line:
                raise RuntimeError('QMP closed before response')
            result = json.loads(line)
            if result.get('id') == self.serial:
                if 'error' in result:
                    raise RuntimeError(result['error'])
                return result['return']

    def hmp(self, command):
        return self.call('human-monitor-command', {'command-line': command})


class GDB:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect(str(path))

    def command(self, body):
        data = body.encode()
        self.sock.sendall(b'$' + data + b'#' + f'{sum(data) % 256:02x}'.encode())
        assert self.sock.recv(1) == b'+'
        assert self.sock.recv(1) == b'$'
        result = bytearray()
        while True:
            char = self.sock.recv(1)
            if not char:
                raise RuntimeError('GDB connection closed')
            if char == b'#':
                break
            result.extend(char)
        checksum = b''
        while len(checksum) < 2:
            char = self.sock.recv(2 - len(checksum))
            if not char:
                raise RuntimeError('GDB closed during response checksum')
            checksum += char
        assert int(checksum, 16) == sum(result) % 256
        self.sock.sendall(b'+')
        # QEMU may run-length encode repeated hex digits in memory replies.
        decoded = bytearray()
        i = 0
        while i < len(result):
            if result[i] == ord('*'):
                i += 1
                decoded.extend([decoded[-1]] * (result[i] - 29))
            else:
                decoded.append(result[i])
            i += 1
        return decoded.decode()

    def control(self, address):
        value = self.command(f'm{address:x},38')
        assert not value.startswith('E'), value
        return list(struct.unpack('<7Q', bytes.fromhex(value)))

    def epoch(self, address, epoch):
        data = struct.pack('<Q', epoch).hex()
        assert self.command(f'M{address:x},8:{data}') == 'OK'


def code_memory(pid):
    text = Path(f'/proc/{pid}/smaps').read_text()
    blocks = re.split(r'(?=^[0-9a-f]+-[0-9a-f]+ )', text, flags=re.M)
    result = []
    for block in blocks:
        lines = block.splitlines()
        if not lines:
            continue
        header = lines[0].split(maxsplit=5)
        if len(header) != 5 or header[1] != 'rwxp' or header[4] != '0':
            continue
        values = {m[1]: int(m[2]) for line in lines[1:]
                  if (m := re.match(r'^(Size|Rss|Anonymous|AnonHugePages):\s+(\d+) kB$', line))}
        result.append({'range': header[0], **values})
    return {'mappings': result, 'rss_kib': sum(x['Rss'] for x in result),
            'size_kib': sum(x['Size'] for x in result)}


def jit_info(qmp):
    raw = qmp.hmp('info jit')
    size = re.search(r'gen code size\s+(\d+)/(\d+)', raw)
    flush = re.search(r'TB flush count\s+(\d+)', raw)
    assert size and flush, raw
    return {'raw': raw, 'generated_bytes': int(size[1]), 'capacity_bytes': int(size[2]),
            'flushes': int(flush[1])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--symbols', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reclaim', choices=['default', 'on', 'off'], required=True)
    parser.add_argument('--thread', choices=['single', 'multi'], default='multi')
    parser.add_argument('--cache-mib', type=int, choices=[4, 1024], required=True)
    parser.add_argument('--cpus', type=int, choices=[2, 3], default=2,
                        help='Two active CPUs; an optional third remains halted')
    args = parser.parse_args()
    out = args.output.resolve()
    root = Path(__file__).resolve().parents[1]
    if out == root or root in out.parents:
        parser.error('Keep evidence outside the source checkout')
    if ',' in str(out):
        parser.error('Output path must not contain a QEMU option separator')
    for name in ('qemu', 'fixture', 'symbols'):
        path = getattr(args, name).resolve(strict=True)
        if not path.is_file():
            parser.error(name + ' must be a regular file')
        setattr(args, name, path)
    out.mkdir(mode=0o700, parents=False, exist_ok=False)
    (out / 'runner.py').write_bytes(Path(__file__).read_bytes())
    symbols = {line.split()[2]: int(line.split()[0], 16)
               for line in args.symbols.read_text().splitlines() if len(line.split()) == 3}
    address = symbols['control']
    blocks = symbols['BLOCKS']
    # Short paths are required for AF_UNIX. Each process gets its own directory.
    qmp_path, gdb_path = out / 'qmp', out / 'gdb'
    if max(len(str(qmp_path).encode()), len(str(gdb_path).encode())) >= 104:
        parser.error('Output path is too long for Unix sockets')
    accel = f'tcg,thread={args.thread},tb-size={args.cache_mib},split-wx=off'
    if args.reclaim != 'default':
        accel += f',tb-reclaim={args.reclaim}'
    command = [str(args.qemu), '-M', 'virt', '-cpu', 'cortex-a57', '-smp', str(args.cpus), '-m', '64M',
               '-accel', accel, '-nodefaults', '-display', 'none', '-serial', 'none',
               '-monitor', 'none', '-net', 'none', '-S',
               '-qmp', f'unix:{qmp_path},server=on,wait=off',
               '-gdb', f'unix:{gdb_path},server=on,wait=off',
               '-kernel', str(args.fixture.resolve()),
               '-semihosting-config', 'enable=on,target=native']
    report = {'status': 'running', 'command': command, 'started_unix': time.time(),
              'qemu_sha256': digest(args.qemu), 'fixture_sha256': digest(args.fixture),
              'reclaim': args.reclaim, 'thread': args.thread, 'cache_mib': args.cache_mib,
              'cpu_count': args.cpus, 'active_cpu_count': 2,
              'cycles': [], 'monitor_operations': [], 'ios_compatibility_verified': False,
              'browser_performance_verified': False}

    def save():
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    def wait_round(epoch):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            assert process.poll() is None, 'Guest process exited during workload'
            qmp.call('stop')
            values = gdb.control(address)
            assert values[3:5] == [0, 0], values
            if values[1:3] == [epoch, epoch]:
                assert values[5:7] == [blocks + epoch] * 2, values
                return values
            qmp.call('cont')
            time.sleep(0.05)
        raise TimeoutError('Original guest round did not complete')

    save()
    process = qmp = gdb = None
    try:
        with (out / 'stdout').open('xb') as stdout, (out / 'stderr').open('xb') as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            report['pid'] = process.pid
            save()
            deadline = time.monotonic() + 10
            while not qmp_path.exists() and time.monotonic() < deadline:
                assert process.poll() is None, 'QEMU exited before QMP'
                time.sleep(0.02)
            qmp, gdb = QMP(qmp_path), GDB(gdb_path)
            assert gdb.command('?').startswith(('T', 'S'))
            qmp.call('cont')
            initial = wait_round(1)
            report['initial'] = {'values': initial, 'jit': jit_info(qmp),
                                 'code_memory': code_memory(process.pid)}
            if args.cache_mib == 4:
                assert report['initial']['jit']['flushes'] > 0, 'No capacity flush observed'
            for cycle in range(3):
                before = {'jit': jit_info(qmp), 'code_memory': code_memory(process.pid)}
                epoch = cycle + 2
                gdb.epoch(address, epoch)
                qmp.call('cont')
                values = wait_round(epoch)
                after = {'jit': jit_info(qmp), 'code_memory': code_memory(process.pid)}
                assert after['jit']['flushes'] > before['jit']['flushes'], 'No capacity flush observed'
                assert after['jit']['capacity_bytes'] == before['jit']['capacity_bytes']
                report['cycles'].append({'before': before, 'after': after, 'next_values': values})
                save()
            gdb.epoch(address, 255)
            qmp.call('cont')
            report['exit_code'] = process.wait(timeout=10)
            assert report['exit_code'] == 0
            report['status'] = 'complete'
    except BaseException as exc:
        report.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            report['disposable_process_terminated_after_failure'] = True
        raise
    finally:
        if gdb is not None:
            gdb.sock.close()
        if qmp is not None:
            qmp.file.close()
            qmp.sock.close()
        report['finished_unix'] = time.time()
        save()
    print(json.dumps({'status': report['status'], 'reclaim': args.reclaim, 'thread': args.thread,
                      'cycles': [{'before_kib': x['before']['code_memory']['rss_kib'],
                                  'after_kib': x['after']['code_memory']['rss_kib']}
                                 for x in report['cycles']]}), flush=True)


if __name__ == '__main__':
    main()
