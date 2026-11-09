import unittest
from unittest.mock import Mock, patch

from tungtungtungbrowser import GuestError, NativeFocusReader
from test_native_focus import SHA, TOKEN, answer
from test_native_focus_resolution_reader import resolved


def expired():
    result = resolved(address=True)
    scan = {'complete': True, 'elements': 18, 'visited': 18, 'matches': 0, 'foreign': 0}
    result.update(error=-30, element=None, scan=scan)
    result['resolution'] = {
        'source': 2, 'first_error': -25216, 'first_present': False,
        'first_seconds': 7.613980, 'total_seconds': 10.027562,
        'attempts': [
            {'error': 0, 'seconds': .688458, **dict(scan, elements=28, visited=28)},
            {'error': 0, 'seconds': .913401, **scan},
        ],
    }
    return result


class BudgetRecoveryTests(unittest.TestCase):
    def reader(self, responses):
        guest = Mock()
        guest._rpc.side_effect = responses
        return guest, NativeFocusReader(guest, sha256=SHA, deployment_id=TOKEN)

    def test_expired_empty_result_is_discarded_before_one_fresh_complete_read(self):
        guest, reader = self.reader([answer(expired()), answer(resolved())])
        with patch('tungtungtungbrowser.native_focus.time.sleep'):
            field = reader.resolve_address_field(301)
        self.assertEqual(field['source'], 'ax_first_responder')
        self.assertEqual(guest._rpc.call_count, 2)
        self.assertEqual([r['status'] for r in reader.last_resolution_attempts], ['failed', 'read'])
        self.assertTrue(reader.last_resolution_attempts[0]['retry_permitted'])
        self.assertIsNone(reader.last_resolution_attempts[0]['native']['element'])
        self.assertTrue(all(c.args == ('script.eval',) for c in guest._rpc.call_args_list))

    def test_existing_limit_and_opt_out_are_preserved(self):
        for attempts in (1, 2):
            with self.subTest(attempts=attempts):
                guest, reader = self.reader([answer(expired())] * attempts)
                with patch('tungtungtungbrowser.native_focus.time.sleep'), self.assertRaises(GuestError):
                    reader.resolve_address_field(301, max_attempts=attempts)
                self.assertEqual(guest._rpc.call_count, attempts)
                self.assertTrue(all(r['status'] == 'failed' for r in reader.last_resolution_attempts))

    def test_partial_foreign_late_field_or_unrestored_results_cannot_authorize_retry(self):
        cases = []
        for key, value in [('requested_pid', 302), ('guard_restored', False),
                           ('error', 0), ('error', -25216), ('error', True),
                           ('element', resolved()['element']), ('disabled_trait', 3)]:
            result = expired(); result[key] = value; cases.append(result)
        result = expired(); result['after']['application'] = 1; cases.append(result)
        result = expired(); del result['element']; cases.append(result)
        for key, value in [('source', 1), ('first_present', True), ('first_error', -25204),
                           ('first_seconds', 10), ('total_seconds', 20),
                           ('total_seconds', float('nan')), ('attempts', [])]:
            result = expired(); result['resolution'][key] = value; cases.append(result)
        for key, value in [('complete', False), ('visited', 17), ('foreign', 1),
                           ('matches', 1), ('error', -25216), ('seconds', -1),
                           ('seconds', float('inf')), ('seconds', True), ('seconds', 4)]:
            result = expired(); result['resolution']['attempts'][-1][key] = value; cases.append(result)
        result = expired(); result['scan']['elements'] = True; cases.append(result)
        result = expired(); result['resolution']['attempts'][0]['extra'] = 1; cases.append(result)
        for result in cases:
            with self.subTest(result=result):
                guest, reader = self.reader([answer(result)])
                with self.assertRaises(GuestError): reader.resolve_address_field(301)
                guest._rpc.assert_called_once()
                self.assertFalse(reader.last_resolution['retry_permitted'])

    def test_second_failure_is_terminal_and_never_reuses_first_evidence(self):
        guest, reader = self.reader([answer(expired()), GuestError('missing second reply')])
        with patch('tungtungtungbrowser.native_focus.time.sleep'), self.assertRaisesRegex(GuestError, 'missing second reply'):
            reader.resolve_address_field(301)
        self.assertEqual(guest._rpc.call_count, 2)
        self.assertFalse(reader.last_resolution['retry_permitted'])

    def test_a_successful_late_scan_is_still_rejected_without_another_read(self):
        result = resolved(address=True)
        result['resolution']['total_seconds'] = 10.1
        guest, reader = self.reader([answer(result)])
        with self.assertRaises(GuestError): reader.resolve_address_field(301)
        guest._rpc.assert_called_once()
        self.assertFalse(reader.last_resolution['retry_permitted'])
