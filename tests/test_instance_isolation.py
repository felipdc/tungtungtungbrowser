import fcntl
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.clone_vm import clone
from scripts.phone_instance import register, compose_command, main as instance_main
from scripts.run_vm import check_vm_access
from tungtungtungbrowser import Browser, InspectorSession
from tungtungtungbrowser.guest import Guest
from tungtungtungbrowser.inspector_transport import InspectorTransport
from tungtungtungbrowser.vm_lease import lease_vm


class InstanceIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = self.root / 'registry'
        self.registry.mkdir()
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        launcher = self.runtime / 'run-vphone.sh'
        launcher.write_text('#!/bin/sh\nexit 0\n')
        launcher.chmod(0o755)
        self.a, self.b = self.bundle('a'), self.bundle('b')

    def bundle(self, name):
        vm = self.root / name
        vm.mkdir()
        for filename in ('Disk.img', 'nvram.bin', 'SEPStorage', 'ecid',
                         'AVPBooter.vresearch1.bin', 'AVPSEPBooter.vresearch1.bin'):
            (vm / filename).write_bytes(b'independent fixture ' + name.encode())
        (vm / 'config.plist').write_bytes(plistlib.dumps({}))
        (vm / 'cache').mkdir()
        return vm

    def test_two_distinct_cids_and_disks_can_be_leased_together(self):
        with lease_vm(self.a, cid=41, registry=self.registry):
            with lease_vm(self.b, cid=42, registry=self.registry):
                with self.assertRaisesRegex(RuntimeError, 'CID 41'):
                    # Releasing this failed acquisition must not release A's lease.
                    other = self.bundle('c')
                    with lease_vm(other, cid=41, registry=self.registry):
                        self.fail('duplicate CID accepted')
                with self.assertRaisesRegex(RuntimeError, 'already leased'):
                    with lease_vm(self.a, cid=43, registry=self.registry):
                        self.fail('live disk reused')
        with lease_vm(self.b, cid=41, registry=self.registry):
            pass  # A stopped: stale lock file must permit safe reacquisition.

    def test_shared_disk_inode_is_rejected_across_different_bundle_paths(self):
        (self.b / 'Disk.img').unlink()
        os.link(self.a / 'Disk.img', self.b / 'Disk.img')
        with lease_vm(self.a, cid=41, registry=self.registry):
            with self.assertRaisesRegex(RuntimeError, 'Writable guest disk'):
                with lease_vm(self.b, cid=42, registry=self.registry):
                    self.fail('hard-linked disk accepted')

    def test_emulator_inherits_lease_after_parent_context_exits(self):
        with lease_vm(self.a, cid=41, registry=self.registry) as fds:
            child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'],
                                     stdin=subprocess.PIPE, pass_fds=fds)
        try:
            with self.assertRaisesRegex(RuntimeError, 'already leased'):
                with lease_vm(self.a, cid=42, registry=self.registry):
                    self.fail('child lease lost')
        finally:
            child.communicate(timeout=5)
        with lease_vm(self.a, cid=41, registry=self.registry):
            pass

    def test_copy_has_independent_disk_and_cache_writes_and_no_runtime_records(self):
        (self.a / 'cache/shader').write_text('cached')
        (self.a / 'launch.json').write_text('old launch')
        result = clone(self.a, self.root / 'copy')
        self.assertEqual(result['status'], 'complete')
        copied = self.root / 'copy'
        self.assertFalse((copied / 'launch.json').exists())
        for name in ('Disk.img', 'nvram.bin', 'SEPStorage', 'cache/shader'):
            self.assertEqual((self.a / name).read_bytes(), (copied / name).read_bytes())
            (copied / name).write_bytes(b'changed')
            self.assertNotEqual((self.a / name).read_bytes(), (copied / name).read_bytes())

    def test_copy_refuses_live_source_and_preserves_partial_failure(self):
        with lease_vm(self.a):
            with self.assertRaisesRegex(RuntimeError, 'already leased'):
                clone(self.a, self.root / 'live-copy')
        self.assertFalse((self.root / 'live-copy').exists())
        with patch('scripts.clone_vm.subprocess.run', side_effect=OSError('copy failed')):
            with self.assertRaises(OSError):
                clone(self.a, self.root / 'failed-copy')
        failed = self.root / 'failed-copy'
        self.assertEqual(json.loads((failed / 'clone.json').read_text())['status'], 'failed')
        with self.assertRaisesRegex(RuntimeError, 'copy is incomplete'):
            check_vm_access(failed, self.runtime)
        with self.assertRaises(FileExistsError):
            clone(self.a, failed)

    def test_copy_rejects_forced_shutdown_qcow_and_cache_symlinks(self):
        for case in ('forced', 'qcow', 'cache-link'):
            with self.subTest(case=case):
                source = self.bundle(case)
                if case == 'forced':
                    (source / 'exit.json').write_text(json.dumps({'exit_code': 0, 'shutdown': {'forced_termination': True}}))
                elif case == 'qcow':
                    (source / 'Disk.img').write_bytes(b'QFI\xfbdata')
                else:
                    (source / 'cache/link').symlink_to(self.a / 'cache', target_is_directory=True)
                with self.assertRaises(ValueError):
                    clone(source, self.root / (case + '-copy'))

    def test_registration_rejects_duplicate_cid_and_aliased_disk(self):
        register(self.registry, 'a', vm=self.a, runtime=self.runtime, cid=41)
        with self.assertRaisesRegex(ValueError, 'CID 41'):
            register(self.registry, 'b', vm=self.b, runtime=self.runtime, cid=41)
        (self.b / 'Disk.img').unlink()
        (self.b / 'Disk.img').symlink_to(self.a / 'Disk.img')
        with self.assertRaisesRegex(ValueError, 'disk inodes'):
            register(self.registry, 'b', vm=self.b, runtime=self.runtime, cid=42)
        self.assertFalse((self.registry / 'b').exists())

    def test_registered_resources_survive_conflicting_shell_settings(self):
        register(self.registry, 'a', vm=self.a, runtime=self.runtime, cid=41,
                 resources={'guest_memory_gib': 2, 'container_memory_mib': 6144,
                            'container_cpus': 3.5, 'tcg_cache_mib': 1024})
        register(self.registry, 'b', vm=self.b, runtime=self.runtime, cid=42)
        with patch.dict(os.environ, {'TTT_VM_MEMORY_GIB': '8', 'TTT_CONTAINER_MEMORY': '20g'}):
            _, a = compose_command(self.registry, 'a', 'config', [])
            _, b = compose_command(self.registry, 'b', 'config', [])
        self.assertEqual(a['TTT_VM_MEMORY_GIB'], '2')
        self.assertEqual(a['TTT_CONTAINER_MEMORY'], '6144m')
        self.assertEqual(a['TTT_CONTAINER_CPUS'], '3.5')
        self.assertEqual(a['TTT_TCG_CACHE_MIB'], '1024')
        self.assertNotIn('TTT_CONTAINER_MEMORY', b)
        self.assertNotIn('TTT_VM_MEMORY_GIB', b)
        self.assertEqual(b['TTT_VM_CID'], '42')

    def test_resource_errors_fail_before_registering_or_running_compose(self):
        for resources in ({'cid': 42}, {'guest_memory_gib': True},
                          {'container_memory_mib': 0}, {'container_cpus': float('nan')},
                          {'container_cpus': float('inf')}, {'tcg_cache_mib': 513}):
            with self.subTest(resources=resources), self.assertRaises(ValueError):
                register(self.registry, 'a', vm=self.a, runtime=self.runtime,
                         cid=41, resources=resources)
            self.assertFalse((self.registry / 'a').exists())
        register(self.registry, 'a', vm=self.a, runtime=self.runtime, cid=41)
        path = self.registry / 'a/instance.json'
        record = json.loads(path.read_text())
        record['resources'] = {'TTT_VM_CID': 42}
        path.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            compose_command(self.registry, 'a', 'up', [])

    def test_registered_intermediate_cache_passes_runtime_preflight(self):
        register(self.registry, 'a', vm=self.a, runtime=self.runtime, cid=41,
                 resources={'tcg_cache_mib': 768})
        _, env = compose_command(self.registry, 'a', 'config', [])
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/run_vm.py'),
             str(self.a), '--source', str(self.runtime), '--check-access',
             '--tcg-cache-mib', env['TTT_TCG_CACHE_MIB']],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(env['TTT_TCG_CACHE_MIB'], '768')
        self.assertFalse(json.loads(result.stdout)['guest_started'])

    def test_compose_ignores_another_phones_environment_and_rejects_scaling(self):
        register(self.registry, 'a', vm=self.a, runtime=self.runtime, cid=41)
        with patch.dict(os.environ, {'TTT_VM_CID': '42', 'TTT_VM_DIR': '/wrong',
                                     'TTT_PROXY_FILE': '/secret-for-b', 'COMPOSE_FILE': '/wrong'}):
            command, env = compose_command(self.registry, 'a', 'up', ['-d'])
        self.assertEqual(env['TTT_VM_CID'], '41')
        self.assertEqual(env['TTT_VM_DIR'], str(self.a))
        self.assertNotIn('TTT_PROXY_FILE', env)
        self.assertNotIn('COMPOSE_FILE', env)
        self.assertTrue(command[3].startswith('ttt-phone-a-'))
        self.assertEqual(command[-2:], ['up', '-d'])
        with self.assertRaisesRegex(ValueError, 'another named instance'):
            compose_command(self.registry, 'a', 'up', ['--scale', 'iphone=2'])

    def test_separate_registries_cannot_reconfigure_each_others_compose_project(self):
        a = register(self.registry, 'phone', vm=self.a, runtime=self.runtime, cid=41)
        b = register(self.root / 'other-registry', 'phone', vm=self.b, runtime=self.runtime, cid=42)
        self.assertNotEqual(a['project'], b['project'])

    def test_cli_creates_external_registry_before_start_but_config_is_read_only(self):
        register(self.registry, 'a', vm=self.a, runtime=self.runtime, cid=41)
        for action in ('up', 'config'):
            events = []
            with self.subTest(action=action), patch.object(sys, 'argv',
                    ['phone_instance.py', '--registry', str(self.registry), 'compose', 'a', action]), \
                    patch('scripts.phone_instance.subprocess.run', side_effect=lambda *a, **k: events.append('volume')) as create, \
                    patch('scripts.phone_instance.subprocess.call', side_effect=lambda *a, **k: events.append('compose') or 0):
                with self.assertRaises(SystemExit) as stopped:
                    instance_main()
                self.assertEqual(stopped.exception.code, 0)
                self.assertEqual(events, ['volume', 'compose'] if action == 'up' else ['compose'])
                if action == 'up':
                    self.assertEqual(create.call_args.args[0], ['docker', 'volume', 'create', 'tungtungtungbrowser-runtime-leases'])

    def test_inspector_inherits_native_browser_cid_and_rejects_mixed_endpoints(self):
        a, b = Browser(cid=41), Browser(cid=42)
        self.assertEqual(InspectorSession(browser=a).transport.cid, 41)
        self.assertEqual(InspectorSession(browser=b).transport.cid, 42)
        with self.assertRaisesRegex(ValueError, 'different guest CIDs'):
            InspectorSession(browser=a, transport=InspectorTransport(cid=42))
        self.assertEqual(InspectorSession(transport=InspectorTransport(cid=42)).transport.cid, 42)
        for cid in (True, 0, 2, 2**32-1, '41'):
            with self.subTest(cid=cid):
                with self.assertRaises(ValueError):
                    Guest(cid=cid)
                with self.assertRaises(ValueError):
                    InspectorTransport(cid=cid)


if __name__ == '__main__':
    unittest.main()
