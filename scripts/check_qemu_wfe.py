#!/usr/bin/env python3
"""Exercise the optional QEMU WFE backoff with a disposable two-core ARM program.

This creates no iPhone and opens no existing guest disk. Run it separately from
browser resource measurements: even this small test consumes host CPU time.
"""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True,
                        help='QEMU binary built with patches/qemu-wfe-backoff.patch')
    parser.add_argument('--output', type=Path, required=True,
                        help='New evidence directory outside the source checkout')
    parser.add_argument('--llvm-mc', default='llvm-mc-19')
    parser.add_argument('--llvm-objcopy', default='llvm-objcopy-19')
    parser.add_argument('--llvm-readelf', default='llvm-readelf-19')
    args = parser.parse_args()
    qemu = args.qemu.resolve(strict=True)
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error('Output must be outside the source checkout')
    if ',' in str(output):
        parser.error('Output path must not contain a QEMU option separator (comma)')
    if args.output.exists() or args.output.is_symlink():
        parser.error('Output directory must be new')
    if not qemu.is_file():
        parser.error('QEMU must be a regular file')
    compiler_tools = {}
    for name in ('llvm_mc', 'llvm_objcopy', 'llvm_readelf'):
        found = shutil.which(getattr(args, name))
        if found is None:
            parser.error(f'Executable not found: {getattr(args, name)}')
        # LLVM selects readelf/readobj output from argv[0]. Resolving a tool's
        # symlink would silently select the other interface.
        compiler_tools[name] = str(Path(found).absolute())
    output.mkdir(mode=0o700)
    source = ROOT / 'native/check_qemu_wfe.S'
    with qemu.open('rb') as binary:
        qemu_hash = hashlib.file_digest(binary, 'sha256').hexdigest()
    report = {'status': 'running', 'started_unix': time.time(),
              'qemu': str(qemu), 'qemu_sha256': qemu_hash,
              'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'compiler_tools': compiler_tools, 'commands': [], 'tests': [],
              'ios_compatibility_verified': False, 'browser_performance_verified': False}

    def save():
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(label, command, timeout):
        row = {'label': label, 'command': command, 'started_unix': time.time()}
        report['commands'].append(row)
        save()
        start = time.monotonic()
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        try:
            with (output / (label + '.stdout')).open('xb') as stdout, \
                    (output / (label + '.stderr')).open('xb') as stderr:
                process = subprocess.run(command, stdout=stdout, stderr=stderr,
                                         timeout=timeout, check=False)
            row['exit_code'] = process.returncode
        except Exception as exc:
            row.update(error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            after = resource.getrusage(resource.RUSAGE_CHILDREN)
            row['wall_seconds'] = time.monotonic() - start
            row['cpu_seconds'] = (after.ru_utime + after.ru_stime
                                  - before.ru_utime - before.ru_stime)
            save()
        return row

    def require_success(row):
        if row['exit_code'] != 0:
            raise RuntimeError(f"{row['label']} exited {row['exit_code']}; see retained output")

    save()
    try:
        require_success(run('assemble', [compiler_tools['llvm_mc'], '-triple=aarch64',
                                        '-filetype=obj', str(source), '-o', str(output / 'check.o')], 30))
        require_success(run('relocations', [compiler_tools['llvm_readelf'], '-r', str(output / 'check.o')], 10))
        if 'There are no relocations' not in (output / 'relocations.stdout').read_text():
            raise RuntimeError('The flat test program must not contain unresolved relocations')
        require_success(run('objcopy', [compiler_tools['llvm_objcopy'], '-O', 'binary',
                                       '--only-section=.text', str(output / 'check.o'),
                                       str(output / 'check.bin')], 10))
        for index, (mode, delay) in enumerate([('multi', 0), ('multi', 50),
                                                ('multi', 0), ('multi', 50), ('single', 50)]):
            command = [str(qemu), '-M', 'virt', '-cpu', f'cortex-a57,wfe-backoff-us={delay}',
                       '-smp', '2', '-m', '64M', '-accel', f'tcg,thread={mode}',
                       '-nodefaults', '-display', 'none', '-serial', 'none',
                       '-monitor', 'none', '-net', 'none', '-device',
                       f'loader,file={output / "check.bin"},addr=0x40200000,cpu-num=0',
                       '-semihosting-config', 'enable=on,target=native']
            row = run(f'handoff-{index}', command, 12)
            report['tests'].append({'mode': mode, 'backoff_us': delay,
                                    **{key: row[key] for key in ('exit_code', 'wall_seconds', 'cpu_seconds')}})
            save()
            require_success(row)
        row = run('invalid-range', [str(qemu), '-M', 'virt', '-cpu',
                                   'cortex-a57,wfe-backoff-us=1001', '-display', 'none', '-nodefaults'], 10)
        error = (output / 'invalid-range.stderr').read_text()
        if row['exit_code'] == 0 or 'wfe-backoff-us must be between 0 and 1000' not in error:
            raise RuntimeError('Out-of-range property was not rejected as expected')
        report.update(status='complete', invalid_range_rejected=True)
    except Exception as exc:
        report.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        report['finished_unix'] = time.time()
        save()
    print(json.dumps({'status': report['status'], 'tests': report['tests'],
                      'invalid_range_rejected': report['invalid_range_rejected'],
                      'report': str(output / 'report.json')}, indent=2))


if __name__ == '__main__':
    main()
