#!/usr/bin/env python3
"""Install a locally built native focused-element helper, verify it, and send no input."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import struct
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tungtungtungbrowser.guest import Guest, GuestError
from tungtungtungbrowser.native_focus import NativeFocusReader, helper_path


def read_build(directory):
    directory = Path(directory)
    manifest = directory / 'build.json'
    binary = directory / 'focused-element'
    if manifest.stat().st_size > 16384 or not 32 <= binary.stat().st_size <= 256 * 1024:
        raise ValueError('Unsupported helper build size')
    with manifest.open('r') as source:
        text = source.read(16385)
    if len(text) > 16384:
        raise ValueError('Build manifest exceeds 16 KiB')
    build = json.loads(text)
    sources = {name: hashlib.sha256((ROOT / 'native' / name).read_bytes()).hexdigest()
               for name in ('focused_element.c', 'focused_element_libSystem.tbd', 'focused_element_entitlements.plist')}
    if (not isinstance(build, dict) or type(build.get('protocol')) is not int
            or build['protocol'] != 1 or build.get('binary') != 'focused-element' or build.get('sources') != sources):
        raise ValueError('Build manifest does not match this checkout; rebuild the helper')
    with binary.open('rb') as source:
        data = source.read(256 * 1024 + 1)
    if not 32 <= len(data) <= 256 * 1024 or struct.unpack_from('<4I', data) != (0xFEEDFACF, 0x100000C, 0, 2):
        raise ValueError('Expected an arm64 Mach-O executable')
    sha256 = hashlib.sha256(data).hexdigest()
    if build.get('binary_sha256') != sha256:
        raise ValueError('Helper binary does not match the build manifest')
    return data, sha256


def install(guest, data, sha256, destination):
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError('Helper digest mismatch')
    deployment_id = uuid.uuid4().hex
    final = helper_path(sha256, deployment_id)
    temporary = final + '.staging'
    destination = Path(destination)
    if destination.resolve().is_relative_to(ROOT):
        raise ValueError('Keep installation evidence outside the repository')
    destination.mkdir(mode=0o700, exist_ok=False)
    report = {'status': 'installing', 'helper_sha256': sha256, 'path': final, 'deployment_id': deployment_id,
              'staging_path': temporary, 'native_input_sent': False, 'calls': []}

    def save():
        (destination / 'install.json').write_text(json.dumps(report, indent=2) + '\n')

    class RecordingGuest:
        def _rpc(self, method, **params):
            row = {'method': method, 'state': 'requested'}
            report['calls'].append(row)
            save()
            try:
                result = guest._rpc(method, **params)
                row['state'] = 'received'
                row['result'] = {k: v for k, v in result.items() if k != 'content'} if isinstance(result, dict) else result
                return result
            except Exception as exc:
                row.update(state='failed', error=str(exc))
                raise
            finally:
                save()

    api = RecordingGuest()
    save()
    try:
        result = api._rpc('files.write', path=temporary, encoding='base64', content=base64.b64encode(data).decode())
        if result.get('path') != temporary or result.get('bytes') != len(data):
            raise GuestError('Native helper upload was not acknowledged')
        result = api._rpc('files.read', path=temporary, binary=True, limit=len(data))
        if (result.get('path') != temporary or result.get('encoding') != 'base64'
                or result.get('truncated') is not False or result.get('size') != len(data)
                or hashlib.sha256(base64.b64decode(result['content'], validate=True)).hexdigest() != sha256):
            raise GuestError('Native helper readback failed')
        api._rpc('files.chmod', path=temporary, mode='700')
        # The pinned native move operation refuses an existing destination.
        # A failed upload/readback/rename is retained, not retried or removed.
        result = api._rpc('files.rename', **{'from': temporary, 'to': final})
        if result.get('source') != temporary or result.get('destination') != final:
            raise GuestError('Native helper installation was not acknowledged')
        report['native_check'] = NativeFocusReader(api, sha256=sha256, deployment_id=deployment_id).check()
        report['status'] = 'complete'
        return report
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        raise
    finally:
        save()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build_directory', type=Path)
    parser.add_argument('--output', type=Path, required=True, help='new external evidence directory')
    parser.add_argument('--cid', type=int, default=3)
    args = parser.parse_args(argv)
    try:
        data, digest = read_build(args.build_directory)
        result = install(Guest(cid=args.cid, timeout=60), data, digest, args.output)
    except (OSError, ValueError, GuestError, KeyError) as exc:
        parser.exit(1, f'Helper installation failed: {exc}. No input was requested; evidence and staging files were retained.\n')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
