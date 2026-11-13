import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tungtungtungbrowser import Browser, BrowserProfile, InspectorSession
from tungtungtungbrowser.guest import Guest, GuestTransportError
from tungtungtungbrowser.native_focus import helper_path as focus_path
from tungtungtungbrowser.native_keyboard import helper_path as keyboard_path
from tungtungtungbrowser.native_touch import helper_path as touch_path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('install_browser_helpers', ROOT / 'scripts/install_browser_helpers.py')
installer = importlib.util.module_from_spec(SPEC)
with patch.object(sys, 'path', [str(ROOT / 'scripts'), *sys.path]):
    SPEC.loader.exec_module(installer)

SHA = 'a' * 64
TOKEN = 'b' * 32


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.write('profile.json', version=1, name='api-experimental', cid=7)
        self.write('touch/install.json', helper_sha256=SHA, guest_path=touch_path(SHA))
        self.write('keyboard/install.json', helper_sha256=SHA, guest_path=keyboard_path(SHA))
        self.write('focus/install.json', helper_sha256=SHA, deployment_id=TOKEN,
                   path=focus_path(SHA, TOKEN))
        self.write('bridge/deploy.json')

    def write(self, relative_path, **fields):
        path = self.directory / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'status': 'complete', **fields}))

    def test_configuration_sends_no_input_and_selects_same_endpoint(self):
        with patch.object(Guest, '_rpc', side_effect=AssertionError('Unexpected native call')):
            profile = BrowserProfile(self.directory)
            browser = profile.browser()
            page = profile.session(browser=browser, new_tab_url='http://example.test/fixture')
        connection = browser.guest.connection_factory()
        self.assertEqual((connection.cid, connection.timeout), (7, 120))
        self.assertEqual((browser.input_settle, browser.touch_hold_ms), (5, 0))
        self.assertEqual(browser.guest.touch_helper_sha256, SHA)
        self.assertEqual(browser.guest.keyboard_helper_sha256, SHA)
        self.assertEqual(browser.address_field_reader.path, focus_path(SHA, TOKEN))
        self.assertEqual((page.timeout, page.transport.timeout, page.transport.max_open_attempts), (300, 240, 4))
        self.assertIsNone(browser.qmp_socket)

    def test_existing_defaults_remain_unchanged(self):
        self.assertEqual((Browser().input_settle, Browser().touch_hold_ms), (2, 50))
        self.assertEqual(Browser().address_focus, 'auto')
        self.assertEqual(BrowserProfile(self.directory).browser().address_focus, 'auto')
        self.assertEqual(InspectorSession().timeout, 30)

    def test_input_settle_override_is_bounded_without_native_work(self):
        with patch.object(Guest, '_rpc', side_effect=AssertionError('Unexpected native call')):
            profile = BrowserProfile(self.directory)
            self.assertEqual(profile.browser(input_settle=1).input_settle, 1)
            self.assertEqual(profile.browser().input_settle, 5)
            for invalid in (-1, 11, float('nan'), float('inf'), '1'):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    profile.browser(input_settle=invalid)

    def test_profile_can_select_shortcut_focus_without_sending_input(self):
        with patch.object(Guest, '_rpc', side_effect=AssertionError('Unexpected native call')):
            browser = BrowserProfile(self.directory).browser(address_focus='shortcut')
        self.assertEqual(browser.address_focus, 'shortcut')
        self.assertEqual(browser.guest.keyboard_helper_sha256, SHA)

    def test_focus_read_bound_is_explicit_and_does_not_send_input(self):
        with patch.object(Guest, '_rpc', side_effect=AssertionError('Unexpected native call')):
            profile = BrowserProfile(self.directory)
            self.assertEqual(profile.browser().address_field_reader.max_read_attempts, 2)
            self.assertEqual(profile.browser(focus_read_attempts=3).address_field_reader.max_read_attempts, 3)
            for invalid in (0, 4, True, 2.5):
                with self.assertRaises(ValueError): profile.browser(focus_read_attempts=invalid)

    def test_profile_forwards_optional_discovery_wait_without_native_work(self):
        self.write('focus/install.json', helper_sha256=SHA, deployment_id=TOKEN,
                   path=focus_path(SHA, TOKEN), native_check={
                       'protocol': 1, 'status': 'ready', 'ui_input_sent': False,
                       'ax_queried': False, 'address_resolution': 'guarded-v1'})
        with patch.object(Guest, '_rpc', side_effect=AssertionError('Unexpected native call')):
            profile = BrowserProfile(self.directory)
            self.assertEqual(profile.browser().address_discovery_timeout, 0)
            self.assertEqual(profile.browser(address_discovery_timeout=30).address_discovery_timeout, 30)

    def test_native_blank_profile_keeps_endpoint_and_rejects_conflicting_url(self):
        with patch.object(Guest, '_rpc', side_effect=AssertionError('Unexpected native call')):
            profile = BrowserProfile(self.directory)
            browser = profile.browser()
            page = profile.session(browser=browser, new_tab=True)
            self.assertTrue(page.new_tab)
            self.assertEqual((page.transport.cid, page.url), (7, 'about:blank'))
            with self.assertRaises(ValueError):
                profile.session(browser=browser, new_tab=True, new_tab_url='https://example.test/')

    def test_failed_bundle_is_not_usable(self):
        self.write('profile.json', version=1, name='api-experimental', cid=3, status='failed')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            BrowserProfile(self.directory)

    def test_incomplete_helper_is_not_usable(self):
        self.write('keyboard/install.json', status='installing')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            BrowserProfile(self.directory)

    def test_wrong_helper_identity_is_rejected(self):
        self.write('touch/install.json', helper_sha256=SHA, guest_path=touch_path('c' * 64))
        with self.assertRaisesRegex(ValueError, 'digest'):
            BrowserProfile(self.directory)

    def test_focus_identity_is_verified_before_controller_use(self):
        self.write('focus/install.json', helper_sha256=SHA, deployment_id=TOKEN, path='/wrong')
        with self.assertRaisesRegex(ValueError, 'identity'):
            BrowserProfile(self.directory).browser()

    def test_custom_transport_cannot_drop_helpers(self):
        with self.assertRaisesRegex(ValueError, 'differs'):
            BrowserProfile(self.directory).browser(guest=Guest())

    def test_profile_rejects_another_guests_browser_even_with_matching_helpers(self):
        profile = BrowserProfile(self.directory)
        wrong = Guest(cid=8, touch_helper_sha256=SHA, keyboard_helper_sha256=SHA)
        with self.assertRaisesRegex(ValueError, 'differs'):
            profile.browser(guest=wrong)
        with self.assertRaisesRegex(ValueError, 'different guest CIDs'):
            profile.session(browser=Browser(guest=wrong), new_tab_url='http://example.test/')


class BundleInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'deployment'
        self.modules = [installer.touch, installer.keyboard, installer.focus, installer.bridge]
        self.reads, self.installs = [], []
        for module in self.modules:
            read = patch.object(module, 'read_build', return_value=(b'fixture', SHA)).start()
            install = patch.object(module, 'install', return_value={'status': 'complete'}).start()
            self.reads.append(read)
            self.installs.append(install)
        self.addCleanup(patch.stopall)

    def test_all_local_builds_checked_before_any_guest_install(self):
        self.reads[-1].side_effect = ValueError('source mismatch')
        with self.assertRaisesRegex(ValueError, 'source mismatch'):
            installer.install('/builds', self.output)
        self.assertFalse(self.output.exists())
        for install in self.installs:
            install.assert_not_called()

    def test_uncertain_install_is_not_replayed_and_later_helpers_not_started(self):
        self.installs[1].side_effect = GuestTransportError('receive_headers', TimeoutError())
        with self.assertRaises(GuestTransportError):
            installer.install('/builds', self.output)
        self.assertEqual([mock.call_count for mock in self.installs], [1, 1, 0, 0])
        report = json.loads((self.output / 'profile.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['completed'], ['touch'])
        self.assertEqual(report['installing'], 'keyboard')
        with self.assertRaises(FileExistsError):
            installer.install('/builds', self.output)
        self.assertEqual([mock.call_count for mock in self.installs], [1, 1, 0, 0])

    def test_success_starts_one_owned_bridge_with_longer_rpc_budget(self):
        report = installer.install('/builds', self.output, cid=7)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['completed'], ['touch', 'keyboard', 'focus', 'bridge'])
        self.assertFalse(report['native_input_sent'])
        self.assertEqual([mock.call_count for mock in self.installs], [1, 1, 1, 1])
        args, kwargs = self.installs[-1].call_args
        connection = args[0].connection_factory()
        self.assertEqual((connection.cid, connection.timeout), (7, 135))
        self.assertEqual(kwargs, {'launch_mode': 'process'})


if __name__ == '__main__':
    unittest.main()
