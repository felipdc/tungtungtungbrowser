"""Readiness failures retain useful evidence without repeating native actions."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from scripts import measure_ready
from tungtungtungbrowser.guest import GuestError, GuestTransportError


class SavedShot:
    def save(self, path):
        with Path(path).open('xb') as output:
            output.write(b'captured display')

    def metadata(self):
        return {'source': 'test_display'}


class ReadinessTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.vm = Path(self.directory.name)
        self.output = self.vm / 'readiness.json'
        self.launch = {
            'pid': os.getpid(),
            'pid_start_ticks': Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19],
            'host_boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'started_monotonic': time.monotonic() - 10,
            'cid': 3,
        }
        (self.vm / 'launch.json').write_text(json.dumps(self.launch))
        self.guest = Mock()
        self.guest.health.return_value = {'status': 'ok'}
        self.guest.call.side_effect = self.native
        self.guest.open.side_effect = lambda url: self.native('apps.open_url', url=url)
        self.calls = []
        self.failure_method = None
        self.launch_response = {'accepted': True}
        self.capture = Mock(return_value=SavedShot())
        self.factory = self.enterContext(patch.object(measure_ready, 'Guest', return_value=self.guest))
        self.browser = self.enterContext(patch.object(measure_ready, 'Browser'))
        self.browser.return_value.screenshot = self.capture
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def native(self, method, **params):
        # Check the report while the request is in flight, not just at exit.
        self.calls.append((method, json.loads(self.output.read_text())['stage']))
        if method == self.failure_method:
            raise GuestError('vphoned transport failed: timed out')
        return {
            'screen.unlock': {'locked': False},
            'apps.launch': self.launch_response,
            'apps.open_url': {'opened': True},
            'apps.foreground': {'bundle_id': 'com.apple.mobilesafari', 'verified': True},
        }[method]

    def run_measurement(self, *, qmp=True, sync_clock=False, initial_url=None):
        argv = [str(self.vm), '--output', str(self.output)]
        if initial_url is not None:
            argv += ['--initial-url', initial_url]
        if sync_clock:
            argv += ['--sync-clock']
        if qmp:
            argv += ['--qmp-socket', str(self.vm / 'control.sock')]
        measure_ready.main(argv)

    def report(self):
        return json.loads(self.output.read_text())

    def test_initial_url_opens_once_and_keeps_content_unverified(self):
        url = 'https://example.org/first?run=1'
        def opened(value):
            self.assertEqual(value, url)
            request = self.report()['once_only_requests'][-1]
            self.assertEqual(request['method'], 'apps.open_url')
            self.assertEqual(request['status'], 'in_flight')
            self.assertNotIn('finished_seconds', request)
            return self.native('apps.open_url', url=value)
        self.guest.open.side_effect = opened
        self.run_measurement(initial_url=url)
        report = self.report()
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['initial_url'], url)
        self.assertEqual(report['startup_method'], 'native_open_url')
        self.assertFalse(report['navigation_verified'])
        self.assertFalse(report['initial_url_content_verified'])
        self.assertEqual(report['visual_review'], 'pending')
        self.assertEqual(report['safari_foreground_source'], 'apps.foreground')
        self.assertNotIn('safari_launch_response', report)
        self.assertEqual(self.calls, [('screen.unlock', 'screen_unlocked'),
                                     ('apps.open_url', 'safari_open_url'),
                                     ('apps.foreground', 'safari_foreground')])
        self.assertEqual([r['method'] for r in report['once_only_requests']],
                         ['screen.unlock', 'apps.open_url'])
        self.guest.open.assert_called_once_with(url)

    def test_initial_url_failure_never_retries_or_falls_back_to_launch(self):
        for phase in ['connect', 'send_request', 'receive_headers', 'read_body']:
            with self.subTest(phase=phase):
                self.output = self.vm / (phase + '.json')
                self.calls.clear()
                self.guest.open.reset_mock()
                self.guest.open.side_effect = GuestTransportError(phase, TimeoutError('timed out'))
                with self.assertRaises(GuestTransportError):
                    self.run_measurement(initial_url='https://example.org/')
                self.guest.open.assert_called_once()
                self.assertEqual(self.calls, [('screen.unlock', 'screen_unlocked')])
                report = self.report()
                self.assertEqual(report['stage'], 'safari_open_url')
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['once_only_requests'][-1]['transport']['phase'], phase)
                self.assertNotIn('initial_url_ack_seconds', report)
                self.assertTrue(self.output.with_suffix('.failure.png').exists())

    def test_initial_url_ack_does_not_hide_foreground_timeout(self):
        self.failure_method = 'apps.foreground'
        with self.assertRaises(GuestError):
            self.run_measurement(initial_url='https://example.org/')
        self.assertEqual([method for method, _ in self.calls],
                         ['screen.unlock', 'apps.open_url', 'apps.foreground'])
        report = self.report()
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['safari_foreground_verified'])
        self.assertFalse(report['initial_url_content_verified'])
        self.assertIn('initial_url_ack_seconds', report)
        self.guest.open.assert_called_once()

    def test_initial_url_unlock_failure_prevents_open(self):
        self.failure_method = 'screen.unlock'
        with self.assertRaises(GuestError):
            self.run_measurement(initial_url='https://example.org/')
        self.guest.open.assert_not_called()
        self.assertEqual([method for method, _ in self.calls], ['screen.unlock'])

    def test_health_identity_and_address_survive_later_input_failure(self):
        health = {'status': 'ok', 'instance': 'original-guest-instance', 'ip': '10.0.2.15'}
        self.guest.health.return_value = health

        def unlock(method, **params):
            self.assertEqual(method, 'screen.unlock')
            self.assertEqual(self.report()['vphoned_health_response'], health)
            raise GuestTransportError('receive_headers', TimeoutError('timed out'))

        self.guest.call.side_effect = unlock
        with self.assertRaises(GuestError):
            self.run_measurement(initial_url='https://example.org/')
        self.guest.health.assert_called_once_with()
        self.guest.open.assert_not_called()
        report = self.report()
        self.assertEqual(report['vphoned_health_response'], health)
        self.assertFalse(report['navigation_verified'])
        self.assertFalse(report['initial_url_content_verified'])
        self.assertEqual(report['stage'], 'screen_unlocked')

    def test_invalid_initial_url_is_rejected_before_evidence_or_requests(self):
        for url in ['javascript:1', 'https://user:secret@example.org/',
                    'https://example.org:99999/', 'https://example.org/\n']:
            with self.subTest(url=url), self.assertRaises(SystemExit):
                self.run_measurement(initial_url=url)
        self.factory.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_initial_url_respects_existing_evidence_before_input(self):
        self.output.write_text('original failed result')
        with self.assertRaises(FileExistsError):
            self.run_measurement(initial_url='https://example.org/')
        self.assertEqual(self.output.read_text(), 'original failed result')
        self.factory.assert_not_called()

    def managed_clock(self, status, **fields):
        self.launch['started_unix'] = 1800000000.0
        path = self.vm / 'clock-current.json'
        self.launch['clock_maintenance'] = {'enabled': True, 'state_file': str(path)}
        (self.vm / 'launch.json').write_text(json.dumps(self.launch))
        path.write_text(json.dumps({'status': status,
                                    'launch_started_unix': self.launch['started_unix'], **fields}))
        return path

    def test_launcher_correction_finishes_before_any_readiness_guest_requests(self):
        path = self.managed_clock('waiting_for_guest')
        response = {'time': 1800000000.0, 'automatic': True, 'changed': True, 'offset': 2088.0}
        phases = iter(['offering_time', 'waiting_for_interval'])

        def advance(seconds):
            self.factory.assert_not_called()
            self.assertEqual(self.calls, [])
            self.assertEqual(self.report()['stage'], 'managed_clock_ready')
            state = json.loads(path.read_text())
            state['status'] = next(phases)
            if state['status'] == 'waiting_for_interval':
                state['last_response'] = response
            path.write_text(json.dumps(state))

        with patch.object(measure_ready.time, 'sleep', side_effect=advance):
            self.run_measurement()
        self.assertEqual(self.report()['managed_clock_state']['last_response'], response)
        self.assertGreaterEqual(self.report()['managed_clock_ready_seconds'], 10)
        self.assertEqual(self.report()['status'], 'complete')
        self.guest.sync_clock.assert_not_called()

    def test_launcher_failure_or_manual_time_blocks_input_despite_earlier_response(self):
        for status in ['failed', 'manual_time_preserved', 'stopped', 'unknown']:
            with self.subTest(status=status):
                self.output = self.vm / (status + '.json')
                self.managed_clock(status, last_response={
                    'time': 1800000000.0, 'automatic': True, 'changed': True, 'offset': 2088.0})
                with self.assertRaisesRegex(GuestError, 'Managed clock is'):
                    self.run_measurement()
                self.factory.assert_not_called()
                self.assertFalse(self.calls)
                self.assertEqual(self.report()['stage'], 'managed_clock_ready')
                self.assertEqual(self.report()['managed_clock_state']['status'], status)

    def test_stale_launcher_clock_report_cannot_unlock_a_new_launch(self):
        self.managed_clock('waiting_for_interval', launch_started_unix=1700000000.0)
        with self.assertRaisesRegex(GuestError, 'another launch'):
            self.run_measurement()
        self.factory.assert_not_called()
        self.assertEqual(self.report()['status'], 'failed')

    def test_malformed_launcher_clock_response_blocks_input(self):
        self.managed_clock('waiting_for_interval', last_response={'automatic': True})
        with self.assertRaisesRegex(GuestError, 'no valid automatic-time response'):
            self.run_measurement()
        self.factory.assert_not_called()
        self.assertFalse(self.calls)

    def test_launcher_clock_wait_uses_original_boot_deadline(self):
        self.managed_clock('waiting_for_guest')
        with self.assertRaisesRegex(TimeoutError, 'Managed clock readiness deadline'):
            measure_ready.main([str(self.vm), '--output', str(self.output), '--deadline-seconds', '1'])
        self.factory.assert_not_called()
        self.assertEqual(self.report()['launch'], self.launch)

    def test_explicit_offer_cannot_duplicate_launcher_clock_maintenance(self):
        self.managed_clock('prepared')
        with self.assertRaises(SystemExit):
            self.run_measurement(sync_clock=True)
        self.factory.assert_not_called()
        self.browser.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_success_keeps_original_clock_and_does_not_claim_navigation(self):
        self.run_measurement()
        result = self.report()
        self.assertEqual(result['status'], 'complete')
        self.assertTrue(result['safari_foreground_verified'])
        self.assertTrue(result['screenshot_captured'])
        self.assertFalse(result['navigation_verified'])
        self.assertGreaterEqual(result['vphoned_healthy_seconds'], 10)
        self.assertEqual(self.calls, [
            ('screen.unlock', 'screen_unlocked'),
            ('apps.launch', 'safari_launch'),
            ('apps.foreground', 'safari_foreground'),
        ])
        self.assertTrue(self.output.with_suffix('.png').exists())
        self.assertNotIn('failure_screenshot', result)
        self.guest.sync_clock.assert_not_called()

    def test_once_only_requests_are_published_before_send_and_keep_timing(self):
        def observe(method, **params):
            if method in {'screen.unlock', 'apps.launch'}:
                request = self.report()['once_only_requests'][-1]
                self.assertEqual(request['method'], method)
                self.assertEqual(request['status'], 'in_flight')
                self.assertGreaterEqual(request['started_seconds'], 10)
                self.assertNotIn('finished_seconds', request)
            return self.native(method, **params)

        self.guest.call.side_effect = observe
        self.run_measurement()
        requests = self.report()['once_only_requests']
        self.assertEqual([r['method'] for r in requests], ['screen.unlock', 'apps.launch'])
        self.assertEqual([r['timeout_seconds'] for r in requests], [90, 120])
        for request in requests:
            self.assertEqual(request['status'], 'response_received')
            self.assertGreaterEqual(request['duration_seconds'], 0)
            self.assertAlmostEqual(request['duration_seconds'],
                                   request['finished_seconds'] - request['started_seconds'])
            self.assertNotIn('transport', request)

    def test_once_only_transport_failure_keeps_phase_without_retrying(self):
        for method in ['screen.unlock', 'apps.launch']:
            for phase in ['connect', 'send_request', 'receive_headers', 'read_body']:
                with self.subTest(method=method, phase=phase):
                    self.output = self.vm / f'{method}-{phase}.json'
                    self.calls.clear()
                    failure = GuestTransportError(phase, TimeoutError('timed out'))

                    def fail(selected, **params):
                        self.native(selected, **params)
                        if selected == method:
                            raise failure
                        return {'locked': False}

                    self.guest.call.side_effect = fail
                    with self.assertRaises(GuestError):
                        self.run_measurement()
                    result = self.report()
                    request = result['once_only_requests'][-1]
                    self.assertEqual(request['method'], method)
                    self.assertEqual(request['status'], 'error')
                    self.assertEqual(request['error'], str(failure))
                    self.assertEqual(request['transport'], {
                        'phase': phase, 'errno': None,
                        'request_may_have_been_sent': phase != 'connect',
                    })
                    self.assertIn('duration_seconds', request)
                    self.assertEqual([m for m, _ in self.calls],
                                     ['screen.unlock'] if method == 'screen.unlock'
                                     else ['screen.unlock', 'apps.launch'])
                    self.assertFalse(result['safari_foreground_verified'])
                    self.assertEqual(result['status'], 'failed')
                    self.assertNotIn('safari_launch_response', result)

    def test_nontransport_error_does_not_infer_whether_request_was_sent(self):
        self.failure_method = 'apps.launch'
        with self.assertRaises(GuestError):
            self.run_measurement()
        request = self.report()['once_only_requests'][-1]
        self.assertEqual(request['status'], 'error')
        self.assertNotIn('transport', request)
        self.assertNotIn('request_may_have_been_sent', request)

    def test_once_only_timeouts_still_use_remaining_original_deadline(self):
        measure_ready.main([str(self.vm), '--output', str(self.output),
                            '--deadline-seconds', '20'])
        for request in self.report()['once_only_requests']:
            self.assertGreater(request['timeout_seconds'], 0)
            self.assertLessEqual(request['timeout_seconds'], 10)

    def test_explicit_clock_sync_precedes_unlock_and_keeps_launch_clock(self):
        response = {'time': 1800000000.0, 'automatic': True, 'changed': True, 'offset': 600.0}

        def sync():
            self.assertEqual(self.report()['stage'], 'clock_sync')
            self.assertEqual(self.calls, [])
            return response

        self.guest.sync_clock.side_effect = sync
        self.run_measurement(sync_clock=True)
        self.guest.sync_clock.assert_called_once_with()
        result = self.report()
        self.assertEqual(result['clock_sync_response'], response)
        self.assertEqual(result['once_only_requests'][0]['method'], 'time.clock')
        self.assertEqual(result['once_only_requests'][0]['status'], 'response_received')
        self.assertGreaterEqual(result['clock_sync_seconds'], 10)
        self.assertEqual(result['launch'], self.launch)
        self.assertEqual(result['status'], 'complete')

    def test_clock_sync_timeout_stops_before_input_and_captures_display(self):
        self.guest.sync_clock.side_effect = GuestError('timed out')
        with self.assertRaisesRegex(GuestError, 'timed out'):
            self.run_measurement(sync_clock=True)
        self.guest.sync_clock.assert_called_once_with()
        self.assertFalse(self.calls)
        result = self.report()
        self.assertEqual(result['stage'], 'clock_sync')
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('clock_sync_seconds', result)
        self.assertTrue(Path(result['failure_screenshot']['path']).exists())

    def test_clock_sync_preserves_manual_time_and_requires_inspection(self):
        response = {'time': 1700000000.0, 'automatic': False, 'changed': False, 'offset': 100000000.0}
        self.guest.sync_clock.return_value = response
        with self.assertRaisesRegex(GuestError, 'requires automatic guest time'):
            self.run_measurement(sync_clock=True)
        self.guest.sync_clock.assert_called_once_with()
        self.assertFalse(self.calls)
        self.assertEqual(self.report()['clock_sync_response'], response)
        self.assertEqual(self.report()['status'], 'failed')

    def test_unlock_timeout_captures_display_without_repeating_guest_work(self):
        self.failure_method = 'screen.unlock'
        with self.assertRaisesRegex(GuestError, 'not retrying'):
            self.run_measurement()
        result = self.report()
        self.assertEqual(result['stage'], 'screen_unlocked')
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['safari_foreground_verified'])
        self.assertFalse(result['screenshot_captured'])
        self.assertEqual(self.calls, [('screen.unlock', 'screen_unlocked')])
        self.assertTrue(Path(result['failure_screenshot']['path']).exists())
        self.browser.assert_called_once_with(qmp_socket=self.vm / 'control.sock', display_timeout=5)

    def test_unlock_disconnect_does_not_repeat_input_or_launch_safari(self):
        self.guest.call.side_effect = GuestError('Connection reset by peer')
        with self.assertRaisesRegex(GuestError, 'not retrying native input'):
            self.run_measurement()
        self.guest.call.assert_called_once_with('screen.unlock', timeout=60)
        result = self.report()
        self.assertEqual(result['stage'], 'screen_unlocked')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Connection reset by peer', result['error'])
        self.assertNotIn('screen_unlocked_seconds', result)
        self.assertTrue(Path(result['failure_screenshot']['path']).exists())

    def test_locked_unlock_reply_requires_inspection_before_more_input(self):
        self.guest.call.side_effect = None
        self.guest.call.return_value = {'locked': True}
        with self.assertRaisesRegex(GuestError, 'Screen remains locked'):
            self.run_measurement()
        self.guest.call.assert_called_once_with('screen.unlock', timeout=60)
        self.assertFalse(self.report()['safari_foreground_verified'])
        self.assertNotIn('safari_launch_response', self.report())
        request = self.report()['once_only_requests'][0]
        self.assertEqual(request['status'], 'response_received')
        self.assertEqual(self.report()['status'], 'failed')

    def test_unverified_launch_response_survives_foreground_timeout(self):
        self.launch_response = {'pid': 0, 'frontmost_verified': False}
        self.failure_method = 'apps.foreground'
        with self.assertRaises(GuestError):
            self.run_measurement()
        result = self.report()
        self.assertEqual(result['safari_launch_response'], self.launch_response)
        self.assertIn('safari_launch_request_seconds', result)
        self.assertFalse(result['safari_foreground_verified'])
        self.assertFalse(result['navigation_verified'])
        self.assertEqual([method for method, _ in self.calls].count('apps.launch'), 1)

    def test_launch_response_does_not_replace_foreground_verification(self):
        self.launch_response = {'pid': 0, 'frontmost_verified': False}
        self.run_measurement()
        result = self.report()
        self.assertEqual(result['safari_launch_response'], self.launch_response)
        self.assertEqual(result['status'], 'complete')
        self.assertTrue(result['safari_foreground_verified'])
        self.assertIn(('apps.foreground', 'safari_foreground'), self.calls)
        self.assertFalse(result['navigation_verified'])

    def test_verified_launch_reuses_native_identity_without_another_app_scan(self):
        self.launch_response = {'pid': 219, 'frontmost_verified': True}
        self.failure_method = 'apps.foreground'
        self.run_measurement()
        result = self.report()
        self.assertEqual([method for method, _ in self.calls], ['screen.unlock', 'apps.launch'])
        self.assertEqual(result['safari_foreground_source'], 'apps.launch')
        self.assertEqual(result['safari_foreground_seconds'], result['safari_launch_request_seconds'])
        self.assertEqual(result['safari_launch_response'], self.launch_response)
        self.assertTrue(result['safari_foreground_verified'])
        self.assertTrue(result['screenshot_captured'])
        self.assertFalse(result['navigation_verified'])
        self.assertEqual(result['visual_review'], 'pending')

    def test_truthy_launch_flags_do_not_skip_foreground_verification(self):
        for index, flag in enumerate([1, 'true', None, False]):
            with self.subTest(flag=flag):
                self.output = self.vm / f'readiness-{index}.json'
                self.calls.clear()
                self.launch_response = {'pid': 219, 'frontmost_verified': flag}
                self.run_measurement()
                result = self.report()
                self.assertIn(('apps.foreground', 'safari_foreground'), self.calls)
                self.assertEqual(result['safari_foreground_source'], 'apps.foreground')
                self.assertEqual(result['safari_foreground_response'],
                                 {'bundle_id': 'com.apple.mobilesafari', 'verified': True})

    def test_verified_launch_still_requires_a_successful_screenshot(self):
        self.launch_response = {'pid': 0, 'frontmost_verified': True}
        self.capture.side_effect = RuntimeError('display capture failed')
        with self.assertRaisesRegex(RuntimeError, 'display capture failed'):
            self.run_measurement()
        result = self.report()
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['safari_foreground_verified'])
        self.assertFalse(result['screenshot_captured'])
        self.assertFalse(result['navigation_verified'])
        self.assertNotIn(('apps.foreground', 'safari_foreground'), self.calls)

    def test_capture_failure_preserves_original_launch_error(self):
        self.failure_method = 'apps.launch'
        self.capture.side_effect = RuntimeError('QMP unavailable')
        with self.assertRaisesRegex(GuestError, 'transport failed'):
            self.run_measurement()
        result = self.report()
        self.assertEqual(result['stage'], 'safari_launch')
        self.assertIn('transport failed', result['error'])
        self.assertEqual(result['failure_screenshot_error'], 'QMP unavailable')
        self.assertNotIn('failure_screenshot', result)

    def test_no_guest_screenshot_is_queued_after_native_timeout(self):
        self.failure_method = 'apps.foreground'
        with self.assertRaises(GuestError):
            self.run_measurement(qmp=False)
        self.browser.assert_not_called()
        self.assertEqual(self.report()['stage'], 'safari_foreground')

    def test_existing_evidence_prevents_any_guest_requests(self):
        for path in [self.output, self.output.with_suffix('.png'), self.output.with_suffix('.failure.png')]:
            with self.subTest(path=path.name):
                path.write_bytes(b'original evidence')
                with self.assertRaises((FileExistsError, SystemExit)):
                    self.run_measurement()
                self.assertEqual(path.read_bytes(), b'original evidence')
                self.factory.assert_not_called()
                self.browser.assert_not_called()
                path.unlink()

    def test_reused_pid_does_not_get_a_failure_capture(self):
        self.launch['pid_start_ticks'] = '0'
        (self.vm / 'launch.json').write_text(json.dumps(self.launch))
        with self.assertRaisesRegex(RuntimeError, 'no longer runs'):
            self.run_measurement()
        self.factory.assert_not_called()
        self.browser.assert_not_called()
        self.assertIn('no longer runs', self.report()['failure_screenshot_error'])


class ReportPublicationTest(unittest.TestCase):
    def test_initial_and_updated_reports_never_expose_partial_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'readiness.json'
            original = {'stage': 'starting'}
            updated = {'stage': 'complete'}
            for exclusive, value in [(True, original), (False, updated)]:
                def interrupted_write(data, stream, **kwargs):
                    encoded = json.dumps(data)
                    stream.write(encoded[:5])
                    stream.flush()
                    if exclusive:
                        self.assertFalse(path.exists())
                    else:
                        self.assertEqual(json.loads(path.read_text()), original)
                    stream.write(encoded[5:])
                with patch.object(measure_ready.json, 'dump', side_effect=interrupted_write):
                    measure_ready.write_report(path, value, exclusive=exclusive)
                self.assertEqual(json.loads(path.read_text()), value)
                self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_competing_initial_writer_cannot_replace_existing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'readiness.json'
            link = os.link
            def competitor(source, destination):
                path.write_text('{"owner":"another observer"}')
                link(source, destination)
            with patch.object(measure_ready.os, 'link', side_effect=competitor):
                with self.assertRaises(FileExistsError):
                    measure_ready.write_report(path, {'owner': 'this observer'}, exclusive=True)
            self.assertEqual(json.loads(path.read_text()), {'owner': 'another observer'})
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_serialization_failure_preserves_previous_report_and_removes_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'readiness.json'
            with self.assertRaises(ValueError):
                measure_ready.write_report(path, {'invalid': float('nan')}, exclusive=True)
            self.assertEqual(list(Path(directory).iterdir()), [])
            path.write_text('{"stage":"starting"}')
            with self.assertRaises(ValueError):
                measure_ready.write_report(path, {'invalid': float('nan')})
            self.assertEqual(json.loads(path.read_text()), {'stage': 'starting'})
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == '__main__':
    unittest.main()
