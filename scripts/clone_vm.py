#!/usr/bin/env python3
"""Copy a stopped raw VM bundle to a new independent instance directory."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tungtungtungbrowser.vm_lease import lease_vm


def clone(source, destination):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('Source and destination must be separate, non-nested directories')
    if destination.is_relative_to(ROOT):
        raise ValueError('Keep VM bundles outside the checkout')
    with lease_vm(source):
        prior = source / 'exit.json'
        if prior.exists():
            outcome = json.loads(prior.read_text())
            if outcome.get('exit_code') != 0 or (outcome.get('shutdown') or {}).get('forced_termination'):
                raise ValueError('Source did not record a clean shutdown; preserve and investigate it')
        config = plistlib.loads((source / 'config.plist').read_bytes())
        roms = config.get('romImages') or {}
        files = ['Disk.img', 'nvram.bin', 'ecid', 'config.plist',
                 roms.get('avpBooter') or 'AVPBooter.vresearch1.bin',
                 roms.get('avpSEPBooter') or 'AVPSEPBooter.vresearch1.bin']
        files += [name for name in ('SEPStorage', 'serial', 'gfx-res') if (source / name).exists()]
        for name in files:
            if not isinstance(name, str) or Path(name).name != name or not (source / name).is_file():
                raise ValueError('Unsupported or missing bundle asset: ' + str(name))
        for name in ('Disk.img', 'nvram.bin', 'SEPStorage'):
            path = source / name
            if path.exists():
                with path.open('rb') as disk:
                    if disk.read(4) == b'QFI\xfb':
                        raise ValueError('This copier accepts raw bundles, not qcow2 backing chains')
        cache = source / 'cache'
        if cache.exists() and (not cache.is_dir() or cache.is_symlink()
                               or any(p.is_symlink() for p in cache.rglob('*'))):
            raise ValueError('Cache symlinks may share mutable state; use a self-contained cache')
        destination.mkdir(parents=True, exist_ok=False, mode=0o700)
        with (destination / '.tungtungtungbrowser.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            report = {'version': 1, 'status': 'copying', 'source': str(source),
                      'started_unix': time.time(), 'files': {}, 'identity_regenerated': False}
            def save():
                (destination / 'clone.json').write_text(json.dumps(report, indent=2) + '\n')
            save()
            try:
                for name in dict.fromkeys(files):
                    original, copied = source / name, destination / name
                    before = original.stat()
                    subprocess.run(['cp', '--reflink=auto', '--sparse=always', '--dereference',
                                    '--', str(original), str(copied)], check=True)
                    after = original.stat()
                    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                        raise RuntimeError('Source changed during copy: ' + name)
                    if copied.stat().st_size != before.st_size or copied.samefile(original):
                        raise RuntimeError('Copy does not have independent storage: ' + name)
                    report['files'][name] = {'bytes': before.st_size, 'source_mtime_ns': before.st_mtime_ns}
                    save()
                if cache.exists():
                    subprocess.run(['cp', '-a', '--reflink=auto', '--sparse=always',
                                    '--', str(cache), str(destination / 'cache')], check=True)
                else:
                    (destination / 'cache').mkdir()
                report.update(status='complete', finished_unix=time.time())
            except BaseException as error:
                report.update(status='failed', error=str(error), finished_unix=time.time())
                raise
            finally:
                save()
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(clone(args.source, args.destination), indent=2))
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Clone stopped: {error}. Any partial destination is retained.\n')


if __name__ == '__main__':
    main()
