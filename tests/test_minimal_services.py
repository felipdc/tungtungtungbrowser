from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from tungtungtungbrowser.minimal_services import (CANDIDATES, MinimalServicePolicy,
    configure_for_boot, verify_after_boot, restore_enablement)


class Services:
    def __init__(self):
        self.disabled = {'existing.disabled.service'}
        self.enabled_overrides = {'existing.explicit.enabled.service'}
        self.protected = ['com.apple.cloudd', 'com.apple.audiomxd']
        self.services = {label: dict(label=label, program=path, enabled=True,
            loaded=True, running=True, pid=100 + i) for i, (label, path) in enumerate(CANDIDATES.items())}
        self.calls = []
        self.hook = lambda method, params: None
        self.stop_effective = True

    def __call__(self, method, **params):
        self.calls.append((method, params))
        if method != 'processes.kill':
            self.hook(method, params)
        if method == 'services.profile':
            return dict(supported=True, ios_major=26, never_disable=list(self.protected))
        if method == 'services.disabled':
            entries = {k: False for k in self.enabled_overrides} | {k: True for k in self.disabled}
            return dict(count=len(entries), disabled=entries)
        label = (next(k for k, v in self.services.items() if v['pid'] == params['pid'])
                 if method == 'processes.kill' else params['label'])
        row = self.services[label]
        if method == 'services.status':
            return deepcopy(row)
        if method == 'services.disable':
            assert params['force'] is True
            self.disabled.add(label)
            row['enabled'] = False
        elif method == 'processes.kill':
            self.hook(method, params)
            assert params['signal'] == 'TERM'
            assert params['force'] is True
            if self.stop_effective:
                row.update(running=False, pid=0)
            return {'pid': params['pid'], 'signal': 'TERM'}
        elif method == 'services.enable':
            self.disabled.remove(label)
            self.enabled_overrides.add(label)
            row['enabled'] = True
        else:
            raise AssertionError(method)
        return deepcopy(row)

    def mutations(self):
        return [(m, p) for m, p in self.calls if m in ('services.enable', 'services.disable', 'processes.kill')]


class PersistentServicesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'policy'
        self.rpc = Services()
        self.old = {'cid': 42, 'container_id': 'owned-container', 'host_boot_id': 'host', 'host_pid': 200,
                    'host_start_ticks': '1000', 'launch_started_unix': 10.}
        self.new = self.old | {'host_pid': 201, 'host_start_ticks': '2000', 'launch_started_unix': 30.}
        self.exit = {'started_unix': 10., 'finished_unix': 20., 'exit_code': 0,
                     'shutdown': {'forced_termination': False, 'native_response': {'accepted': True}},
                     'clock_maintenance': {'status': 'stopped', 'launch_started_unix': 10., 'offers': 1}}
        self.live = self.old
        self.closing = False

    def kwargs(self):
        return dict(rpc=self.rpc, owner=self.live, ownership=lambda:self.live,
                    closing=lambda:self.closing)

    def configure(self):
        return configure_for_boot(self.path, **self.kwargs())

    def next_boot(self, *, loaded=False):
        self.live = self.new
        for row in self.rpc.services.values():
            row.update(loaded=loaded, running=False)
            if loaded: row['pid'] = 0
            else:
                row.pop('program', None)
                row.pop('pid', None)

    def verify(self):
        return verify_after_boot(self.path, **self.kwargs(), stopped=self.exit)

    def test_disabled_jobs_may_still_run_until_clean_boot_then_restore_enablement(self):
        before = self.configure()
        self.assertTrue(all(row['running'] for row in before['after']['services'].values()))
        self.assertEqual([m for m, p in self.rpc.mutations()], ['services.disable']*4)
        self.next_boot()
        verified = self.verify()
        self.assertEqual(verified['status'], 'verified_after_clean_boot_requires_product_validation')
        restored = restore_enablement(self.path, **self.kwargs())
        self.assertEqual(restored['status'], 'enablement_configuration_restored')
        self.assertEqual([m for m, p in self.rpc.mutations()], ['services.disable']*4+['services.enable']*4)
        with self.assertRaises(FileExistsError): restore_enablement(self.path, **self.kwargs())

    def test_loaded_but_disabled_nonrunning_job_is_valid(self):
        self.configure(); self.next_boot(loaded=True)
        self.verify()

    def test_same_boot_or_different_device_is_not_a_reboot(self):
        self.configure()
        for owner in (self.old, self.new | {'cid': 43}, self.new | {'host_boot_id':'replacement'},
                      self.new | {'container_id':'replacement'},
                      self.new | {'launch_started_unix':19.},
                      self.new | {'host_pid':200, 'host_start_ticks':'1000'}):
            with self.subTest(owner=owner), self.assertRaises(ValueError):
                verify_after_boot(self.path, rpc=self.rpc, owner=owner, ownership=lambda:owner, stopped=self.exit)
        self.assertFalse((self.path/'verification.json').exists())

    def test_unclean_or_wrong_exit_cannot_authorize_reboot_verification(self):
        self.configure(); self.next_boot()
        cases = [self.exit | {'exit_code':1}, self.exit | {'started_unix':9.},
                 self.exit | {'finished_unix':float('nan')},
                 self.exit | {'shutdown':{'forced_termination':True}},
                 self.exit | {'clock_maintenance':{'status':'failed', 'offers':1}}]
        for stopped in cases:
            with self.subTest(stopped=stopped), self.assertRaises(ValueError):
                verify_after_boot(self.path, **self.kwargs(), stopped=stopped)
        self.assertFalse((self.path/'verification.json').exists())

    def test_running_job_after_boot_fails_without_signalling(self):
        self.configure(); self.next_boot(loaded=True)
        self.rpc.services[next(iter(CANDIDATES))].update(running=True, pid=999)
        with self.assertRaises(ValueError): self.verify()
        self.assertEqual([m for m,p in self.rpc.mutations()], ['services.disable']*4)

    def test_postboot_program_mismatch_is_rejected(self):
        self.configure(); self.next_boot(loaded=True)
        self.rpc.services[next(iter(CANDIDATES))]['program']='/other'
        with self.assertRaises(ValueError): self.verify()

    def test_unrelated_override_drift_is_rejected(self):
        self.configure(); self.next_boot()
        self.rpc.enabled_overrides.add('unrelated')
        with self.assertRaises(ValueError): self.verify()

    def test_unknown_preparation_mutation_has_no_replay_or_verification(self):
        def fail(method, params):
            if method=='services.disable': raise TimeoutError('unknown')
        self.rpc.hook=fail
        with self.assertRaises(TimeoutError): self.configure()
        with self.assertRaises(FileExistsError): self.configure()
        self.next_boot()
        with self.assertRaises(ValueError): self.verify()
        self.assertEqual(len(self.rpc.mutations()), 1)

    def test_closure_before_configuration_prevents_rpc(self):
        self.closing=True
        with self.assertRaises(RuntimeError): self.configure()
        self.assertEqual(self.rpc.calls, [])

    def test_uncertain_restore_cannot_be_repeated(self):
        self.configure(); self.next_boot(); self.verify()
        def fail(method, params):
            if method=='services.enable': raise TimeoutError('unknown')
        self.rpc.hook=fail
        with self.assertRaises(TimeoutError): restore_enablement(self.path, **self.kwargs())
        with self.assertRaises(FileExistsError): restore_enablement(self.path, **self.kwargs())
        self.assertEqual(len(self.rpc.mutations()), 5)

    def test_transport_cid_must_match_before_any_rpc(self):
        policy = MinimalServicePolicy(self.path, guest=SimpleNamespace(cid=43, _rpc=self.rpc),
                                     ownership=lambda: self.live)
        with self.assertRaises(ValueError): policy.configure()
        self.assertFalse(self.rpc.calls)
        self.assertFalse(self.path.exists())

    def test_wrapper_requires_three_explicit_stages(self):
        policy = MinimalServicePolicy(self.path, guest=SimpleNamespace(cid=42, _rpc=self.rpc),
                                     ownership=lambda: self.live)
        self.assertEqual(policy.configure()['status'], 'configured_requires_clean_boot')
        self.assertEqual([m for m, _ in self.rpc.mutations()], ['services.disable'] * 4)
        self.next_boot()
        self.assertEqual(policy.verify(self.exit)['status'], 'verified_after_clean_boot_requires_product_validation')
        self.assertEqual(policy.restore()['status'], 'enablement_configuration_restored')
        self.assertEqual([m for m, _ in self.rpc.mutations()], ['services.disable'] * 4 + ['services.enable'] * 4)

    def test_malformed_ownership_prevents_rpc(self):
        for change in ({'cid': True}, {'host_pid': 1}, {'host_start_ticks': 'nan'},
                       {'container_id': ''}, {'host_boot_id': ''},
                       {'launch_started_unix': float('nan')}, {'launch_started_unix': True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                configure_for_boot(self.path, rpc=self.rpc, owner=self.old | change,
                                   ownership=lambda: self.old | change)
        self.assertFalse(self.rpc.calls)
        self.assertFalse(self.path.exists())

    def test_invalid_clock_offer_count_cannot_prove_clean_shutdown(self):
        self.configure(); self.next_boot()
        for count in (True, 0, 1.0, float('nan')):
            stopped = self.exit | {'clock_maintenance': self.exit['clock_maintenance'] | {'offers': count}}
            with self.subTest(count=count), self.assertRaises(ValueError):
                verify_after_boot(self.path, **self.kwargs(), stopped=stopped)
        self.assertFalse((self.path / 'verification.json').exists())

    def test_current_protected_service_is_never_disabled(self):
        self.rpc.protected.append(next(iter(CANDIDATES)))
        with self.assertRaises(ValueError): self.configure()
        self.assertFalse(self.rpc.mutations())

    def test_owner_change_after_first_ack_stops_without_another_mutation(self):
        def change(method, params):
            if method == 'services.disable':
                self.live = self.old | {'host_start_ticks': '999'}
        self.rpc.hook = change
        with self.assertRaises(RuntimeError): self.configure()
        self.assertEqual(len(self.rpc.mutations()), 1)
        with self.assertRaises(FileExistsError): self.configure()

    def test_candidate_list_cannot_be_extended(self):
        with self.assertRaises(TypeError): CANDIDATES['com.apple.cloudd'] = '/usr/libexec/cloudd'


if __name__=='__main__': unittest.main()
