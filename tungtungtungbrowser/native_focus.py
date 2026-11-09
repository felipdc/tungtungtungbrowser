"""Read native focus using a separately installed, verified guest helper.

The fixed administrative expression launches a native executable. It never
evaluates JavaScript in Safari or accepts a caller-supplied executable path.
"""
import json
import math
from pathlib import Path
import re
import time

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
    def __init__(self, guest, *, sha256, deployment_id, max_read_attempts=2):
        if type(max_read_attempts) is not int or not 1 <= max_read_attempts <= 3:
            raise ValueError('max_read_attempts must be an integer between 1 and 3')
        self.guest = guest
        self.path = helper_path(sha256, deployment_id)
        self.max_read_attempts = max_read_attempts
        self.last_read_attempts = []
        self.last_address_lookup = None
        self.last_address_attempts = []
        self.last_resolution = None
        self.last_resolution_attempts = []
        self.supports_address_resolution = False

    @classmethod
    def from_deployment(cls, guest, path, *, max_read_attempts=2):
        path = Path(path)
        if path.stat().st_size > 128 * 1024:
            raise ValueError('Focus deployment record exceeds 128 KiB')
        report = json.loads(path.read_text())
        if report.get('status') != 'complete':
            raise ValueError('Focus helper deployment did not complete')
        result = cls(guest, sha256=report['helper_sha256'], deployment_id=report['deployment_id'],
                     max_read_attempts=max_read_attempts)
        if report.get('path') != result.path:
            raise ValueError('Focus helper path does not match its deployment identity')
        if 'native_check' in report:
            result._accept_check(report['native_check'])
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

    def _accept_check(self, result):
        expected = {'protocol': 1, 'status': 'ready', 'ui_input_sent': False, 'ax_queried': False}
        if (not isinstance(result, dict) or type(result.get('protocol')) is not int
                or result.get('ui_input_sent') is not False or result.get('ax_queried') is not False
                or result not in (expected, {**expected, 'address_resolution': 'guarded-v1'})):
            raise GuestError('Native focus helper returned invalid capability evidence')
        self.supports_address_resolution = result.get('address_resolution') == 'guarded-v1'
        return result

    def check(self):
        return self._accept_check(self._invoke(['check']))

    def __call__(self, pid):
        if type(pid) is not int or not 1 < pid <= 2147483647:
            raise ValueError('Focus query requires a valid native process ID')
        self.last_read_attempts = []
        for attempt in range(self.max_read_attempts):
            record = {'attempt': attempt + 1}
            self.last_read_attempts.append(record)
            started = time.monotonic()
            try:
                field = self._read_once(pid)
            except GuestError as error:
                record['status'] = 'failed'
                result = getattr(error, 'native_helper_response', {})
                record['ax_error'] = result.get('error')
                # Only a completed read with verified restoration may be read
                # again. A timeout, process failure or missing state cannot
                # establish restoration. This repeats no key, tap or paste.
                if not getattr(error, 'retryable_focus_read', False) or attempt + 1 == self.max_read_attempts:
                    raise
            else:
                record['status'] = 'absent' if field is None else 'read'
                return field
            finally:
                record['seconds'] = time.monotonic() - started
            time.sleep(0.1)

    def _read_once(self, pid):
        result = self._invoke(['pid', str(pid)])
        return self._read_result(result, pid, 'ax_first_responder')

    def find_address_field(self, pid):
        """Find one native URL field; this does not establish keyboard focus."""
        if type(pid) is not int or not 1 < pid <= 2147483647:
            raise ValueError('Address lookup requires a valid native process ID')
        record = self.last_address_lookup = {'attempt': 1, 'status': 'failed'}
        self.last_address_attempts = [record]
        started = time.monotonic()
        try:
            result = self._invoke(['address', str(pid)])
            field = self._address_result(result, pid, 'address')
            record['scan'] = result['scan']
            record['status'] = 'absent' if field is None else 'read'
            return field
        finally:
            record['seconds'] = time.monotonic() - started

    def _address_result(self, result, pid, lookup):
        field = self._read_result(result, pid, 'ax_address_field')
        scan, disabled = result.get('scan'), result.get('disabled_trait')
        if (result.get('lookup') != lookup or not isinstance(scan, dict)
                or set(scan) != {'complete', 'elements', 'visited', 'matches', 'foreign'}
                or scan['complete'] is not True
                or any(type(scan[k]) is not int for k in ('elements', 'visited', 'matches', 'foreign'))
                or not 0 <= scan['elements'] <= 512 or scan['visited'] != scan['elements']
                or not 0 <= scan['foreign'] <= scan['elements']
                or scan['matches'] > scan['elements'] - scan['foreign']
                or scan['matches'] != (0 if field is None else 1)
                or type(disabled) is not int or not 0 < disabled < 2**64
                or disabled & (disabled - 1)):
            raise GuestError('Native address lookup did not establish a complete unique result')
        if field is not None:
            if field['identifier'] not in ('URL', 'TabBarItemTitle') or field['traits'] & disabled:
                raise GuestError('Native address lookup did not return an enabled URL field')
            field['enabled'] = True
        return field

    def resolve_address_field(self, pid, *, max_attempts=2):
        """Allow one fresh query after verified restored absence/unavailability."""
        if type(pid) is not int or not 1 < pid <= 2147483647:
            raise ValueError('Focus resolution requires a valid native process ID')
        if type(max_attempts) is not int or not 1 <= max_attempts <= 2:
            raise ValueError('Resolution max_attempts must be 1 or 2')
        self.last_resolution_attempts = []
        for attempt in range(max_attempts):
            try:
                field = self._resolve_address_field_once(pid)
            except GuestError:
                if not self.last_resolution.get('retry_permitted') or attempt + 1 == max_attempts:
                    raise
            else:
                if field is not None or not self.last_resolution.get('retry_permitted') or attempt + 1 == max_attempts:
                    return field
            time.sleep(0.1)

    def _resolve_address_field_once(self, pid):
        record = self.last_resolution = {'status': 'failed', 'retry_permitted': False, 'attempt': len(self.last_resolution_attempts) + 1}
        self.last_resolution_attempts.append(record)
        started = time.monotonic()
        try:
            result = self._invoke(['resolve', str(pid)])
            record['native'] = result
            trace = result.get('resolution')
            if (result.get('lookup') != 'resolve' or not isinstance(trace, dict)
                    or set(trace) != {'source', 'first_error', 'first_present', 'first_seconds', 'total_seconds', 'attempts'}
                    or type(trace['source']) is not int or trace['source'] not in (1, 2)
                    or type(trace['first_error']) is not int or type(trace['first_present']) is not bool
                    or any(type(trace[k]) not in (int, float) or not math.isfinite(trace[k])
                           or trace[k] < 0 for k in ('first_seconds', 'total_seconds'))
                    or trace['first_seconds'] > trace['total_seconds'] or trace['total_seconds'] >= 10
                    or not isinstance(trace['attempts'], list) or len(trace['attempts']) > 3):
                raise GuestError('Native focus resolution has invalid or expired query evidence')
            if trace['source'] == 1:
                if (trace['attempts'] or trace['first_error'] != result.get('error')
                        or (not trace['first_present'] and trace['first_error'] in (0, -25212, -25216))):
                    raise GuestError('Native focus resolution did not establish its responder source')
                try:
                    field = self._read_result(result, pid, 'ax_first_responder')
                except GuestError as error:
                    # A completed first-responder -25215 has been observed with
                    # no element and full restoration. Discard it and allow one
                    # new guarded query; no key or tap is repeated by this reader.
                    scan = result.get('scan')
                    empty = {'complete': False, 'elements': 0, 'visited': 0, 'matches': 0, 'foreign': 0}
                    if (getattr(error, 'native_helper_response', None) is result
                            and result['error'] == -25215 and result['element'] is None
                            and trace['first_present'] is False
                            and trace['first_seconds'] == trace['total_seconds']
                            and isinstance(scan, dict) and scan == empty
                            and type(scan['complete']) is bool
                            and all(type(scan[k]) is int for k in ('elements', 'visited', 'matches', 'foreign'))):
                        record['retry_permitted'] = True
                    elif (getattr(error, 'native_helper_response', None) is result
                            and getattr(error, 'retryable_focus_attributes', False) is True
                            and trace['first_present'] is True and trace['first_error'] == 0
                            and trace['first_seconds'] == trace['total_seconds']
                            and isinstance(scan, dict) and scan == empty
                            and type(scan['complete']) is bool
                            and all(type(scan[k]) is int for k in ('elements', 'visited', 'matches', 'foreign'))):
                        # The responder exists but its identity attributes were
                        # unavailable. Discard every attribute, including its
                        # frame; only a new complete query can authorize input.
                        record['retry_permitted'] = True
                    raise
                if field is None:
                    raise GuestError('Native focus resolution omitted its reported responder')
            else:
                if trace['first_present'] or trace['first_error'] not in (0, -25212, -25216) or not trace['attempts']:
                    raise GuestError('Native focus resolution did not justify address discovery')
                for index, attempt in enumerate(trace['attempts']):
                    if (not isinstance(attempt, dict) or set(attempt) != {'error', 'seconds', 'complete', 'elements', 'visited', 'matches', 'foreign'}
                            or type(attempt['error']) is not int or type(attempt['complete']) is not bool
                            or type(attempt['seconds']) not in (int, float) or not math.isfinite(attempt['seconds']) or attempt['seconds'] < 0
                            or any(type(attempt[k]) is not int for k in ('elements', 'visited', 'matches', 'foreign'))
                            or not 0 <= attempt['foreign'] <= attempt['visited'] <= attempt['elements'] <= 512
                            or not 0 <= attempt['matches'] <= attempt['visited'] - attempt['foreign']):
                        raise GuestError('Native focus resolution has invalid scan evidence')
                    if index + 1 < len(trace['attempts']):
                        absent = (attempt['error'] == 0 and attempt['complete']
                                  and attempt['visited'] == attempt['elements'] and attempt['matches'] == 0)
                        unavailable = (attempt['error'] == -25216 and not attempt['complete']
                                       and not any(attempt[k] for k in ('elements', 'visited', 'matches', 'foreign')))
                        if not (absent or unavailable):
                            raise GuestError('Native focus resolution repeated an ineligible scan')
                last = trace['attempts'][-1]
                if trace['first_seconds'] + sum(a['seconds'] for a in trace['attempts']) > trace['total_seconds'] + 0.001:
                    raise GuestError('Native focus resolution durations exceed the guarded interval')
                if result.get('scan') != {k: last[k] for k in ('complete', 'elements', 'visited', 'matches', 'foreign')} or result.get('error') != last['error']:
                    raise GuestError('Native focus resolution final scan does not match its history')
                try:
                    field = self._address_result(result, pid, 'resolve')
                except GuestError as error:
                    # _read_result attaches native evidence only after verifying
                    # the PID, element presence, and original switch restoration.
                    # A failed process, missing reply, or malformed trace cannot
                    # authorize another read. Discard eligible partial evidence;
                    # input still requires a fresh complete result.
                    unavailable = {'complete': False, 'elements': 0, 'visited': 0, 'matches': 0, 'foreign': 0}
                    scan = result['scan']
                    disabled = result.get('disabled_trait')
                    partial_unavailable = (
                        scan['complete'] is False
                        and all(type(scan[k]) is int for k in ('elements', 'visited', 'matches', 'foreign'))
                        and 0 <= scan['visited'] < scan['elements'] <= 512
                        and scan['matches'] == 0 and scan['foreign'] == 0
                        and type(disabled) is int and 0 < disabled < 2**64
                        and not disabled & (disabled - 1))
                    if (getattr(error, 'native_helper_response', None) is result
                            and result['error'] == -25216 and result['element'] is None
                            and (scan == unavailable or partial_unavailable)):
                        record['retry_permitted'] = True
                    raise
                else:
                    # The address parser proved a complete zero-match scan,
                    # matching PID and fully restored switches before None.
                    record['retry_permitted'] = field is None

            record['status'] = 'absent' if field is None else 'read'
            return field
        finally:
            record['seconds'] = time.monotonic() - started

    def _read_result(self, result, pid, source):
        if (type(result.get('requested_pid')) is not int or result['requested_pid'] != pid
                or type(result.get('error')) is not int):
            raise GuestError('Native focused-element response has an invalid process or error code')
        before, after = result.get('before'), result.get('after')
        if (not isinstance(before, dict) or set(before) != {'application', 'automation'}
                or any(type(v) is not int or v not in (0, 1) for v in before.values())
                or not isinstance(after, dict) or set(after) != set(before)
                or any(type(v) is not int or v not in (0, 1) for v in after.values())
                or before != after or result.get('guard_restored') is not True):
            raise GuestError('Native focus query did not verify restored accessibility switches')
        if 'element' not in result:
            raise GuestError('Native focus query omitted its result')
        element = result['element']
        if result['error'] not in (0, -25212):
            error = GuestError(f"Native focused-element query failed (AX error {result['error']!r})")
            error.native_helper_response = result
            error.retryable_focus_read = source == 'ax_first_responder' and result['error'] == -25216 and element is None
            raise error
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
            error = GuestError('Native focused-element attribute read failed')
            frame = attributes.get('frame')
            geometry = frame.get('value') if isinstance(frame, dict) else None
            unavailable = all(isinstance(attributes[k], dict)
                              and set(attributes[k]) == {'error', 'value'}
                              and type(attributes[k]['error']) is int
                              and attributes[k]['error'] == -25216
                              and attributes[k]['value'] is None
                              for k in ('label', 'value', 'identifier', 'traits'))
            missing_names = (
                all(isinstance(attributes[k], dict)
                    and set(attributes[k]) == {'error', 'value'}
                    and type(attributes[k]['error']) is int
                    and attributes[k]['error'] == -25212
                    and attributes[k]['value'] is None for k in ('label', 'identifier'))
                and all(isinstance(attributes[k], dict)
                        and set(attributes[k]) == {'error', 'value'}
                        and type(attributes[k]['error']) is int
                        and attributes[k]['error'] == 0 for k in ('value', 'traits'))
                and isinstance(attributes['value']['value'], str) and bool(attributes['value']['value'])
                and type(attributes['traits']['value']) is int
                and 0 < attributes['traits']['value'] < 2**64)
            if (source == 'ax_first_responder' and (unavailable or missing_names)
                    and isinstance(frame, dict) and set(frame) == {'error', 'value'}
                    and type(frame['error']) is int and frame['error'] == 0
                    and isinstance(geometry, dict) and set(geometry) == {'x', 'y', 'width', 'height'}
                    and all(type(v) in (int, float) and math.isfinite(v) for v in geometry.values())
                    and geometry['width'] > 0 and geometry['height'] > 0):
                # Ownership and restoration were verified above. This exact
                # completed failures permit discarding the responder and reading
                # again in resolve mode; it never yields a partially read field.
                error.native_helper_response = result
                error.retryable_focus_attributes = True
            raise error
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
        return {**field, 'pid': pid, 'source': source}
