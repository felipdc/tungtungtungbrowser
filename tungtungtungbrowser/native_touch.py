"""Fixed native helper invocation through the daemon's separate admin process.

The administrative JavaScriptCore expression starts a native program. It does
not execute in Safari and accepts no caller-supplied script or executable path.
"""
import json
import math
import re

from .guest import GuestError, contact_hold_ms


def helper_path(sha256):
    if not isinstance(sha256, str) or not re.fullmatch('[0-9a-f]{64}', sha256):
        raise ValueError('Touch helper SHA-256 must be 64 lowercase hexadecimal characters')
    return '/private/var/root/ttt-touch-v1-' + sha256


def invoke(guest, sha256, arguments):
    # Both callers below build arguments from a fixed operation and validated
    # numbers. JSON serialization is for the admin expression, never a shell.
    source = 'proc.run(' + json.dumps(helper_path(sha256)) + ', ' + json.dumps(arguments) + ', "")'
    answer = guest._rpc('script.eval', source=source, timeout=30)
    error = GuestError('Native touch helper did not complete successfully; input is not repeated')
    error.native_script_response = answer
    if (not isinstance(answer, dict) or answer.get('ok') is not True
            or answer.get('crashed') is not False or answer.get('output') != ''
            or not isinstance(answer.get('result'), str) or len(answer['result']) > 16384):
        raise error
    try:
        process = json.loads(answer['result'])
        error.native_process_response = process
        if (not isinstance(process, dict) or type(process.get('status')) is not int
                or process['status'] != 0 or process.get('signal', 0) is not None
                or process.get('stderr') != '' or not isinstance(process.get('stdout'), str)):
            raise error
        result = json.loads(process['stdout'])
        if not isinstance(result, dict) or type(result.get('protocol')) is not int or result['protocol'] != 1:
            raise error
        return result
    except (ValueError, TypeError) as exc:
        raise error from exc


def check_helper(guest, sha256):
    result = invoke(guest, sha256, ['check'])
    if result != {'protocol': 1, 'status': 'ready', 'dispatch_count': 0}:
        error = GuestError('Native touch helper check failed; no tap requested')
        error.native_helper_response = result
        raise error
    return result


def calibrated_tap(guest, sha256, x, y, *, hold_ms=50):
    helper_path(sha256)
    contact_hold_ms(hold_ms)
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
               and math.isfinite(v) and v >= 0 for v in (x, y)):
        raise ValueError('Tap coordinates must be finite, nonnegative screen points')
    screen = guest.call('device.screen')
    expected = {'width': 393, 'height': 852, 'scale': 3, 'orientation': 0,
                'locked': False, 'screen_off': False}
    if (not isinstance(screen, dict)
            or any(type(screen.get(k)) is not type(v) or screen[k] != v for k, v in expected.items())):
        raise GuestError('Calibrated tap requires the verified 393 × 852 @3 unlocked portrait display; no input sent')
    if x >= screen['width'] or y >= screen['height']:
        raise ValueError('Tap coordinates must be inside the verified display')
    # A five-point contact in the tested portrait digitizer space. This is
    # independent of the framework field-mapping repair.
    nx, ny, radius = x / screen['width'], y / screen['height'], 5 / screen['height']
    result = invoke(guest, sha256, ['tap', repr(nx), repr(ny), repr(radius), str(hold_ms)])
    numbers = {'normalized_x': nx, 'normalized_y': ny, 'normalized_radius': radius,
               'requested_hold_ms': hold_ms}
    if (result.get('status') != 'dispatched' or type(result.get('dispatch_count')) is not int
            or result['dispatch_count'] != 2 or result.get('release_dispatched') is not True
            or result.get('hold_completed') is not True or result.get('page_result_verified') is not False
            or any(not isinstance(result.get(k), (int, float)) or isinstance(result[k], bool)
                   or not math.isfinite(result[k]) or not math.isclose(result[k], v, rel_tol=0, abs_tol=1e-12)
                   for k, v in numbers.items())):
        error = GuestError('Invalid calibrated tap result; input is not repeated')
        error.native_helper_response = result
        raise error
    return {'x': x, 'y': y, 'method': 'native_calibrated_helper', 'helper_sha256': sha256,
            'radius_points': 5, 'screen': screen, 'native': result,
            'page_result_verified': False}
