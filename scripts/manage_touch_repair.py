#!/usr/bin/env python3
"""Install, inspect or deliberately apply/revert the exact native touch repair.

Runs fixed administrative native commands, never code in a browser page.
Each invocation uses a new evidence directory and never retries a guest write.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import struct
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tungtungtungbrowser.guest import Guest, GuestError
from prepare_touch_repair import RECIPE, REPAIR_ID
from touch_repair_build import recipe_header, source_hashes

BACKUPS = '/private/var/root/ttt-touch-repair-v1'


def helper_path(digest):
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Expected a lowercase SHA-256 helper digest')
    return '/private/var/root/ttt-touch-repair-v1-' + digest


def read_build(directory):
    directory = Path(directory)
    manifest, binary = directory / 'build.json', directory / 'touch-repair'
    if manifest.stat().st_size > 32768 or not 32 <= binary.stat().st_size <= 256 * 1024:
        raise ValueError('Unsupported helper build size')
    build = json.loads(manifest.read_text())
    if (not isinstance(build, dict) or type(build.get('protocol')) is not int
            or build['protocol'] != 1 or build.get('binary') != binary.name
            or build.get('repair_id') != REPAIR_ID or build.get('sources') != source_hashes()
            or build.get('recipe_sha256') != hashlib.sha256(recipe_header().encode()).hexdigest()):
        raise ValueError('Build does not match this checkout; rebuild the helper')
    data = binary.read_bytes()
    if not 32 <= len(data) <= 256 * 1024 or struct.unpack_from('<4I', data) != (0xFEEDFACF, 0x100000C, 0, 2):
        raise ValueError('Expected an arm64 Mach-O executable')
    digest = hashlib.sha256(data).hexdigest()
    if build.get('binary_sha256') != digest:
        raise ValueError('Helper binary does not match its manifest')
    return data, digest


def firmware_receipt(path):
    """Check supplied build evidence; this is not runtime firmware attestation."""
    data = Path(path).read_bytes()
    if len(data) > 4 * 1024 * 1024:
        raise ValueError('Firmware receipt exceeds 4 MiB')
    receipt = plistlib.loads(data)
    required = {'kernelcache': 'kernel-boot-amfi_trustcache', 'TXM': 'txm-boot-trustcache_bypass'}
    if not isinstance(receipt, dict) or any(
            patch not in receipt.get('Parts', {}).get(part, {}).get('Patches', [])
            for part, patch in required.items()):
        raise ValueError('Firmware receipt does not declare the required native trust patches')
    return {'sha256': hashlib.sha256(data).hexdigest(), 'declared_patches': required,
            'runtime_firmware_attested': False}


def durable_json(path, value):
    temporary = path.with_suffix('.pending')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class Session:
    def __init__(self, guest, digest, operation, output):
        self.path = helper_path(digest)
        self.guest = guest
        self.output = Path(output)
        if self.output.resolve().is_relative_to(ROOT):
            raise ValueError('Keep installation evidence outside the source checkout')
        self.output.mkdir(mode=0o700, exist_ok=False)
        self.report = {'protocol': 1, 'repair_id': REPAIR_ID, 'operation': operation,
                       'helper_sha256': digest, 'guest_path': self.path,
                       'status': 'started', 'attempt': uuid.uuid4().hex,
                       'mutation_requested': False, 'browser_verified': False, 'calls': []}
        self.save()

    def save(self):
        durable_json(self.output / 'report.json', self.report)

    def rpc(self, method, **params):
        # Persist intent before sending. A transport error never causes a retry.
        row = {'method': method, 'params': {k: v for k, v in params.items() if k != 'content'},
               'state': 'requested'}
        self.report['calls'].append(row)
        self.save()
        try:
            answer = self.guest._rpc(method, **params)
            row.update(state='received', response={k: v for k, v in answer.items() if k != 'content'}
                       if isinstance(answer, dict) else answer)
            return answer
        except Exception as exc:
            row.update(state='failed', error=str(exc),
                       request_may_have_been_sent=getattr(exc, 'request_may_have_been_sent', True))
            raise
        finally:
            self.save()

    def process(self, path, arguments, *, require_success=True):
        if path not in (self.path, '/sbin/mount'):
            raise ValueError('Unsupported native executable')
        source = 'proc.run(' + json.dumps(path) + ', ' + json.dumps(arguments) + ', "")'
        answer = self.rpc('script.eval', source=source, timeout=600)
        if (not isinstance(answer, dict) or answer.get('ok') is not True
                or answer.get('crashed') is not False or answer.get('output') != ''
                or not isinstance(answer.get('result'), str) or len(answer['result']) > 65536):
            raise GuestError('Native process response is incomplete; no command is repeated')
        process = json.loads(answer['result'])
        if (not isinstance(process, dict) or type(process.get('status')) is not int
                or process.get('signal', 0) is not None
                or not isinstance(process.get('stdout'), str) or not isinstance(process.get('stderr'), str)
                or (require_success and (process['status'] != 0 or process['stderr'] != ''))):
            raise GuestError('Native process failed; inspect the retained response before any further action')
        return process

    def helper(self, operation, attempt=None):
        if operation not in ('inspect', 'apply', 'revert') or (operation == 'inspect') != (attempt is None):
            raise ValueError('Unsupported helper operation')
        if attempt is not None and not re.fullmatch('[0-9a-f]{32}', attempt):
            raise ValueError('Invalid attempt ID')
        arguments = [operation] + ([] if attempt is None else [attempt])
        result = self.process(self.path, arguments)
        rows = [json.loads(line) for line in result['stdout'].splitlines()]
        expected_stages = ['inspection'] if operation == 'inspect' else [
            'preparing', 'backup_verified', 'ready_to_replace', 'reboot_required']
        if len(rows) != len(expected_stages):
            raise GuestError('Incomplete native repair journal')
        for i, (row, stage) in enumerate(zip(rows, expected_stages)):
            final = i == len(rows) - 1
            status = ('verified' if operation == 'inspect' else 'complete') if final else 'running'
            if (not isinstance(row, dict) or type(row.get('protocol')) is not int or row['protocol'] != 1
                    or row.get('operation') != operation or row.get('attempt') != (attempt or '')
                    or row.get('stage') != stage or row.get('status') != status
                    or row.get('sha256') not in (RECIPE.original_sha256, RECIPE.repaired_sha256)
                    or not isinstance(row.get('boot_uuid'), str)
                    or not re.fullmatch('[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', row['boot_uuid'])
                    or row['boot_uuid'] != rows[0]['boot_uuid']
                    or row.get('replacement_performed') is not (operation != 'inspect' and final)
                    or type(row.get('errno')) is not int or row['errno'] != 0):
                raise GuestError('Invalid native repair journal')
        if operation != 'inspect':
            before, after = ((RECIPE.original_sha256, RECIPE.repaired_sha256) if operation == 'apply'
                             else (RECIPE.repaired_sha256, RECIPE.original_sha256))
            if [row['sha256'] for row in rows] != [before, before, after, after]:
                raise GuestError('Native repair journal hashes do not match the operation')
        return rows

    def mount_state(self):
        result = self.process('/sbin/mount', [])
        roots = re.findall(r'^\S+ on / \(([^\r\n]*)\)$', result['stdout'], re.MULTILINE)
        if len(roots) != 1 or 'apfs' not in roots[0].split(', '):
            raise GuestError('Cannot establish the native System mount state')
        return {'read_only': 'read-only' in roots[0].split(', '), 'options': roots[0]}

    def read_journal(self, attempt):
        if not isinstance(attempt, str) or not re.fullmatch('[0-9a-f]{32}', attempt):
            raise ValueError('Invalid attempt ID')
        path = BACKUPS + '/' + attempt + '/events.jsonl'
        result = self.rpc('files.read', path=path, binary=True, limit=65536)
        if (not isinstance(result, dict) or result.get('path') != path
                or result.get('encoding') != 'base64' or result.get('truncated') is not False
                or type(result.get('size')) is not int or not 0 < result['size'] <= 65536):
            raise GuestError('Native journal readback is incomplete')
        raw = base64.b64decode(result['content'], validate=True)
        if len(raw) != result['size']:
            raise GuestError('Native journal size mismatch')
        rows = [json.loads(line) for line in raw.decode().splitlines()]
        durable_json(self.output / 'native-journal.json', rows)
        return rows

    def install(self, data):
        if hashlib.sha256(data).hexdigest() != self.report['helper_sha256']:
            raise ValueError('Helper digest mismatch')
        temporary = self.path + '.staging-' + self.report['attempt']
        result = self.rpc('files.write', path=temporary, encoding='base64', content=base64.b64encode(data).decode())
        if not isinstance(result, dict) or result.get('path') != temporary or result.get('bytes') != len(data):
            raise GuestError('Helper upload not acknowledged')
        result = self.rpc('files.read', path=temporary, binary=True, limit=len(data))
        if (not isinstance(result, dict) or result.get('path') != temporary
                or result.get('encoding') != 'base64' or result.get('truncated') is not False
                or result.get('size') != len(data)
                or base64.b64decode(result['content'], validate=True) != data):
            raise GuestError('Helper upload readback mismatch')
        self.rpc('files.chmod', path=temporary, mode='700')
        result = self.rpc('files.rename', **{'from': temporary, 'to': self.path})
        if not isinstance(result, dict) or result.get('source') != temporary or result.get('destination') != self.path:
            raise GuestError('Helper installation not acknowledged')
        self.report['inspection'] = self.helper('inspect')[-1]
        self.report['status'] = 'helper_installed'

    def change(self, operation, receipt):
        if operation not in ('apply', 'revert'):
            raise ValueError('Expected apply or revert')
        self.report['firmware_receipt'] = receipt
        self.report['inspection'] = self.helper('inspect')[-1]
        expected = RECIPE.original_sha256 if operation == 'apply' else RECIPE.repaired_sha256
        if self.report['inspection']['sha256'] != expected:
            raise GuestError('Cache is not the expected preimage; no write requested')
        self.report['mount_before'] = self.mount_state()
        if not self.report['mount_before']['read_only']:
            raise GuestError('Start from a clean boot with the System volume read-only')
        self.process('/sbin/mount', ['-u', '-w', '/'])
        self.report['mount_writable'] = self.mount_state()
        if self.report['mount_writable']['read_only']:
            raise GuestError('System volume did not become writable')
        self.report.update(status='mutation_requested', mutation_requested=True)
        self.save()
        rows = self.helper(operation, self.report['attempt'])
        self.report['native_events'] = rows
        if rows[0]['boot_uuid'] != self.report['inspection']['boot_uuid']:
            raise GuestError('Guest boot identity changed during installation')
        if self.read_journal(self.report['attempt']) != rows:
            raise GuestError('Durable native journal differs from process output')
        self.report['backup_path'] = BACKUPS + '/' + self.report['attempt'] + '/backup.cache'
        # Native clean shutdown/restart is an explicit next step. Do not issue
        # another command after a lost mutation response or silently reboot.
        self.report['status'] = 'reboot_required'

    def verify(self, previous):
        if (not isinstance(previous, dict) or previous.get('protocol') != 1
                or previous.get('repair_id') != REPAIR_ID or previous.get('status') != 'reboot_required'
                or previous.get('helper_sha256') != self.report['helper_sha256']
                or previous.get('operation') not in ('apply', 'revert')
                or not isinstance(previous.get('native_events'), list) or len(previous['native_events']) != 4):
            raise ValueError('Expected a completed apply/revert report awaiting reboot')
        last = previous['native_events'][-1]
        expected = RECIPE.repaired_sha256 if previous['operation'] == 'apply' else RECIPE.original_sha256
        if last.get('stage') != 'reboot_required' or last.get('sha256') != expected:
            raise ValueError('Previous operation did not establish the expected cache')
        self.report['inspection'] = self.helper('inspect')[-1]
        self.report['mount'] = self.mount_state()
        self.report['previous_attempt'] = previous['attempt']
        journal = self.read_journal(previous['attempt'])
        if (self.report['inspection']['boot_uuid'] == last.get('boot_uuid')
                or self.report['inspection']['sha256'] != expected or not self.report['mount']['read_only']
                or journal != previous['native_events']):
            raise GuestError('Reboot, read-only mount, cache hash or retained journal did not verify')
        self.report['status'] = 'reboot_verified'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('install', 'inspect', 'apply', 'revert', 'verify'))
    parser.add_argument('--build', type=Path, required=True, help='locally built, source-matched helper directory')
    parser.add_argument('--output', type=Path, required=True, help='new external evidence directory')
    parser.add_argument('--firmware-receipt', type=Path, help='private PatchReceipt.plist for this guest')
    parser.add_argument('--previous', type=Path, help='completed apply/revert report.json, for verify')
    parser.add_argument('--attempt', help='also retrieve this attempt journal during inspection')
    parser.add_argument('--cid', type=int, default=3)
    args = parser.parse_args(argv)
    session = None
    try:
        data, digest = read_build(args.build)
        receipt = None
        if args.operation in ('apply', 'revert'):
            if args.firmware_receipt is None:
                raise ValueError('apply/revert requires --firmware-receipt')
            receipt = firmware_receipt(args.firmware_receipt)
        if args.operation == 'verify' and args.previous is None:
            raise ValueError('verify requires --previous')
        session = Session(Guest(cid=args.cid, timeout=630), digest, args.operation, args.output)
        if args.operation == 'install':
            session.install(data)
        elif args.operation in ('apply', 'revert'):
            session.change(args.operation, receipt)
        elif args.operation == 'verify':
            session.verify(json.loads(args.previous.read_text()))
        else:
            session.report['inspection'] = session.helper('inspect')[-1]
            session.report['mount'] = session.mount_state()
            if args.attempt:
                session.read_journal(args.attempt)
            session.report['status'] = 'inspected'
        session.save()
    except (OSError, ValueError, GuestError, KeyError, TypeError) as exc:
        if session is not None:
            session.report.update(status='failed', error=str(exc))
            session.save()
        parser.exit(1, f'Repair operation failed: {exc}. Evidence is retained; no command was retried.\n')
    print(json.dumps(session.report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
