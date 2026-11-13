#!/usr/bin/env python3
"""Measure native Safari visibility from a recorded launch, not just QEMU liveness.

Run in the same PID namespace as scripts/run_vm.py. Web navigation is measured
separately with capture; foreground Safari alone does not prove network access.
"""
import argparse
import ipaddress
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tungtungtungbrowser.guest import Guest, GuestError, GuestHTTPError, GuestTransportError, web_url
from tungtungtungbrowser import Browser


def usable_guest_ip(value):
    """An assigned address is a prerequisite, not a connectivity check."""
    if not isinstance(value, str) or '%' in value:
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return not (address.is_unspecified or address.is_loopback
                or address.is_link_local or address.is_multicast
                or str(address) == '255.255.255.255')


def write_report(path, result, *, exclusive=False):
    """Publish complete JSON; initial creation must not expose an empty file."""
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent,
                                     prefix=path.name + '.', delete=False) as report:
        temporary = Path(report.name)
        try:
            json.dump(result, report, indent=2, allow_nan=False)
            report.close()
            if exclusive:
                # Same-filesystem link is atomic and refuses an existing name.
                # An exists-then-replace check could overwrite another observer.
                os.link(temporary, path)
            else:
                temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('vm', type=Path)
    parser.add_argument('--deadline-seconds', type=float, default=600)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--qmp-socket', type=Path, help='Capture the rendered display as PNG instead of guest JPEG')
    parser.add_argument('--sync-clock', action='store_true',
                        help='Offer host time once before unlock; requires automatic guest time')
    parser.add_argument('--initial-url',
                        help='Experimental: open one HTTP(S) URL in Safari instead of apps.launch; '
                             'foreground and rendering still require separate observations')
    parser.add_argument('--wait-for-guest-ip', action='store_true',
                        help='Experimental: wait for an assigned guest IP before native input; '
                             'does not verify routing or Safari page readiness')
    parser.add_argument('--bounded-foreground', action='store_true',
                        help='Experimental: observe foreground through a native session that '
                             'the daemon stops at its deadline; never repeats browser input')
    args = parser.parse_args(argv)
    if args.initial_url is not None:
        try:
            web_url(args.initial_url)
        except ValueError as exc:
            parser.error(str(exc))
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
              'visual_review': 'pending', 'status': 'running', 'stage': 'starting',
              'once_only_requests': []}
    if args.bounded_foreground:
        result.update(foreground_mode='bounded_native_session', foreground_observations=[],
                      foreground_transport_timeout_ceiling_seconds=120)
    if args.initial_url is not None:
        result.update(initial_url=args.initial_url, startup_method='native_open_url',
                      initial_url_content_verified=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    screenshot_path = args.output.with_suffix('.png' if args.qmp_socket else '.jpg')
    failure_path = args.output.with_suffix('.failure.png')
    for path in (screenshot_path, failure_path):
        if path.exists() or path.is_symlink():
            parser.error(f'Output already exists: {path}')
    # Claim the report before making guest requests. A second observer must
    # choose another filename, and must not replace earlier evidence.
    write_report(args.output, result, exclusive=True)

    def checkpoint():
        result['elapsed_seconds'] = time.monotonic() - start
        write_report(args.output, result)

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

    def request_once(method, timeout, operation):
        # Publish before sending. A timeout ends the host's wait, not the native
        # operation. Keep that uncertainty and its original timing in the report.
        started = time.monotonic()
        request = {'method': method, 'status': 'in_flight',
                   'started_seconds': started - start, 'timeout_seconds': timeout}
        result['once_only_requests'].append(request)
        checkpoint()
        try:
            value = operation(Guest(launch['cid'], timeout=timeout))
        except Exception as exc:
            request.update(status='error', error=str(exc))
            if isinstance(exc, GuestTransportError):
                request['transport'] = {
                    'phase': exc.phase, 'errno': exc.errno,
                    'request_may_have_been_sent': exc.request_may_have_been_sent,
                }
            raise
        else:
            # This records the reply, not foreground, render or page readiness.
            request['status'] = 'response_received'
            return value
        finally:
            finished = time.monotonic()
            request.update(finished_seconds=finished - start,
                           duration_seconds=finished - started)
            checkpoint()

    def wait_for_guest_ip():
        initial = result['vphoned_health_response']
        observations = result['guest_ip_observations'] = []

        def observe(guest):
            first = not observations
            row = {'source': 'initial_health' if first else 'health_poll',
                   'started_seconds': time.monotonic() - start}
            observations.append(row)
            checkpoint()
            try:
                response = initial if first else guest.health()
                row['response'] = response
                instance = initial.get('instance')
                if instance is not None and response.get('instance') != instance:
                    raise RuntimeError('Guest daemon instance changed while waiting for an IP')
                if time.monotonic() >= deadline:
                    raise TimeoutError('Guest IP readiness deadline exceeded')
                if not usable_guest_ip(response.get('ip')):
                    raise GuestError('Guest health has no usable assigned IP address')
                return response
            except Exception as exc:
                row['error'] = str(exc)
                raise
            finally:
                row['finished_seconds'] = time.monotonic() - start
                checkpoint()

        # Only read-only health observations are retried. Keep the initial
        # response, each failed sample and the original launch deadline.
        result['guest_ip_ready_response'] = retry(
            'guest_ip_ready', observe, timeout=5, retry_timeouts=True)
        checkpoint()

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
            value = request_once('screen.unlock', min(90, remaining),
                                 lambda guest: guest.call('screen.unlock', timeout=60))
            if value.get('locked') is not False:
                raise GuestError('Screen remains locked')
        except GuestError as exc:
            raise GuestError(f'screen_unlocked: {exc}; not retrying native input automatically') from exc
        result['screen_unlocked_seconds'] = time.monotonic() - start
        checkpoint()
        print(json.dumps({'stage': 'screen_unlocked',
                          'seconds': result['screen_unlocked_seconds']}), flush=True)

    def safari(guest, *, reconcile_refusal=False):
        if args.bounded_foreground:
            # The pinned daemon kills and reaps the native session before its
            # explicit deadline response. A lost transport response provides
            # no such proof: stop rather than overlap another foreground scan.
            remaining = deadline - time.monotonic()
            if remaining <= 11:
                raise TimeoutError('Insufficient startup budget for a bounded foreground read')
            native_timeout = min(45, remaining - 10)
            observation = {'started_unix': time.time(),
                           'native_timeout_seconds': native_timeout,
                           'native_stop_confirmed': False}
            result['foreground_observations'].append(observation)
            checkpoint()
            try:
                reply = guest._rpc('script.eval', source='api("apps.foreground")',
                                   timeout=native_timeout)
                if (not isinstance(reply, dict) or reply.get('ok') is not True
                        or reply.get('crashed') is not False or reply.get('output') != ''
                        or not isinstance(reply.get('result'), str)):
                    raise RuntimeError('Invalid bounded foreground session response')
                try:
                    foreground = json.loads(reply['result'])
                except ValueError as exc:
                    raise RuntimeError('Invalid bounded foreground result') from exc
                if (not isinstance(foreground, dict)
                        or not isinstance(foreground.get('bundle_id'), str)
                        or type(foreground.get('verified')) is not bool
                        or type(foreground.get('pid')) is not int
                        or not 0 <= foreground['pid'] <= 2147483647):
                    raise RuntimeError('Invalid bounded foreground identity')
                if (foreground['verified'] is True
                        and foreground['bundle_id'] == 'com.apple.mobilesafari'
                        and foreground['pid'] <= 1):
                    raise RuntimeError('Bounded foreground Safari process is not verified')
                observation['response'] = foreground
            except GuestHTTPError as exc:
                observation['error'] = str(exc)
                expected = (f'The script did not finish within {int(native_timeout)} '
                            'seconds and was stopped')
                if exc.status == 400 and exc.detail == expected:
                    observation['native_stop_confirmed'] = True
                    # This is a new read after confirmed native cancellation,
                    # within the original startup deadline. No input is replayed.
                    raise GuestError('Foreground observation reached its native deadline and was stopped') from exc
                raise RuntimeError('Bounded foreground session failed without confirmed cancellation') from exc
            except GuestError as exc:
                observation['error'] = str(exc)
                if isinstance(exc, GuestTransportError):
                    observation['transport'] = {
                        'phase': exc.phase, 'errno': exc.errno,
                        'request_may_have_been_sent': exc.request_may_have_been_sent,
                    }
                raise RuntimeError('Bounded foreground exchange failed; no new read is safe') from exc
            except RuntimeError as exc:
                observation['error'] = str(exc)
                raise
            finally:
                observation['finished_unix'] = time.time()
                checkpoint()
            if time.monotonic() >= deadline:
                raise TimeoutError('Bounded foreground exceeded the original startup deadline')
        else:
            foreground = guest.call('apps.foreground')
        if foreground.get('bundle_id') != 'com.apple.mobilesafari' or foreground.get('verified') is not True:
            raise GuestError('Safari is not the frontmost app')
        if reconcile_refusal:
            if type(foreground.get('pid')) is not int or not 1 < foreground['pid'] <= 2147483647:
                raise GuestError('Safari foreground process is not verified')
            if time.monotonic() >= deadline:
                raise TimeoutError('Safari foreground reconciliation deadline exceeded')
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
        result['vphoned_health_response'] = retry(
            'vphoned_healthy', lambda guest: guest.health(), timeout=5, retry_timeouts=True)
        # Keep the already-requested identity/address snapshot before input.
        # A guest IP is useful evidence, not proof of Safari network readiness.
        checkpoint()
        if args.wait_for_guest_ip:
            wait_for_guest_ip()
        if args.sync_clock:
            result['stage'] = 'clock_sync'
            checkpoint()
            alive()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Clock synchronization deadline exceeded')
            result['clock_sync_response'] = request_once(
                'time.clock', min(30, remaining), lambda guest: guest.sync_clock())
            checkpoint()
            if result['clock_sync_response']['automatic'] is not True:
                raise GuestError('Clock synchronization requires automatic guest time; inspect the guest clock settings')
            result['clock_sync_seconds'] = time.monotonic() - start
            checkpoint()
        unlock_once()
        # Select the startup operation before sending it. A failed URL open
        # must never fall back to a second open or an application launch.
        result['stage'] = 'safari_open_url' if args.initial_url is not None else 'safari_launch'
        checkpoint()
        alive()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Safari launch deadline exceeded')
        if args.initial_url is not None:
            result['initial_url_response'] = request_once(
                'apps.open_url', min(120, remaining), lambda guest: guest.open(args.initial_url))
            result['initial_url_ack_seconds'] = time.monotonic() - start
            checkpoint()
            # URL opening acknowledges an asynchronous operation. It supplies
            # neither foreground proof nor page-load completion.
            result['safari_foreground_source'] = 'apps.foreground'
            result['safari_foreground_response'] = retry(
                'safari_foreground', safari, timeout=120 if args.bounded_foreground else 60)
        else:
            try:
                result['safari_launch_response'] = request_once(
                    'apps.launch', min(120, remaining),
                    lambda guest: guest.call('apps.launch', bundle_id='com.apple.mobilesafari'))
            except GuestHTTPError as exc:
                if exc.status != 400 or exc.detail != 'launch refused: com.apple.mobilesafari':
                    raise
                # This complete native rejection can accompany an already
                # foreground Safari. Preserve the failed request and reconcile
                # only its postcondition; never send another launch or unlock.
                result['safari_launch_error'] = {'status': exc.status, 'detail': exc.detail}
            result['safari_launch_request_seconds'] = time.monotonic() - start
            checkpoint()
            if result.get('safari_launch_response', {}).get('frontmost_verified') is True:
                # Native launch already checks foreground identity. Avoid a
                # duplicate installed/running-app scan under TCG.
                result['safari_foreground_source'] = 'apps.launch'
                result['safari_foreground_seconds'] = result['safari_launch_request_seconds']
            else:
                result['safari_foreground_source'] = 'apps.foreground'
                result['safari_foreground_response'] = retry(
                    'safari_foreground', lambda guest: safari(
                        guest, reconcile_refusal='safari_launch_error' in result),
                    timeout=120 if args.bounded_foreground else 60)
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
