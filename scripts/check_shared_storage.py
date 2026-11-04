#!/usr/bin/env python3
"""Check cross-device writes using synthetic disks and stopped guest CPUs.

No prepared device, firmware or network is used. This checks the block graph,
not native boot. Independent overlays are a negative control: their writes
must remain separate. Two backends on one node must see each other's writes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tungtungtungbrowser.display import _Monitor


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def executable(value):
    found = shutil.which(value)
    if found is None:
        raise ValueError(f'Executable not found: {value}')
    return Path(found).resolve()


def run_case(mode, machine, qemu, qemu_img, directory, output):
    directory.mkdir()
    parent = directory / 'parent.raw'
    parent.write_bytes(bytes([0x11]) * (8 * 1024 * 1024))
    parent_hash = digest(parent)
    qmp = directory / 'qmp.sock'
    args = [str(qemu), '-M', machine if machine == 'virt' else machine + ',gfx-device=none',
            '-m', '1G' if machine == 'vresearch101' else '128M', '-smp', '1', '-S',
            '-nodefaults', '-display', 'none', '-serial', 'none', '-monitor', 'none',
            '-net', 'none', '-qmp', f'unix:{qmp},server=on,wait=off']
    if machine == 'virt':
        args += ['-cpu', 'cortex-a57']
    else:
        rom = directory / 'synthetic-rom.bin'
        rom.write_bytes(bytes(65536))
        args += ['-bios', str(rom), '-global', 'vmapple-virtio-blk.share-rw=on']
    overlays = {}
    backends = []
    for index, (disk, guest) in enumerate([('nvram', 'aux-pci'), ('disk', 'root-pci')]):
        views = ['shared'] if mode == 'shared' else ['firmware', 'guest']
        for view in views:
            node = disk + '-' + view
            overlay = directory / (node + '.qcow2')
            subprocess.run([str(qemu_img), 'create', '-q', '-f', 'qcow2', '-F', 'raw',
                            '-b', str(parent), str(overlay)], check=True, timeout=15)
            overlays[node] = overlay
            args += ['-blockdev', json.dumps({
                'driver': 'qcow2', 'node-name': node,
                'file': {'driver': 'file', 'filename': str(overlay)},
            })]
        fw_node = disk + ('-shared' if mode == 'shared' else '-firmware')
        guest_node = disk + ('-shared' if mode == 'shared' else '-guest')
        firmware = 'fw-' + disk
        fw_interface = f'if=pflash,index={index}' if machine == 'vresearch101' else 'if=none'
        args += ['-drive', f'{fw_interface},id={firmware},driver=raw,file.driver=raw,file.file={fw_node}',
                 '-drive', f'if=none,id={guest},driver=raw,file.driver=raw,file.file={guest_node}']
        backends.append((firmware, guest))
    result = {'status': 'starting', 'args': args, 'commands': []}
    report = output / (mode + '.json')

    def save():
        report.write_text(json.dumps(result, indent=2) + '\n')

    save()
    stdout_path, stderr_path = output / (mode + '.stdout'), output / (mode + '.stderr')
    with stdout_path.open('xb') as stdout, stderr_path.open('xb') as stderr:
        process = subprocess.Popen(args, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 15
            while not qmp.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            require(process.poll() is None and qmp.exists(), 'QEMU failed to expose QMP; inspect stderr')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(15)
                sock.connect(str(qmp))
                monitor = _Monitor(sock, time.monotonic() + 30)
                monitor.receive()
                monitor.call('qmp_capabilities')
                result['vm_status'] = monitor.call('query-status')
                require(result['vm_status']['running'] is False, 'Guest CPUs must remain stopped')
                result['nodes'] = monitor.call('query-named-block-nodes')
                result['backends'] = monitor.call('query-block')
                save()
                for firmware, guest in backends:
                    for command in [
                        f'qemu-io {firmware} "write -P 0xa5 4096 4096"',
                        f'qemu-io {guest} "read -P 0xa5 4096 4096"',
                        f'qemu-io {guest} "write -P 0x5a 8192 4096"',
                        f'qemu-io {firmware} "read -P 0x5a 8192 4096"',
                    ]:
                        call = {'command': command}
                        result['commands'].append(call)
                        save()
                        call['response'] = monitor.call('human-monitor-command', {'command-line': command})
                        save()
                monitor.call('quit')
            require(process.wait(timeout=15) == 0, 'QEMU did not exit successfully')
            text, errors = stdout_path.read_text(), stderr_path.read_text()
            require(text.count('wrote 4096/4096') == 4 and text.count('read 4096/4096') == 4,
                    'Incomplete qemu-io operations; inspect retained output')
            mismatches = (text + errors).lower().count('pattern verification failed')
            require(mismatches == (0 if mode == 'shared' else 4), 'Unexpected cross-view write visibility')
            for node, overlay in overlays.items():
                raw = directory / (node + '.raw')
                subprocess.run([str(qemu_img), 'convert', '-O', 'raw', str(overlay), str(raw)],
                               check=True, timeout=15)
                data = raw.read_bytes()
                expected = bytearray(parent.read_bytes())
                if mode == 'shared' or node.endswith('-firmware'):
                    expected[4096:8192] = bytes([0xa5]) * 4096
                if mode == 'shared' or node.endswith('-guest'):
                    expected[8192:12288] = bytes([0x5a]) * 4096
                require(data == expected, f'Unexpected persisted bytes in {node}')
            require(digest(parent) == parent_hash, 'Synthetic parent changed')
            result.update(status='passed', parent_unchanged=True, persisted_bytes_verified=True,
                          cross_view_writes_visible=(mode == 'shared'), pattern_mismatches=mismatches)
            save()
        except BaseException as exc:
            result.update(status='failed', error=repr(exc))
            save()
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    return {key: result[key] for key in ['status', 'parent_unchanged', 'persisted_bytes_verified',
                                        'cross_view_writes_visible', 'pattern_mismatches']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True, help='QEMU AArch64 system emulator')
    parser.add_argument('--qemu-img', default='qemu-img')
    parser.add_argument('--machine', choices=['virt', 'vresearch101'], default='virt')
    parser.add_argument('--output', type=Path, required=True, help='New evidence directory')
    args = parser.parse_args()
    qemu, qemu_img = executable(args.qemu), executable(args.qemu_img)
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'starting', 'qemu_sha256': digest(qemu), 'qemu_img_sha256': digest(qemu_img),
              'machine': args.machine, 'native_boot_validated': False, 'guest_cpus_started': False,
              'vendor_firmware_used': False, 'cases': {}}
    try:
        with tempfile.TemporaryDirectory(prefix='ttt-storage-') as scratch:
            for mode in ['separate', 'shared']:
                report['cases'][mode] = run_case(mode, args.machine, qemu, qemu_img,
                                                  Path(scratch) / mode, args.output)
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', error=repr(exc))
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
