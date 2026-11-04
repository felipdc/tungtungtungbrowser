#!/usr/bin/env python3
"""Record one native Safari -> Home -> Safari transition without retrying input.

Run in the VM launcher's PID namespace after inspecting a loaded Safari page.
Completion verifies process continuity and Safari foreground state. Inspect the
saved screenshots separately; neither completion nor a live process proves
that a page rendered, that Home was visible, or that network access works.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tungtungtungbrowser import Browser
from tungtungtungbrowser.guest import Guest

SAFARI = 'com.apple.mobilesafari'


def safari_identity(value):
    if not isinstance(value, dict) or not isinstance(value.get('processes'), list):
        raise ValueError('Invalid native process inventory')
    rows = [row for row in value['processes'] if isinstance(row, dict)
            and isinstance(row.get('executable'), str)
            and row['executable'].endswith('/MobileSafari.app/MobileSafari')]
    if len(rows) != 1:
        raise ValueError('Expected exactly one Safari executable')
    row = rows[0]
    pid, start = row.get('pid'), row.get('start_time')
    if (row.get('bundle_id') != SAFARI or type(pid) is not int or pid <= 0
            or isinstance(start, bool) or not isinstance(start, (int, float))
            or not math.isfinite(start) or start <= 0):
        raise ValueError('Safari process identity is incomplete')
    return {'pid': pid, 'start_time': start}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('vm', type=Path)
    parser.add_argument('--output', type=Path, required=True, help='New evidence directory')
    parser.add_argument('--qmp-socket', type=Path, required=True)
    parser.add_argument('--home-seconds', type=float, default=15)
    parser.add_argument('--return-seconds', type=float, default=5)
    parser.add_argument('--timeout', type=float, default=120, help='Timeout per native request')
    args = parser.parse_args(argv)
    for name in ('home_seconds', 'return_seconds'):
        value = getattr(args, name)
        if not math.isfinite(value) or not 0 <= value <= 60:
            parser.error(f'{name.replace("_", "-")} must be between 0 and 60')
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 300:
        parser.error('timeout must be greater than 0 and at most 300')
    launch = json.loads((args.vm / 'launch.json').read_text())
    if launch['host_boot_id'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
        parser.error('Launch record is from another host boot')
    if (type(launch.get('pid')) is not int or launch['pid'] <= 0
            or type(launch.get('cid')) is not int or launch['cid'] <= 2
            or isinstance(launch.get('started_monotonic'), bool)
            or not isinstance(launch.get('started_monotonic'), (int, float))
            or not math.isfinite(launch['started_monotonic'])
            or not 0 <= launch['started_monotonic'] <= time.monotonic()):
        parser.error('Invalid VM launch identity or start time')

    def alive():
        stat = Path(f"/proc/{launch['pid']}/stat").read_text().rsplit(')', 1)[1].split()
        if stat[0] in {'Z', 'X'} or stat[19] != launch['pid_start_ticks']:
            raise RuntimeError('Recorded VM process is no longer running')

    alive()
    args.output.mkdir(parents=True, exist_ok=False)
    report = args.output / 'report.json'
    result = {'status': 'running', 'stage': 'starting', 'launch': launch,
              'calls': [], 'screenshots': {}, 'native_input_retried': False,
              'page_scripts_injected': False, 'navigation_verified': False,
              'home_screen_verified': False, 'visual_review': 'pending',
              'home_seconds': args.home_seconds, 'return_seconds': args.return_seconds,
              'timeout_per_request': args.timeout}

    def save():
        result['elapsed_since_launch_seconds'] = time.monotonic() - launch['started_monotonic']
        with tempfile.NamedTemporaryFile(mode='w', dir=args.output, delete=False) as file:
            temporary = Path(file.name)
            try:
                json.dump(result, file, indent=2, allow_nan=False)
                file.close()
                temporary.replace(report)
            finally:
                temporary.unlink(missing_ok=True)

    class RecordedGuest(Guest):
        def _rpc(self, method, **params):
            alive()
            row = {'method': method, 'params': params, 'started_monotonic': time.monotonic()}
            result['calls'].append(row)
            save()
            try:
                value = super()._rpc(method, **params)
                # Reject non-JSON values before they can make the failure
                # report itself unwritable.
                json.dumps(value, allow_nan=False)
                row['result'] = value
                return value
            except Exception as error:
                row['error'] = str(error)
                raise
            finally:
                row['finished_monotonic'] = time.monotonic()
                save()

    guest = RecordedGuest(launch['cid'], timeout=args.timeout)
    browser = Browser(guest=guest, qmp_socket=args.qmp_socket)

    def stage(name):
        result['stage'] = name
        save()
        alive()

    def capture(name):
        shot = browser.screenshot(args.output / (name + '.png'))
        result['screenshots'][name] = shot.metadata()
        save()

    def identity(name):
        value = guest._rpc('processes.list', filter='MobileSafari')
        result[name] = safari_identity(value)
        save()
        return result[name]

    save()
    try:
        stage('initial_foreground')
        foreground = guest.call('apps.foreground')
        if foreground.get('bundle_id') != SAFARI or foreground.get('verified') is not True:
            raise RuntimeError('Safari must already be verified in the foreground')
        before = identity('before_identity')
        if type(foreground.get('pid')) is not int or foreground['pid'] != before['pid']:
            raise RuntimeError('Foreground identity disagrees with the Safari process')
        capture('before')
        stage('home_requested')
        result['home_response'] = browser.home()
        save()
        time.sleep(args.home_seconds)
        stage('home_observation')
        capture('home')
        if identity('home_identity') != before:
            raise RuntimeError('Safari process changed after Home; no launch requested')
        stage('safari_launch_requested')
        result['launch_response'] = browser.launch()
        save()
        if result['launch_response'].get('frontmost_verified') is not True:
            raise RuntimeError('Safari foreground was not verified after launch')
        if result['launch_response']['pid'] != before['pid']:
            raise RuntimeError('Launch returned a different Safari process')
        time.sleep(args.return_seconds)
        stage('return_observation')
        capture('returned')
        if identity('returned_identity') != before:
            raise RuntimeError('Safari process changed during return')
        result.update(status='complete', stage='complete', process_continuity_verified=True)
        save()
        return 0
    except Exception as error:
        result.update(status='failed', error=str(error))
        save()
        try:
            alive()
            capture('failure')
        except Exception as capture_error:
            result['failure_capture_error'] = str(capture_error)
            save()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
