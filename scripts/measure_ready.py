#!/usr/bin/env python3
"""Measure native Safari visibility from a recorded launch, not just QEMU liveness.

Run in the same PID namespace as scripts/run_vm.py. Web navigation is measured
separately with capture; foreground Safari alone does not prove network access.
"""
import argparse
import base64
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tungtungtungbrowser.guest import Guest, GuestError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('vm', type=Path)
    parser.add_argument('--deadline-seconds', type=float, default=600)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    launch = json.loads((args.vm / 'launch.json').read_text())
    if launch['host_boot_id'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
        parser.error('Launch record is from an earlier host boot')
    if not 0 < args.deadline_seconds <= 3600:
        parser.error('deadline-seconds must be between 0 and 3600')
    start = launch['started_monotonic']
    deadline = start + args.deadline_seconds
    result = {'launch': launch, 'navigation_verified': False,
              'safari_foreground_verified': False, 'screenshot_captured': False,
              'visual_review': 'pending'}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def alive():
        stat = Path(f"/proc/{launch['pid']}/stat").read_text().rsplit(')', 1)[1].split()
        if stat[0] in {'Z', 'X'} or stat[19] != launch['pid_start_ticks']:
            raise RuntimeError('Recorded PID no longer runs this VM bundle')

    def retry(label, operation, timeout=60, retry_timeouts=False):
        last_error = None
        while time.monotonic() < deadline:
            alive()
            try:
                remaining = deadline - time.monotonic()
                guest = Guest(launch['cid'], timeout=min(timeout, max(0.1, remaining)))
                value = operation(guest)
                elapsed = time.monotonic() - start
                result[label + '_seconds'] = elapsed
                print(json.dumps({'stage': label, 'seconds': elapsed}), flush=True)
                return value
            except GuestError as exc:
                last_error = str(exc)
                # A socket timeout does not cancel the
                # guest operation; retrying could accumulate overlapping work.
                if 'timed out' in last_error and not retry_timeouts:
                    raise GuestError(f'{label}: {last_error}; not retrying an operation that may still be running') from exc
                print(json.dumps({'stage': label, 'retry_error': last_error}), flush=True)
                time.sleep(min(5, max(0, deadline - time.monotonic())))
        raise TimeoutError(f'{label} deadline exceeded: {last_error}')

    def unlock(guest):
        value = guest.call('screen.unlock', timeout=60)
        if value.get('locked') is not False:
            raise GuestError('Screen remains locked')
        return value

    def safari(guest):
        foreground = guest.call('apps.foreground')
        if foreground.get('bundle_id') != 'com.apple.mobilesafari' or foreground.get('verified') is not True:
            raise GuestError('Safari is not the frontmost app')
        return foreground

    def screenshot(guest):
        value = guest.call('screen.screenshot')
        if value.get('mime_type') != 'image/jpeg' or not value.get('data'):
            raise GuestError('Screenshot missing')
        jpeg = base64.b64decode(value['data'], validate=True)
        if not jpeg.startswith(b'\xff\xd8') or not jpeg.endswith(b'\xff\xd9'):
            raise GuestError('Screenshot is not framed as JPEG')
        args.output.with_suffix('.jpg').write_bytes(jpeg)

    try:
        retry('vphoned_healthy', lambda guest: guest.health(), timeout=5, retry_timeouts=True)
        retry('screen_unlocked', unlock, timeout=90)
        # Request launch once. Foreground polling must not relaunch Safari or
        # rescan its running state for every poll. Native launch can be slow on TCG.
        alive()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Safari launch deadline exceeded')
        Guest(launch['cid'], timeout=min(120, remaining)).call(
            'apps.launch', bundle_id='com.apple.mobilesafari')
        result['safari_launch_request_seconds'] = time.monotonic() - start
        retry('safari_foreground', safari)
        result['safari_foreground_verified'] = True
        retry('safari_capture', screenshot)
        # Native foreground identity plus a valid JPEG still requires inspection
        # for launch screens, dialogs or a blank render before claiming readiness.
        result['screenshot_captured'] = True
    except Exception as exc:
        result['error'] = str(exc)
        raise
    finally:
        result['elapsed_seconds'] = time.monotonic() - start
        args.output.write_text(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
