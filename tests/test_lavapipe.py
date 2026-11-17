from pathlib import Path
import tempfile
import unittest

from tungtungtungbrowser.lavapipe import select_manifest


class LavapipeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def manifest(self, name):
        path = self.directory / name
        path.write_text('{}')
        return path

    def test_native_driver_is_selected_in_mixed_architecture_installation(self):
        x86 = self.manifest('lvp_icd.x86_64.json')
        arm = self.manifest('lvp_icd.aarch64.json')
        self.assertEqual(select_manifest(self.directory, machine='x86_64'), x86)
        self.assertEqual(select_manifest(self.directory, machine='aarch64'), arm)

    def test_arm64_filename_is_supported(self):
        arm = self.manifest('lvp_icd.arm64.json')
        self.assertEqual(select_manifest(self.directory, machine='aarch64'), arm)

    def test_generic_driver_is_supported_on_both_hosts(self):
        generic = self.manifest('lvp_icd.json')
        for machine in ('aarch64', 'x86_64'):
            self.assertEqual(select_manifest(self.directory, machine=machine), generic)

    def test_wrong_architecture_and_hardware_drivers_are_rejected(self):
        self.manifest('lvp_icd.x86_64.json')
        self.manifest('nvidia_icd.json')
        with self.assertRaisesRegex(RuntimeError, 'lavapipe for aarch64'):
            select_manifest(self.directory, machine='aarch64')

    def test_missing_manifest_fails(self):
        with self.assertRaises(RuntimeError):
            select_manifest(self.directory, machine='x86_64')
