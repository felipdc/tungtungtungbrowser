#!/usr/bin/env python3
"""Compare actual QEMU display functions before/after the optional trace patch.

This uses synthetic renderer and event-queue callbacks. It never starts a VM,
loads firmware, invokes a GPU, or modifies the supplied QEMU source checkout.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = {
    'hw/display/apple-gfx.c': '867c0c3e0cc4947bc1f45e8a49dae61ca4fd86f0c0c3ef343fd61313ccc8e2a5',
    'hw/display/apple-gfx.h': '00a3f053eaecb4c0d139e9bf466c67c2d0013978219022d29c9e39a4ad67fcf0',
    'hw/display/trace-events': 'e47585eabc9e5e59ff5dd73723ba7cd072cf7f269e56fdc8f6e7be85b91910a5',
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu-source', type=Path, required=True,
                        help='Unmodified display sources from QEMU 14c9913d')
    parser.add_argument('--output', type=Path, required=True,
                        help='New evidence directory outside this repository')
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    source = args.qemu_source.resolve(strict=True)
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error('Evidence must be outside the source checkout')
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
    patch = ROOT / 'patches/qemu-display-trace.patch'
    fixture = ROOT / 'native/check_qemu_display_trace.c.in'
    os.umask(0o077)
    output.mkdir(mode=0o700)
    report = {
        'status': 'running', 'started_unix': time.time(),
        'source_revision': '14c9913d2231080bd234c69421ed4f293a25f767',
        'original_sources': dict(REFERENCE), 'patch_sha256': digest(patch.read_bytes()),
        'fixture_sha256': digest(fixture.read_bytes()), 'commands': [],
        'sanitizers': ['address', 'undefined'], 'native_display_verified': False,
        'browser_performance_verified': False, 'source_checkout_modified': False,
    }

    def save():
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(label, command, *, cwd=output, env=None):
        row = {'label': label, 'command': list(map(str, command))}
        report['commands'].append(row)
        save()
        started = time.monotonic()
        try:
            with (output / (label + '.stdout')).open('xb') as stdout, \
                    (output / (label + '.stderr')).open('xb') as stderr:
                process = subprocess.run(command, cwd=cwd, env=env, stdout=stdout,
                                         stderr=stderr, timeout=90, check=False)
            row['exit_code'] = process.returncode
            if process.returncode:
                raise RuntimeError(f'{label} failed; see preserved output')
        except Exception as exc:
            row['error'] = str(exc)
            raise
        finally:
            row['seconds'] = time.monotonic() - started
            save()
        return (output / (label + '.stdout')).read_bytes()

    save()
    try:
        for variant in ['before', 'after']:
            for name, data in original.items():
                p = output / variant / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
        run('patch-check', [git, 'apply', '--check', patch], cwd=output / 'after')
        run('patch-apply', [git, 'apply', patch], cwd=output / 'after')
        outputs = {}
        for variant in ['before', 'after']:
            text = (output / variant / 'hw/display/apple-gfx.c').read_text()
            start = 'typedef struct AppleGFXRenderJob'
            end = 'static const GraphicHwOps apple_gfx_fb_ops'
            if text.count(start) != 1 or text.count(end) != 1:
                raise RuntimeError('Rendering source boundaries are ambiguous')
            code = text[text.index(start):text.index(end)]
            p, binary = output / (variant + '.c'), output / variant / 'check'
            p.write_text(fixture.read_text().replace('/* RENDER_CODE */', code))
            run('compile-' + variant, [cc, '-std=c11', '-O1', '-g', '-Wall', '-Wextra',
                                      '-Wno-unused-function', '-Wno-misleading-indentation',
                                      '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                                      p, '-o', binary])
            modes = ['off'] if variant == 'before' else ['off', 'trace']
            for mode in modes:
                label = variant + '-' + mode
                outputs[label] = run(label, [binary, *(['trace'] if mode == 'trace' else [])],
                                     env=dict(os.environ, ASAN_OPTIONS='detect_leaks=1',
                                              UBSAN_OPTIONS='halt_on_error=1'))
                if (output / (label + '.stderr')).stat().st_size:
                    raise RuntimeError(f'{label} emitted diagnostics; see preserved stderr')
                if len(outputs[label].splitlines()) != 12:
                    raise RuntimeError(f'{label} did not complete twelve scenarios')
        if len(set(outputs.values())) != 1:
            raise RuntimeError('Instrumentation changed observable fixture behavior')
        for name, data in original.items():
            if (source / name).read_bytes() != data:
                raise RuntimeError('Source checkout changed during the check')
        report.update(status='passed', scenarios=12, variants=list(outputs),
                      behavior_outputs_identical=True, trace_order_and_copy_flags_asserted=True,
                      disabled_trace_clock_reads=0, finished_unix=time.time())
        save()
    except Exception as exc:
        report.update(status='failed', error=str(exc), finished_unix=time.time())
        save()
        raise
    print(json.dumps({k: report[k] for k in ['status', 'scenarios', 'variants',
                                           'behavior_outputs_identical', 'native_display_verified']}))


if __name__ == '__main__':
    main()
