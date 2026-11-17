import unittest

from scripts.measure_ready import unlock_native_home
from tungtungtungbrowser.guest import GuestError, GuestTransportError


def state(locked=True, off=False, started=True):
    return {'source': 'darwin_notify', 'springboard_started': started,
            'locked': locked if started else None,
            'screen_off': off if started else None}


class NativeHomeUnlockTests(unittest.TestCase):
    def run_unlock(self, states, *, reply=None, deadline=5):
        self.now = 0
        self.buttons = []
        self.reads = 0
        pending = iter(states)
        last = None

        def read():
            nonlocal last
            self.reads += 1
            last = next(pending, last)
            if isinstance(last, Exception):
                raise last
            return last

        def press(name):
            self.buttons.append(name)
            if isinstance(reply, Exception):
                raise reply
            return reply if reply is not None else {'button': name}

        def sleep(seconds):
            self.now += seconds

        return unlock_native_home(read, press, deadline=deadline,
                                  clock=lambda: self.now, sleep=sleep)

    def test_lit_locked_screen_gets_one_home(self):
        result = self.run_unlock([state(), state(), state(False), state(False)])
        self.assertEqual(self.buttons, ['home'])
        self.assertFalse(result['locked'])
        self.assertFalse(result['visual_readiness_verified'])

    def test_dark_locked_screen_wakes_then_gets_one_home(self):
        self.run_unlock([state(off=True)] * 2 + [state()] * 2 + [state(False)] * 2)
        self.assertEqual(self.buttons, ['power', 'home'])

    def test_dark_unlocked_screen_only_wakes(self):
        self.run_unlock([state(False, True)] * 2 + [state(False)] * 2)
        self.assertEqual(self.buttons, ['power'])

    def test_already_unlocked_and_lit_sends_no_input(self):
        self.run_unlock([state(False)] * 2)
        self.assertEqual(self.buttons, [])

    def test_unstarted_notifications_cannot_authorize_input(self):
        with self.assertRaises(TimeoutError):
            self.run_unlock([state(started=False)], deadline=1)
        self.assertEqual(self.buttons, [])

    def test_changing_state_is_observed_before_choosing_action(self):
        self.run_unlock([state(), state(False), state(False)])
        self.assertEqual(self.buttons, [])

    def test_bad_notification_does_not_send_input(self):
        bad = state()
        bad['locked'] = 0
        with self.assertRaises(GuestError):
            self.run_unlock([bad])
        self.assertEqual(self.buttons, [])

    def test_failed_read_does_not_send_input(self):
        with self.assertRaises(GuestError):
            self.run_unlock([GuestError('read timed out')])
        self.assertEqual(self.buttons, [])

    def test_unknown_home_result_is_terminal_without_more_reads(self):
        failure = GuestTransportError('receive_headers', TimeoutError('timeout'))
        with self.assertRaises(GuestTransportError):
            self.run_unlock([state()] * 2, reply=failure)
        self.assertEqual(self.buttons, ['home'])
        self.assertEqual(self.reads, 2)

    def test_missing_acknowledgement_is_terminal(self):
        with self.assertRaises(GuestError):
            self.run_unlock([state()] * 2, reply={'button': 'power'})
        self.assertEqual(self.buttons, ['home'])

    def test_still_locked_after_home_never_repeats(self):
        with self.assertRaises(TimeoutError):
            self.run_unlock([state()], deadline=2)
        self.assertEqual(self.buttons, ['home'])

    def test_still_dark_after_power_never_sends_home(self):
        with self.assertRaises(TimeoutError):
            self.run_unlock([state(off=True)], deadline=2)
        self.assertEqual(self.buttons, ['power'])

    def test_expired_deadline_never_observes_or_sends(self):
        with self.assertRaises(TimeoutError):
            self.run_unlock([state()], deadline=0)
        self.assertEqual(self.reads, 0)
        self.assertEqual(self.buttons, [])

    def test_read_finishing_after_deadline_cannot_authorize_input(self):
        now = [0]
        buttons = []
        def slow_read():
            now[0] = 10
            return state()
        with self.assertRaises(TimeoutError):
            unlock_native_home(slow_read, buttons.append, deadline=5,
                               clock=lambda: now[0])
        self.assertEqual(buttons, [])


if __name__ == '__main__':
    unittest.main()
