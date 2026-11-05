"""Read native focus using a separately installed, verified guest helper.

The fixed administrative expression launches a native executable. It never
evaluates JavaScript in Safari or accepts a caller-supplied executable path.
"""
import json
import math
from pathlib import Path
import re

from .guest import GuestError


def helper_path(digest, deployment_id):
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Focus helper SHA-256 must be 64 lowercase hexadecimal characters')
    if not isinstance(deployment_id, str) or not re.fullmatch('[0-9a-f]{32}', deployment_id):
        raise ValueError('Focus deployment ID must be 32 lowercase hexadecimal characters')
    return '/private/var/root/ttt-focus-v1-' + digest + '-' + deployment_id


class NativeFocusReader:
    """Callable reader for Browser(address_field_reader=...).

    None means the native query completed and found no first responder. A failed
    query, wrong owner, malformed response, or un-restored switch is an error.
    """
    def __init__(self, guest, *, sha256, deployment_id):
        self.guest = guest
        self.path = helper_path(sha256, deployment_id)

    @classmethod
    def from_deployment(cls, guest, path):
        path = Path(path)
        if path.stat().st_size > 128 * 1024:
            raise ValueError('Focus deployment record exceeds 128 KiB')
        report = json.loads(path.read_text())
        if report.get('status') != 'complete':
            raise ValueError('Focus helper deployment did not complete')
        result = cls(guest, sha256=report['helper_sha256'], deployment_id=report['deployment_id'])
        if report.get('path') != result.path:
            raise ValueError('Focus helper path does not match its deployment identity')
        return result

    def _invoke(self, arguments):
        source = 'proc.run(' + json.dumps(self.path) + ', ' + json.dumps(arguments) + ', "")'
        answer = self.guest._rpc('script.eval', source=source, timeout=30)
        error = GuestError('Native focus helper did not complete successfully')
        error.native_script_response = answer
        if (not isinstance(answer, dict) or answer.get('ok') is not True
                or answer.get('crashed') is not False or answer.get('output') != ''
                or not isinstance(answer.get('result'), str) or len(answer['result']) > 65536):
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

    def check(self):
        result = self._invoke(['check'])
        if result != {'protocol': 1, 'status': 'ready', 'ui_input_sent': False, 'ax_queried': False}:
            raise GuestError('Native focus helper check failed')
        return result

    def __call__(self, pid):
        if type(pid) is not int or not 1 < pid <= 2147483647:
            raise ValueError('Focus query requires a valid native process ID')
        result = self._invoke(['pid', str(pid)])
        if (type(result.get('requested_pid')) is not int or result['requested_pid'] != pid
                or type(result.get('error')) is not int or result['error'] not in (0, -25212)):
            error = GuestError(f"Native focused-element query failed (AX error {result.get('error')!r})")
            error.native_helper_response = result
            raise error
        before, after = result.get('before'), result.get('after')
        if (not isinstance(before, dict) or set(before) != {'application', 'automation'}
                or any(type(v) is not int or v not in (0, 1) for v in before.values())
                or not isinstance(after, dict) or set(after) != set(before)
                or any(type(v) is not int or v not in (0, 1) for v in after.values())
                or before != after):
            raise GuestError('Native focus query did not verify restored accessibility switches')
        if 'element' not in result:
            raise GuestError('Native focus query omitted its result')
        element = result['element']
        if element is None:
            # AXRuntime can represent no first responder either as a null
            # successful value or kAXErrorNoValue. Other errors stay failures.
            return None
        if result['error'] != 0:
            raise GuestError('Native focus query returned an element with an error')
        if (not isinstance(element, dict) or type(element.get('pid')) is not int or element['pid'] != pid
                or any(type(element.get(k)) is not int or element[k] != 0
                       for k in ('pid_error', 'timeout_error'))):
            raise GuestError('Cannot verify focused-element ownership')
        attributes = element.get('attributes')
        if not isinstance(attributes, dict) or set(attributes) != {'label', 'value', 'identifier', 'traits', 'frame'}:
            raise GuestError('Native focused-element attributes are incomplete')
        if any(not isinstance(v, dict) or type(v.get('error')) is not int or v['error'] != 0
               or 'value' not in v for v in attributes.values()):
            raise GuestError('Native focused-element attribute read failed')
        field = {k: v['value'] for k, v in attributes.items()}
        if (any(not isinstance(field[k], str) for k in ('label', 'identifier'))
                or not isinstance(field['value'], (str, int, float)) or isinstance(field['value'], bool)
                or (isinstance(field['value'], (int, float)) and not math.isfinite(field['value']))
                or type(field['traits']) is not int or field['traits'] < 0):
            raise GuestError('Native focused-element attributes have invalid types')
        frame = field['frame']
        if (not isinstance(frame, dict) or set(frame) != {'x', 'y', 'width', 'height'}
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                       for v in frame.values()) or frame['width'] < 0 or frame['height'] < 0):
            raise GuestError('Native focused element has invalid geometry')
        return {**field, 'pid': pid, 'source': 'ax_first_responder'}
