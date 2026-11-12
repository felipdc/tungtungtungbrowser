import unittest
from unittest.mock import Mock, patch
from tungtungtungbrowser import NativeFocusReader, GuestError
from test_native_focus import SHA, TOKEN, answer
from test_native_focus_resolution_reader import resolved


def absent(*, unavailable=False):
    result = resolved(address=True)
    scan = {'complete': not unavailable, 'elements': 0 if unavailable else 24,
            'visited': 0 if unavailable else 24, 'matches': 0, 'foreign': 0}
    error = -25216 if unavailable else 0
    result.update(element=None, error=error, scan=scan)
    result['resolution']['attempts'] = [{'error': error, 'seconds': .3, **scan}]
    return result


class ResolutionRecoveryTests(unittest.TestCase):
    def test_completed_absence_and_restored_unavailable_can_get_one_fresh_read(self):
        for unavailable in (False, True):
            with self.subTest(unavailable=unavailable):
                guest=Mock();guest._rpc.side_effect=[answer(absent(unavailable=unavailable)),answer(resolved(address=True))]
                reader=NativeFocusReader(guest,sha256=SHA,deployment_id=TOKEN)
                with patch('tungtungtungbrowser.native_focus.time.sleep'):
                    field=reader.resolve_address_field(301)
                self.assertEqual(field['source'],'ax_address_field')
                self.assertEqual(guest._rpc.call_count,2)
                self.assertEqual(len(reader.last_resolution_attempts),2)
                self.assertTrue(reader.last_resolution_attempts[0]['retry_permitted'])
                self.assertEqual(reader.last_resolution_attempts[1]['status'],'read')
                for c in guest._rpc.call_args_list:
                    self.assertEqual(c.args,('script.eval',))
                    self.assertIn('["resolve", "301"]',c.kwargs['source'])

    def test_exhaustion_and_opt_out_preserve_absence_or_terminal_error(self):
        for unavailable in (False, True):
            for attempts in (1,2):
                with self.subTest(unavailable=unavailable, attempts=attempts):
                    guest=Mock();guest._rpc.return_value=answer(absent(unavailable=unavailable))
                    reader=NativeFocusReader(guest,sha256=SHA,deployment_id=TOKEN)
                    with patch('tungtungtungbrowser.native_focus.time.sleep'):
                        if unavailable:
                            with self.assertRaises(GuestError):reader.resolve_address_field(301,max_attempts=attempts)
                        else:self.assertIsNone(reader.resolve_address_field(301,max_attempts=attempts))
                    self.assertEqual(guest._rpc.call_count,attempts)
                    self.assertEqual(len(reader.last_resolution_attempts),attempts)

    def test_unrestored_foreign_expired_and_transport_failures_are_not_repeated(self):
        cases=[]
        for key,value in [('requested_pid',302),('guard_restored',False),('before',{})]:
            result=absent(unavailable=True);result[key]=value;cases.append(answer(result))
        result=absent(unavailable=True);result['scan'].update(elements=24,visited=1,foreign=1)
        result['resolution']['attempts'][0].update(elements=24,visited=1,foreign=1);cases.append(answer(result))
        result=absent();result['resolution']['total_seconds']=10;cases.append(answer(result))
        result=absent();result['resolution']['attempts'][0]['matches']=1;cases.append(answer(result))
        result=absent();result['after']=dict(result['after'],automation=0);cases.append(answer(result))
        cases.extend([GuestError('Missing transport reply'),{'ok':False}])
        for response in cases:
            with self.subTest(response=response):
                guest=Mock()
                if isinstance(response,Exception):guest._rpc.side_effect=response
                else:guest._rpc.return_value=response
                reader=NativeFocusReader(guest,sha256=SHA,deployment_id=TOKEN)
                with self.assertRaises(GuestError):reader.resolve_address_field(301)
                guest._rpc.assert_called_once()
                self.assertFalse(reader.last_resolution['retry_permitted'])

    def test_existing_responder_and_invalid_limit_do_not_authorize_discovery_retry(self):
        guest=Mock();guest._rpc.return_value=answer(resolved())
        reader=NativeFocusReader(guest,sha256=SHA,deployment_id=TOKEN)
        self.assertEqual(reader.resolve_address_field(301)['source'],'ax_first_responder')
        guest._rpc.assert_called_once()
        guest.reset_mock()
        for invalid in (0,4,True,1.0):
            with self.assertRaises(ValueError):reader.resolve_address_field(301,max_attempts=invalid)
        guest._rpc.assert_not_called()
