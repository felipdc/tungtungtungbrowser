#!/usr/bin/env python3
"""Check tracked source for accidental runtime data or private configuration."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {'.state', 'artifacts', 'vendor', '.venv', '__pycache__'}
FORBIDDEN_SUFFIXES = {'.ipsw', '.img', '.qcow2', '.pem', '.key', '.p12', '.pfx', '.deb', '.rpm'}
PRIVATE_KEY = re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----')


def main():
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    errors = []
    for name in filter(None, names):
        path = ROOT / name
        if not path.is_file():
            errors.append(f'{name}: tracked path missing'); continue
        if set(Path(name).parts) & FORBIDDEN_PARTS or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f'{name}: runtime or private file')
        if path.name.startswith('.env') and path.name != '.env.example':
            errors.append(f'{name}: environment configuration')
        if path.stat().st_size > 1024 * 1024:
            errors.append(f'{name}: source file exceeds 1 MiB')
        if PRIVATE_KEY.search(path.read_bytes()):
            errors.append(f'{name}: private key material')
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print('Tracked source boundary checks passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
