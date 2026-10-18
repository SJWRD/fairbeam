"""Native CPU defaults are limited to macOS and an explicitly qualified Windows pack."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import fairbeam

NAMES = [n for n, _ in fairbeam._NATIVE_CPU_ENV]
EXPECTED = dict(fairbeam._NATIVE_CPU_ENV)
IO_FLAG = "OPENEMS_EXPERIMENTAL_HDF5_TD_BATCH"


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in NAMES and k not in ("FAIRBEAM_NATIVE_CPU", IO_FLAG)}
    env.update(extra)
    return mock.patch.dict(os.environ, env, clear=True)


class NativeCpuDefault(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = mock.patch.dict(os.environ, {"OPENEMS_INSTALL_PATH": str(self.root)})
        env.start()
        self.addCleanup(env.stop)

    def write_marker(self, **changes):
        marker = {"marker": "fairbeam openEMS pack v1", "platform": "windows-x64",
                  "native_cpu": True, "openems_commit": "12cd91de21548987b835ce21f10fd8421d74da78"}
        marker.update(changes)
        (self.root / 'openems-pack.json').write_text(json.dumps(marker), encoding='utf-8')

    def test_variables_and_values(self):
        self.assertEqual(EXPECTED, {
            "OPENEMS_EXPERIMENTAL_CPU_PHASE_DISPATCH": "phase-lists",
            "OPENEMS_EXPERIMENTAL_CPU_UPML_CURSOR": "cursor",
            "OPENEMS_EXPERIMENTAL_CPU_UPML_ROWS": "rows",
        })

    def test_macos_sets_all_three(self):
        with clean_env(), mock.patch.object(sys, "platform", "darwin"):
            fairbeam._native_cpu_defaults()
            for name, value in EXPECTED.items():
                self.assertEqual(os.environ[name], value)
            note = fairbeam.native_cpu_note()
            self.assertIn("macOS default", note)
            self.assertIn("FAIRBEAM_NATIVE_CPU=0", note)

    def test_opt_out(self):
        for off in ("0", "off", "False", "no"):
            with clean_env(FAIRBEAM_NATIVE_CPU=off), mock.patch.object(sys, "platform", "darwin"):
                fairbeam._native_cpu_defaults()
                self.assertFalse(any(n in os.environ for n in NAMES), off)
                self.assertIsNone(fairbeam.native_cpu_note())

    def test_windows_and_linux_unchanged(self):
        for platform in ("win32", "linux"):
            with clean_env(), mock.patch.object(sys, "platform", platform):
                fairbeam._native_cpu_defaults()
                self.assertFalse(any(n in os.environ for n in NAMES), platform)
                self.assertIsNone(fairbeam.native_cpu_note())

    def test_user_values_are_kept(self):
        with clean_env(OPENEMS_EXPERIMENTAL_CPU_UPML_ROWS="off"), mock.patch.object(sys, "platform", "darwin"):
            fairbeam._native_cpu_defaults()
            self.assertEqual(os.environ["OPENEMS_EXPERIMENTAL_CPU_UPML_ROWS"], "off")
            self.assertEqual(os.environ["OPENEMS_EXPERIMENTAL_CPU_UPML_CURSOR"], "cursor")
            self.assertIsNone(fairbeam.native_cpu_note())  # not all three requested as validated

    def test_explicit_environment_without_default(self):
        with clean_env(**EXPECTED), mock.patch.object(sys, "platform", "win32"):
            self.assertIn("environment", fairbeam.native_cpu_note())

    def test_qualified_windows_pack_requests_patches(self):
        self.write_marker()
        with clean_env(), mock.patch.object(sys, "platform", "win32"):
            fairbeam._native_cpu_defaults()
            self.assertTrue(all(os.environ.get(k) == v for k, v in EXPECTED.items()))
            self.assertIn("qualified Windows pack default", fairbeam.native_cpu_note())

    def test_windows_marker_rejects_other_builds_and_malformed_data(self):
        for changes in ({'native_cpu': False}, {'native_cpu': 1}, {'native_cpu': 'true'},
                        {'platform': 'macos-arm64'}, {'marker': 'another pack'},
                        {'openems_commit': '08e15ff532a7f4cfd1d4e7164ec262f5187bf30e'}):
            with self.subTest(changes=changes), clean_env(), mock.patch.object(sys, 'platform', 'win32'):
                self.write_marker(**changes)
                fairbeam._native_cpu_defaults()
                self.assertFalse(any(n in os.environ for n in NAMES))
        for raw in (b'[]', b'null', b'{broken', b'\xff', b' ' * 65537):
            with self.subTest(raw=raw[:20]), clean_env(), mock.patch.object(sys, 'platform', 'win32'):
                (self.root / 'openems-pack.json').write_bytes(raw)
                self.assertFalse(fairbeam.native_cpu_default())

    def test_windows_opt_out_and_user_values(self):
        self.write_marker()
        for value in ('0', 'off', ' FALSE ', 'no'):
            with clean_env(FAIRBEAM_NATIVE_CPU=value), mock.patch.object(sys, 'platform', 'win32'):
                fairbeam._native_cpu_defaults()
                self.assertFalse(any(n in os.environ for n in NAMES))
        with clean_env(OPENEMS_EXPERIMENTAL_CPU_UPML_ROWS='off'), mock.patch.object(sys, 'platform', 'win32'):
            fairbeam._native_cpu_defaults()
            self.assertEqual(os.environ['OPENEMS_EXPERIMENTAL_CPU_UPML_ROWS'], 'off')
            self.assertEqual(os.environ['OPENEMS_EXPERIMENTAL_CPU_UPML_CURSOR'], 'cursor')
            self.assertIsNone(fairbeam.native_cpu_note())

    def test_windows_root_selection_matches_dll_resolution(self):
        self.write_marker()
        with clean_env(OPENEMS_INSTALL_PATH='', CSXCAD_INSTALL_PATH=str(self.root)), mock.patch.object(sys, 'platform', 'win32'):
            self.assertTrue(fairbeam.native_cpu_default())
        # An unmarked selected runtime must not borrow another installation's marker.
        with clean_env(OPENEMS_INSTALL_PATH=str(self.root / 'other'), CSXCAD_INSTALL_PATH=str(self.root)), mock.patch.object(sys, 'platform', 'win32'):
            self.assertFalse(fairbeam.native_cpu_default())
        with clean_env(OPENEMS_INSTALL_PATH='', CSXCAD_INSTALL_PATH=''):
            self.assertEqual(fairbeam._openems_install_root(), r'C:\opt\openEMS')

    def test_marker_read_error_is_nonfatal(self):
        with clean_env(), mock.patch.object(sys, 'platform', 'win32'), mock.patch('builtins.open', side_effect=PermissionError):
            self.assertFalse(fairbeam.native_cpu_default())

    def test_windows_hdf5_batch_requires_explicit_boolean_capability(self):
        for changes in ({}, {'native_hdf5_td_batch': False}, {'native_hdf5_td_batch': 1},
                        {'native_hdf5_td_batch': 'true'}, {'native_hdf5_td_batch': None}):
            with self.subTest(changes=changes), clean_env(), mock.patch.object(sys, 'platform', 'win32'):
                self.write_marker(**changes)
                fairbeam._native_cpu_defaults()
                self.assertNotIn(IO_FLAG, os.environ)
                self.assertTrue(all(os.environ.get(k) == v for k, v in EXPECTED.items()))
        self.write_marker(native_hdf5_td_batch=True)
        with clean_env(), mock.patch.object(sys, 'platform', 'win32'):
            fairbeam._native_cpu_defaults()
            self.assertEqual(os.environ[IO_FLAG], '1')

    def test_hdf5_batch_requires_qualified_windows_pack(self):
        self.write_marker(native_hdf5_td_batch=True)
        for platform in ('darwin', 'linux'):
            with self.subTest(platform=platform), clean_env(), mock.patch.object(sys, 'platform', platform):
                fairbeam._native_cpu_defaults()
                self.assertNotIn(IO_FLAG, os.environ)
        for changes in ({'native_cpu': False}, {'openems_commit': 'unqualified'},
                        {'marker': 'GPU pack'}, {'platform': 'macos-arm64'}):
            with self.subTest(changes=changes), clean_env(), mock.patch.object(sys, 'platform', 'win32'):
                self.write_marker(native_hdf5_td_batch=True, **changes)
                fairbeam._native_cpu_defaults()
                self.assertNotIn(IO_FLAG, os.environ)
        (self.root / 'openems-pack.json').unlink()
        with clean_env(), mock.patch.object(sys, 'platform', 'win32'):
            fairbeam._native_cpu_defaults()
            self.assertNotIn(IO_FLAG, os.environ)

    def test_hdf5_batch_optout_and_explicit_value_are_preserved(self):
        self.write_marker(native_hdf5_td_batch=True)
        for off in ('0', 'off', ' FALSE ', 'no'):
            with self.subTest(off=off), clean_env(FAIRBEAM_NATIVE_CPU=off), mock.patch.object(sys, 'platform', 'win32'):
                fairbeam._native_cpu_defaults()
                self.assertNotIn(IO_FLAG, os.environ)
        with clean_env(**{IO_FLAG: '0'}), mock.patch.object(sys, 'platform', 'win32'):
            fairbeam._native_cpu_defaults()
            self.assertEqual(os.environ[IO_FLAG], '0')
            self.assertTrue(all(os.environ.get(k) == v for k, v in EXPECTED.items()))


if __name__ == "__main__":
    unittest.main()
