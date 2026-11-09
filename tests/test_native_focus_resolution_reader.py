import copy
import unittest
from unittest.mock import Mock

from tungtungtungbrowser import NativeFocusReader, GuestError
from test_native_focus import SHA, TOKEN, native_result, answer


def resolved(*, address=False, unavailable=False):
    scan = {'complete': True, 'elements': 18, 'visited': 18, 'matches': 1, 'foreign': 12}
    attempts = []
    if unavailable:
        attempts.append({'error': -25216, 'seconds': .2, 'complete': False,
                         'elements': 0, 'visited': 0, 'matches': 0, 'foreign': 0})
    if address:
        attempts.append({'error': 0, 'seconds': .5, **scan})
    return {**native_result(), 'lookup': 'resolve', 'disabled_trait': 256, 'scan': scan,
            'resolution': {'source': 2 if address else 1, 'first_error': -25212 if address else 0,
                           'first_present': not address, 'first_seconds': .1,
                           'total_seconds': 1.2, 'attempts': attempts}}


class ResolutionReaderTests(unittest.TestCase):
    def read(self, result):
        guest = Mock()
        guest._rpc.return_value = answer(result)
        reader = NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)
        self.addCleanup(lambda: self.assertEqual(guest._rpc.call_count, 1))
        return reader.resolve_address_field(301, max_attempts=1), reader

    def test_first_responder_and_discovered_field_have_distinct_sources(self):
        for address, unavailable in [(False, False), (True, False), (True, True)]:
            with self.subTest(address=address, unavailable=unavailable):
                field, reader = self.read(resolved(address=address, unavailable=unavailable))
                self.assertEqual(field['source'], 'ax_address_field' if address else 'ax_first_responder')
                self.assertEqual(reader.last_resolution['status'], 'read')
                if address:
                    self.assertTrue(field['enabled'])

    def test_three_complete_empty_queries_return_absent_without_new_invocations(self):
        result = resolved(address=True)
        empty = {'complete': True, 'elements': 0, 'visited': 0, 'matches': 0, 'foreign': 0}
        result.update(element=None, scan=empty)
        result['resolution']['attempts'] = [{'error': 0, 'seconds': .1, **empty} for _ in range(3)]
        field, reader = self.read(result)
        self.assertIsNone(field)
        self.assertEqual(reader.last_resolution['status'], 'absent')

    def test_complete_nonempty_absence_can_precede_a_unique_discovery(self):
        result = resolved(address=True, unavailable=True)
        result['resolution']['attempts'][0] = {'error': 0, 'seconds': .2, 'complete': True,
            'elements': 24, 'visited': 24, 'matches': 0, 'foreign': 6}
        field, reader = self.read(result)
        self.assertEqual(field['source'], 'ax_address_field')
        self.assertEqual(reader.last_resolution['status'], 'read')
        result['resolution']['attempts'][0]['visited'] = 23
        with self.assertRaises(GuestError): self.read(result)

    def test_failed_partial_foreign_or_unrestored_results_are_rejected(self):
        valid = resolved(address=True, unavailable=True)
        cases = []
        for key, value in [('guard_restored', False), ('requested_pid', 302), ('error', -25216)]:
            result = copy.deepcopy(valid); result[key] = value; cases.append(result)
        result = copy.deepcopy(valid); result['element']['pid'] = 302; cases.append(result)
        result = copy.deepcopy(valid); result['after']['application'] = 1; cases.append(result)
        result = copy.deepcopy(valid); result['scan']['complete'] = False; cases.append(result)
        result = copy.deepcopy(valid); result['resolution']['attempts'][0]['elements'] = 1; cases.append(result)
        result = copy.deepcopy(valid); result['resolution']['attempts'][0]['error'] = -25212; cases.append(result)
        result = copy.deepcopy(valid); result['element']['attributes']['traits']['value'] |= 256; cases.append(result)
        for result in cases:
            with self.subTest(result=result), self.assertRaises(GuestError):
                self.read(result)

    def test_malformed_expired_or_contradictory_history_never_authorizes_field(self):
        cases = []
        for key, value in [('total_seconds', 10), ('total_seconds', float('nan')),
                           ('first_seconds', True), ('first_seconds', 2), ('source', True),
                           ('first_present', True), ('first_error', -25204), ('attempts', [])]:
            result = resolved(address=True); result['resolution'][key] = value; cases.append(result)
        result = resolved(address=True); result['resolution']['attempts'] *= 4; cases.append(result)
        result = resolved(address=True); result['resolution']['attempts'][0]['seconds'] = 2; cases.append(result)
        result = resolved(); result['resolution']['first_error'] = -25216; cases.append(result)
        result = resolved(); result['element'] = None; cases.append(result)
        result = resolved(address=True); result['scan']['matches'] = 2; cases.append(result)
        for result in cases:
            with self.subTest(result=result), self.assertRaises(GuestError):
                self.read(result)
