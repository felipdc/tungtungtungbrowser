"""Fixed native navigation key invocation; no website scripting or replay."""
import json
import re

from .guest import GuestError

KEYS = {'cmd+a': 4, 'cmd+l': 15, 'cmd+t': 23, 'cmd+w': 26, 'return': 40}


def helper_path(sha256):
    if not isinstance(sha256, str) or not re.fullmatch('[0-9a-f]{64}', sha256):
        raise ValueError('Keyboard helper SHA-256 must be 64 lowercase hexadecimal characters')
    return '/private/var/root/ttt-keyboard-v1-' + sha256


def invoke(guest, sha256, arguments):
    source = 'proc.run(' + json.dumps(helper_path(sha256)) + ', ' + json.dumps(arguments) + ', "")'
    answer = guest._rpc('script.eval', source=source, timeout=30)
    error = GuestError('Native keyboard helper did not complete successfully; input is not repeated')
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
    if (result != {'protocol': 1, 'status': 'ready', 'dispatch_count': 0}
            or type(result.get('dispatch_count')) is not int):
        error = GuestError('Native keyboard helper check failed; no key requested')
        error.native_helper_response = result
        raise error
    return result


def press_key(guest, sha256, name):
    helper_path(sha256)
    if not isinstance(name, str) or name not in KEYS:
        raise ValueError('Native navigation keys support cmd+a, cmd+l, cmd+t, cmd+w and return')
    result = invoke(guest, sha256, ['press', name])
    expected = [(7, KEYS[name], 1), (7, KEYS[name], 0)]
    if name.startswith('cmd+'):
        expected = [(7, 227, 1), *expected, (7, 227, 0)]
    events = result.get('events')
    valid_events = isinstance(events, list) and len(events) == len(expected)
    timestamps = []
    if valid_events:
        for event, identity in zip(events, expected):
            if (not isinstance(event, dict)
                    or any(type(event.get(k)) is not int for k in
                           ('usage_page', 'usage', 'down', 'before_ns', 'after_ns'))
                    or tuple(event[k] for k in ('usage_page', 'usage', 'down')) != identity):
                valid_events = False
                break
            timestamps.extend((event['before_ns'], event['after_ns']))
    if (result.get('status') != 'dispatched' or result.get('key') != name
            or type(result.get('dispatch_count')) is not int or result['dispatch_count'] != len(expected)
            or result.get('release_dispatched') is not True
            or result.get('post_release_wait_completed') is not True
            or result.get('application_consumption_verified') is not False
            or not valid_events or any(t <= 0 or t >= 2**64 for t in timestamps)
            or timestamps != sorted(timestamps)):
        error = GuestError('Invalid native keyboard result; input is not repeated')
        error.native_helper_response = result
        raise error
    return {'key': name, 'dispatched': True, 'method': 'native_keyboard_helper',
            'helper_sha256': sha256, 'native': result, 'application_consumption_verified': False}
