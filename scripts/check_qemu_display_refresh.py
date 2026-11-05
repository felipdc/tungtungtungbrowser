#!/usr/bin/env python3
"""Check the optional, default-off QOM display-refresh experiment offline.

Copies pinned display sources to new external evidence, applies the trace and
refresh patches, and compiles the real functions with synthetic callbacks.
Does not modify the input checkout, boot a guest or use a hardware GPU.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_qemu_display_trace import REFERENCE


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu-source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True,
                        help='New evidence directory outside this repository')
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    source = args.qemu_source.resolve(strict=True)
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error('Evidence must be outside the source checkout')
    if output == source or source in output.parents:
        parser.error('Evidence must be outside the supplied QEMU checkout')
    if args.output.exists() or args.output.is_symlink():
        parser.error('Output must be a new directory')
    cc, git = shutil.which(args.cc), shutil.which('git')
    if not cc or not git:
        parser.error('A C compiler with ASan/UBSan and git are required')
    original = {}
    for name, expected in REFERENCE.items():
        data = (source / name).read_bytes()
        if digest(data) != expected:
            parser.error(f'Unsupported or modified display source: {name}')
        original[name] = data
    patches = [ROOT / 'patches' / name for name in
               ['qemu-display-trace.patch', 'qemu-display-refresh-toggle.patch']]
    fixtures = [ROOT / 'native' / name for name in
                ['check_qemu_display_refresh.c.in',
                 'check_qemu_display_refresh_property.c.in']]
    os.umask(0o077)
    output.mkdir(mode=0o700)
    report = {
        'status': 'running', 'started_unix': time.time(),
        'source_revision': '14c9913d2231080bd234c69421ed4f293a25f767',
        'original_sources': dict(REFERENCE),
        'patches': {p.name: digest(p.read_bytes()) for p in patches},
        'fixtures': {p.name: digest(p.read_bytes()) for p in fixtures},
        'sanitizers': ['address', 'undefined'], 'commands': [],
        'native_display_verified': False, 'browser_performance_verified': False,
        'source_checkout_modified': False,
    }

    def save():
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(label, command, *, cwd=output):
        row = {'label': label, 'command': list(map(str, command))}
        report['commands'].append(row)
        save()
        started = time.monotonic()
        try:
            with (output / (label + '.stdout')).open('xb') as stdout, \
                    (output / (label + '.stderr')).open('xb') as stderr:
                result = subprocess.run(command, cwd=cwd, stdout=stdout, stderr=stderr,
                                        timeout=90, env=os.environ | {
                                            'ASAN_OPTIONS': 'detect_leaks=1',
                                            'UBSAN_OPTIONS': 'halt_on_error=1'})
            row['exit_code'] = result.returncode
            if result.returncode:
                raise RuntimeError(f'{label} failed; see preserved output')
        except Exception as exc:
            row['error'] = str(exc)
            raise
        finally:
            row['seconds'] = time.monotonic() - started
            save()
        return (output / (label + '.stdout')).read_bytes()

    def compile_fixture(label, text):
        path = output / (label + '.c')
        path.write_text(text)
        binary = output / (label + '.bin')
        run('compile-' + label, [cc, '-std=c11', '-O1', '-g', '-Wall', '-Wextra',
                                '-Wno-unused-function', '-Wno-unused-parameter',
                                '-Wno-misleading-indentation',
                                '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                                str(path), '-o', str(binary)])
        return binary

    save()
    try:
        for name, data in original.items():
            path = output / 'control' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        run('trace-patch-check', [git, 'apply', '--check', str(patches[0])],
            cwd=output / 'control')
        run('trace-patch', [git, 'apply', str(patches[0])], cwd=output / 'control')
        shutil.copytree(output / 'control', output / 'candidate')
        run('refresh-patch-check', [git, 'apply', '--check', str(patches[1])],
            cwd=output / 'candidate')
        run('refresh-patch', [git, 'apply', str(patches[1])], cwd=output / 'candidate')
        outputs = {}
        for variant in ['control', 'candidate']:
            code = (output / variant / 'hw/display/apple-gfx.c').read_text()
            functions = code[code.index('typedef struct AppleGFXRenderJob'):
                             code.index('static const GraphicHwOps apple_gfx_fb_ops')]
            binary = compile_fixture(variant, fixtures[0].read_text().replace(
                '/* RENDER_CODE */', functions))
            for trace in ['off', 'trace']:
                for value in (['0', '1'] if variant == 'candidate' else ['0']):
                    label = f'{variant}-{trace}-{value}'
                    outputs[label] = run(label, [str(binary), trace, value])
                    assert len(outputs[label].splitlines()) == 14, label
        reference = outputs['control-off-0']
        assert all(value == reference for name, value in outputs.items()
                   if name.endswith('-0'))
        assert outputs['candidate-off-1'] == outputs['candidate-trace-1']
        changed = [i for i, (left, right) in enumerate(zip(
            reference.splitlines(), outputs['candidate-off-1'].splitlines()))
                   if left != right]
        assert changed == [0, 12], changed
        code = (output / 'candidate/hw/display/apple-gfx.c').read_text()
        properties = code[code.index('static void apple_gfx_get_refresh'):
                          code.index('void apple_gfx_common_init')]
        binary = compile_fixture('property', fixtures[1].read_text().replace(
            '/* PROPERTY_CODE */', properties))
        run('property-check', [str(binary)])
        assert all((source / name).read_bytes() == data for name, data in original.items())
        report.update(status='passed', render_scenarios=14, render_variants=len(outputs),
                      default_outputs_unchanged=True, enabled_changed_scenarios=changed,
                      property_checks_passed=True, completed_unix=time.time())
        save()
    except BaseException as exc:
        report.update(status='failed', error=repr(exc))
        save()
        raise
    manifest = {str(p.relative_to(output)): {'bytes': p.stat().st_size,
                                            'sha256': digest(p.read_bytes())}
                for p in output.rglob('*') if p.is_file()}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'output': str(output),
                      'render_scenarios': 14, 'native_display_verified': False}))


if __name__ == '__main__':
    main()
