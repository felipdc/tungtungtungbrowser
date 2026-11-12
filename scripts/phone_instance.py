#!/usr/bin/env python3
"""Register and operate independently addressed phone Compose projects."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r'[a-z][a-z0-9-]{0,39}\Z')


def read_instance(registry, name):
    if not NAME.fullmatch(name):
        raise ValueError('Instance name must be 1–40 lowercase letters, digits or hyphens, starting with a letter')
    record = json.loads((Path(registry) / name / 'instance.json').read_text())
    if record.get('version') != 1 or record.get('name') != name:
        raise ValueError('Invalid instance record')
    return record


def register(registry, name, *, vm, runtime, cid, image='tungtungtungbrowser:cpu',
             qemu=None, proxy=None):
    if not NAME.fullmatch(name):
        raise ValueError('Invalid instance name')
    if type(cid) is not int or not 3 <= cid < 2**32 - 1:
        raise ValueError('CID must be between 3 and 4294967294')
    registry = Path(registry).resolve()
    vm, runtime = Path(vm).resolve(strict=True), Path(runtime).resolve(strict=True)
    if any(p.is_relative_to(ROOT) for p in (registry, vm)):
        raise ValueError('Keep instance state and VM bundles outside the checkout')
    if not vm.is_dir() or not (runtime / 'run-vphone.sh').is_file():
        raise ValueError('A prepared VM bundle and built vphone runtime are required')
    for filename in ('Disk.img', 'nvram.bin', 'ecid', 'config.plist'):
        if not (vm / filename).is_file():
            raise ValueError('Missing prepared VM file: ' + filename)
    if qemu is not None and (not qemu.startswith(('/vm/', '/runtime/')) or '..' in Path(qemu).parts):
        raise ValueError('QEMU must be an absolute container path under /vm or /runtime')
    if proxy is not None:
        proxy = str(Path(proxy).resolve(strict=True))
    registry.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (registry / '.registry.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for path in registry.glob('*/instance.json'):
            other = json.loads(path.read_text())
            other_vm = Path(other['vm']).resolve()
            if other['cid'] == cid:
                raise ValueError(f"CID {cid} already belongs to {other['name']}")
            if vm.is_relative_to(other_vm) or other_vm.is_relative_to(vm):
                raise ValueError('Instance VM directories must be separate and non-nested')
            for filename in ('Disk.img', 'nvram.bin', 'SEPStorage'):
                a, b = vm / filename, other_vm / filename
                if a.exists() and b.exists() and a.samefile(b):
                    raise ValueError('Instances must not share writable disk inodes: ' + filename)
            a, b = vm / 'cache', other_vm / 'cache'
            if a.resolve().is_relative_to(b.resolve()) or b.resolve().is_relative_to(a.resolve()):
                raise ValueError('Instances must not share writable caches')
        destination = registry / name
        destination.mkdir(mode=0o700, exist_ok=False)
        namespace = hashlib.sha256(os.fsencode(registry)).hexdigest()[:12]
        record = {'version': 1, 'name': name, 'project': f'ttt-phone-{name}-{namespace}',
                  'cid': cid, 'vm': str(vm), 'runtime': str(runtime),
                  'image': image, 'qemu': qemu, 'proxy': proxy}
        # Use JSON for paths and values; never interpolate a shell command or .env file.
        (destination / 'image.json').write_text(json.dumps({'services': {'iphone': {'image': image}}}, indent=2))
        (destination / 'instance.json').write_text(json.dumps(record, indent=2) + '\n')
        return record


def compose_command(registry, name, action, arguments):
    record = read_instance(registry, name)
    if any(a == '--scale' or a.startswith('--scale=') for a in arguments):
        raise ValueError('Register another named instance with its own VM and CID instead of --scale')
    # A shell configured for another phone must not redirect this one's disks,
    # CID, proxy or resource profile. Docker client credentials remain inherited.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('TTT_', 'COMPOSE_'))}
    env.update(TTT_VM_DIR=record['vm'], TTT_CACHE_DIR=str(Path(record['vm']) / 'cache'),
               TTT_RUNTIME_DIR=record['runtime'], TTT_VM_CID=str(record['cid']))
    command = ['docker', 'compose', '--project-name', record['project'],
               '--env-file', str(ROOT / 'runtime/api-experimental.env'),
               '-f', str(ROOT / 'runtime/compose.yaml')]
    if record['qemu'] is not None:
        env['TTT_QEMU_EXECUTABLE'] = record['qemu']
        command += ['-f', str(ROOT / 'runtime/compose.api-experimental.yaml')]
    if record['proxy'] is not None:
        env['TTT_PROXY_FILE'] = record['proxy']
        command += ['-f', str(ROOT / 'runtime/compose.proxy.yaml')]
    command += ['-f', str(Path(registry).resolve() / name / 'image.json'), action, *arguments]
    return command, env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, required=True, help='Host directory containing instance records')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='Register a prepared independent bundle; does not start it')
    init.add_argument('name')
    init.add_argument('--vm', type=Path, required=True)
    init.add_argument('--runtime', type=Path, required=True)
    init.add_argument('--cid', type=int, required=True)
    init.add_argument('--image', default='tungtungtungbrowser:cpu')
    init.add_argument('--qemu', help='Optional qualified native-FP executable path inside the container')
    init.add_argument('--proxy', type=Path)
    show = commands.add_parser('show')
    show.add_argument('name')
    compose = commands.add_parser('compose', help='Run Compose for exactly one registered phone')
    compose.add_argument('name')
    compose.add_argument('action', choices=['build', 'config', 'up', 'stop', 'down', 'ps', 'logs', 'exec', 'run'])
    compose.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.command == 'init':
            result = register(args.registry, args.name, vm=args.vm, runtime=args.runtime,
                              cid=args.cid, image=args.image, qemu=args.qemu, proxy=args.proxy)
        elif args.command == 'show':
            result = read_instance(args.registry, args.name)
        else:
            command, env = compose_command(args.registry, args.name, args.action, args.arguments)
            if args.action in ('up', 'run'):
                # Host-wide ownership: per-project down -v must not remove it.
                subprocess.run(['docker', 'volume', 'create', 'tungtungtungbrowser-runtime-leases'],
                               env=env, stdout=subprocess.DEVNULL, check=True)
            raise SystemExit(subprocess.call(command, env=env))
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Instance operation stopped: {error}\n')


if __name__ == '__main__':
    main()
