#!/usr/bin/env python3
"""Check two-CPU code updates in disposable ARM machines without guest disks."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, action='append', required=True,
                        help='Repeat for separately built control/candidate executables')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--llvm-mc', default='llvm-mc-19')
    parser.add_argument('--linker', default='ld.lld-19')
    parser.add_argument('--llvm-readelf', default='llvm-readelf-19')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = args.output.resolve()
    if out == root or root in out.parents:
        parser.error('Keep evidence outside the source checkout')
    binaries = [p.resolve(strict=True) for p in args.qemu]
    if not all(p.is_file() for p in binaries):
        parser.error('QEMU paths must be regular files')
    out.mkdir(mode=0o700, parents=False, exist_ok=False)
    report = {'status': 'running', 'started_unix': time.time(), 'commands': [],
              'rounds_per_case': 256, 'active_cpus': 2,
              'native_browser_validated': False, 'cases': []}

    def save():
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(label, command, timeout):
        row = {'label': label, 'command': command, 'started_unix': time.time()}
        report['commands'].append(row)
        save()
        try:
            with (out / (label + '.stdout')).open('xb') as stdout, \
                    (out / (label + '.stderr')).open('xb') as stderr:
                result = subprocess.run(command, stdout=stdout, stderr=stderr,
                                        timeout=timeout)
            row['exit_code'] = result.returncode
            if result.returncode:
                raise RuntimeError(f'{label} exited {result.returncode}')
        except subprocess.TimeoutExpired:
            row['timed_out'] = True
            raise
        finally:
            row['finished_unix'] = time.time()
            save()

    save()
    try:
        source = out / 'fixture.S'
        source.write_bytes((root / 'native/check_qemu_jump_cache.S').read_bytes())
        (out / 'runner.py').write_bytes(Path(__file__).read_bytes())
        obj, elf = out / 'fixture.o', out / 'fixture.elf'
        run('assemble', [args.llvm_mc, '-triple=aarch64', '-filetype=obj',
                         str(source), '-o', str(obj)], 30)
        run('link', [args.linker, '-Ttext=0x40080000', '-e', '_start',
                     str(obj), '-o', str(elf)], 30)
        run('relocations', [args.llvm_readelf, '--relocs', str(elf)], 10)
        relocations = (out / 'relocations.stdout').read_text()
        if 'There are no relocations in this file.' not in relocations:
            raise RuntimeError('Fixture has unresolved or unrecognized relocations')
        report['fixture_sha256'] = hashlib.sha256(elf.read_bytes()).hexdigest()
        for index, binary in enumerate(binaries):
            for thread in ('single', 'multi'):
                label = f'binary-{index}-{thread}'
                case = {'label': label, 'binary': str(binary), 'thread': thread,
                        'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
                report['cases'].append(case)
                save()
                command = [str(binary), '-M', 'virt', '-cpu', 'cortex-a57',
                           '-smp', '2', '-m', '64M', '-accel', f'tcg,thread={thread}',
                           '-nodefaults', '-display', 'none', '-serial', 'none',
                           '-monitor', 'none', '-net', 'none', '-kernel', str(elf),
                           '-semihosting-config', 'enable=on,target=native']
                run(label, command, 30)
                text = ((out / (label + '.stdout')).read_text() +
                        (out / (label + '.stderr')).read_text())
                if text.count('SMC_TWO_CPU_PASS\n') != 1 or 'SMC_TWO_CPU_FAIL' in text:
                    raise RuntimeError(f'{label} did not confirm all code updates')
                case['status'] = 'passed'
                save()
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        report['finished_unix'] = time.time()
        save()
    print(json.dumps({'status': report['status'], 'cases': len(report['cases'])}))


if __name__ == '__main__':
    main()
