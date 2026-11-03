#!/usr/bin/env python3
"""Measure native Safari visibility from a recorded launch, not just QEMU liveness.

Run in the same PID namespace as scripts/run_vm.py. Web navigation is measured
separately with capture; foreground Safari alone does not prove network access.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tungtungtungbrowser.guest import Guest, GuestError
from tungtungtungbrowser import Browser


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('vm', type=Path)
    parser.add_argument('--deadline-seconds', type=float, default=600)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--qmp-socket', type=Path, help='Capture the rendered display as PNG instead of guest JPEG')
    parser.add_argument('--sync-clock', action='store_true',
                        help='Offer host time once before unlock; requires automatic guest time')
    args = parser.parse_args(argv)
    if args.output.suffix != '.json':
        parser.error('output must have a .json suffix')
    launch = json.loads((args.vm / 'launch.json').read_text())
    if launch['host_boot_id'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
        parser.error('Launch record is from an earlier host boot')
    if not 0 < args.deadline_seconds <= 3600:
        parser.error('deadline-seconds must be between 0 and 3600')
    maintenance = launch.get('clock_maintenance', {})
    if not isinstance(maintenance, dict):
        parser.error('Invalid launcher clock configuration')
    managed_clock = maintenance.get('enabled') is True
    if managed_clock and args.sync_clock:
        parser.error('The VM launcher manages time; omit --sync-clock')
    start = launch['started_monotonic']
    deadline = start + args.deadline_seconds
    result = {'launch': launch, 'navigation_verified': False,
              'safari_foreground_verified': False, 'screenshot_captured': False,
              'visual_review': 'pending', 'status': 'running', 'stage': 'starting'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    screenshot_path = args.output.with_suffix('.png' if args.qmp_socket else '.jpg')
    failure_path = args.output.with_suffix('.failure.png')
    for path in (screenshot_path, failure_path):
        if path.exists() or path.is_symlink():
            parser.error(f'Output already exists: {path}')
    # Claim the report before making guest requests. A second observer must
    # choose another filename, and must not replace earlier evidence.
    with args.output.open('x') as report:
        json.dump(result, report, indent=2)

    def checkpoint():
        result['elapsed_seconds'] = time.monotonic() - start
        with tempfile.NamedTemporaryFile(mode='w', dir=args.output.parent,
                                         prefix=args.output.name + '.', delete=False) as report:
            temporary = Path(report.name)
            try:
                json.dump(result, report, indent=2)
                report.close()
                temporary.replace(args.output)
            finally:
                temporary.unlink(missing_ok=True)

    def alive():
        stat = Path(f"/proc/{launch['pid']}/stat").read_text().rsplit(')', 1)[1].split()
        if stat[0] in {'Z', 'X'} or stat[19] != launch['pid_start_ticks']:
            raise RuntimeError('Recorded PID no longer runs this VM bundle')

    def retry(label, operation, timeout=60, retry_timeouts=False):
        result['stage'] = label
        checkpoint()
        last_error = None
        while time.monotonic() < deadline:
            alive()
            try:
                remaining = deadline - time.monotonic()
                guest = Guest(launch['cid'], timeout=min(timeout, max(0.1, remaining)))
                value = operation(guest)
                elapsed = time.monotonic() - start
                result[label + '_seconds'] = elapsed
                checkpoint()
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

    def unlock_once():
        # A reset or malformed reply can occur after the guest received input,
        # just as a timeout can. Do not repeat this state-changing operation on
        # any failure; retain the stage and capture the display for inspection.
        result['stage'] = 'screen_unlocked'
        checkpoint()
        alive()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Screen unlock deadline exceeded')
        try:
            unlock(Guest(launch['cid'], timeout=min(90, remaining)))
        except GuestError as exc:
            raise GuestError(f'screen_unlocked: {exc}; not retrying native input automatically') from exc
        result['screen_unlocked_seconds'] = time.monotonic() - start
        checkpoint()
        print(json.dumps({'stage': 'screen_unlocked',
                          'seconds': result['screen_unlocked_seconds']}), flush=True)

    def safari(guest):
        foreground = guest.call('apps.foreground')
        if foreground.get('bundle_id') != 'com.apple.mobilesafari' or foreground.get('verified') is not True:
            raise GuestError('Safari is not the frontmost app')
        return foreground

    def screenshot(guest):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Safari capture deadline exceeded')
        shot = Browser(guest=guest, qmp_socket=args.qmp_socket,
                       display_timeout=min(15, remaining)).screenshot()
        shot.save(screenshot_path)
        result['screenshot'] = shot.metadata()

    def wait_for_managed_clock():
        # Initial correction can move guest wall time by many minutes. Observe
        # the launcher's own result before sending input or launching apps.
        # This observer does not send another health or time request while waiting.
        result['stage'] = 'managed_clock_ready'
        checkpoint()
        path = Path(maintenance['state_file'])
        if path.parent.resolve() != args.vm.resolve():
            raise GuestError('Managed clock state must belong to this VM bundle')
        while time.monotonic() < deadline:
            alive()
            state = json.loads(path.read_text())
            if state.get('launch_started_unix') != launch['started_unix']:
                raise GuestError('Managed clock state belongs to another launch')
            if state != result.get('managed_clock_state'):
                result['managed_clock_state'] = state
                checkpoint()
            status = state.get('status')
            if status == 'waiting_for_interval':
                response = state.get('last_response')
                if (not isinstance(response, dict)
                        or response.get('automatic') is not True
                        or type(response.get('changed')) is not bool
                        or not all(isinstance(response.get(key), (int, float))
                                   and not isinstance(response[key], bool)
                                   and math.isfinite(response[key])
                                   for key in ('time', 'offset'))):
                    raise GuestError('Managed clock has no valid automatic-time response')
                result['managed_clock_ready_seconds'] = time.monotonic() - start
                checkpoint()
                return
            if status not in {'prepared', 'waiting_for_guest', 'offering_time'}:
                raise GuestError(f'Managed clock is {status}: inspect its retained state before input')
            time.sleep(min(1, max(0, deadline - time.monotonic())))
        raise TimeoutError('Managed clock readiness deadline exceeded')

    try:
        if managed_clock:
            wait_for_managed_clock()
        retry('vphoned_healthy', lambda guest: guest.health(), timeout=5, retry_timeouts=True)
        if args.sync_clock:
            result['stage'] = 'clock_sync'
            checkpoint()
            alive()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Clock synchronization deadline exceeded')
            result['clock_sync_response'] = Guest(launch['cid'], timeout=min(30, remaining)).sync_clock()
            checkpoint()
            if result['clock_sync_response']['automatic'] is not True:
                raise GuestError('Clock synchronization requires automatic guest time; inspect the guest clock settings')
            result['clock_sync_seconds'] = time.monotonic() - start
            checkpoint()
        unlock_once()
        # Request launch once. Foreground polling must not relaunch Safari or
        # rescan its running state for every poll. Native launch can be slow on TCG.
        result['stage'] = 'safari_launch'
        checkpoint()
        alive()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Safari launch deadline exceeded')
        result['safari_launch_response'] = Guest(launch['cid'], timeout=min(120, remaining)).call(
            'apps.launch', bundle_id='com.apple.mobilesafari')
        result['safari_launch_request_seconds'] = time.monotonic() - start
        checkpoint()
        if result['safari_launch_response'].get('frontmost_verified') is True:
            # The pinned native launch method already checks foreground identity.
            # Repeating apps.foreground also scans installed/running apps and can
            # add substantial work under TCG. Keep the original observation time.
            result['safari_foreground_source'] = 'apps.launch'
            result['safari_foreground_seconds'] = result['safari_launch_request_seconds']
        else:
            result['safari_foreground_source'] = 'apps.foreground'
            result['safari_foreground_response'] = retry('safari_foreground', safari)
        result['safari_foreground_verified'] = True
        retry('safari_capture', screenshot)
        # Native foreground identity plus a valid image still requires inspection
        # for launch screens, dialogs or a blank render before claiming readiness.
        result['screenshot_captured'] = True
        result['status'] = 'complete'
        result['stage'] = 'complete'
    except Exception as exc:
        result['status'] = 'failed'
        result['error'] = str(exc)
        checkpoint()
        # Do not queue guest-side work after a timed-out native request. QMP
        # can capture the display independently, even after the boot deadline.
        # Keep this diagnostic separate from successful readiness evidence.
        if args.qmp_socket:
            try:
                alive()
                shot = Browser(qmp_socket=args.qmp_socket, display_timeout=5).screenshot()
                shot.save(failure_path)
                result['failure_screenshot'] = {
                    'path': str(failure_path), 'metadata': shot.metadata(),
                    'elapsed_seconds': time.monotonic() - start,
                }
            except Exception as capture_error:
                result['failure_screenshot_error'] = str(capture_error)
        raise
    finally:
        checkpoint()


if __name__ == '__main__':
    main()
