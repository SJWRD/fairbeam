"""Exercise the real Docker wrapper without Docker, apt or a native app on the test host."""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check-linux-package-install.py"
SPEC = importlib.util.spec_from_file_location("linux_package_check", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SHA = "a" * 40
CID = "b" * 64


class LinuxPackageInstallTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="package check ")
        self.addCleanup(self.temporary.cleanup)
        self.package = Path(self.temporary.name) / "Fairbeam package.deb"
        self.package.write_bytes(b"test package; never installed")
        self.calls = []
        self.failure = None
        self.cid = CID

    def fake_run(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        self.assertEqual(argv[0], "docker", "host must never execute apt or a package binary")
        self.assertNotIn("shell", kwargs)
        if argv[1] == "run":
            Path(argv[argv.index("--cidfile") + 1]).write_text(self.cid, encoding="ascii")
            if self.failure is not None:
                raise self.failure
        return subprocess.CompletedProcess(argv, 0)

    def run_check(self):
        with patch.object(MODULE.subprocess, "run", side_effect=self.fake_run):
            MODULE.check_package(self.package, SHA)

    def test_success_mount_is_read_only_and_only_owned_container_is_removed(self):
        self.run_check()
        run = next(argv for argv, _ in self.calls if argv[1] == "run")
        self.assertIn(f"type=bind,source={self.package.resolve()},target=/package/fairbeam.deb,readonly", run)
        self.assertIn("linux/amd64", run)
        self.assertEqual(run[-3:], ["/bin/bash", "-c", MODULE.CONTAINER_CHECK])
        self.assertEqual(self.calls[-1][0], ["docker", "rm", "--force", CID])
        self.assertTrue(self.package.exists())

    def test_failed_install_and_interruption_propagate_after_cleanup(self):
        for failure in (subprocess.CalledProcessError(9, ["docker", "run"]), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                self.calls.clear()
                self.failure = failure
                with self.assertRaises(type(failure)):
                    self.run_check()
                self.assertEqual(self.calls[-1][0], ["docker", "rm", "--force", CID])

    def test_untrusted_cid_never_used_for_cleanup(self):
        self.cid = "--all"
        self.run_check()
        self.assertFalse(any(argv[1] == "rm" for argv, _ in self.calls))

    def test_invalid_input_rejected_before_any_docker_or_system_operation(self):
        with patch.object(MODULE.subprocess, "run") as run:
            for sha in ("main", "a" * 39, SHA + ";echo invalid"):
                with self.assertRaises(ValueError):
                    MODULE.check_package(self.package, sha)
            with self.assertRaises(FileNotFoundError):
                MODULE.check_package(self.package.with_name("missing.deb"), SHA)
            comma = self.package.with_name("package,comma.deb")
            comma.write_bytes(b"fixture")
            with self.assertRaises(ValueError):
                MODULE.check_package(comma, SHA)
            run.assert_not_called()

    def test_container_gate_order_and_no_application_launch(self):
        program = MODULE.CONTAINER_CHECK
        self.assertLess(program.index("ldd /usr/bin/fairbeam"),
                        program.index("apt-get install -y --no-install-recommends binutils"))
        self.assertIn("grep -q 'not found'", program)
        self.assertIn("set -euo pipefail", program)
        self.assertIn('test "$(sha256sum "$sentinel")" = "$sentinel_sha"', program)
        self.assertLess(program.index("sentinel_sha="), program.index("apt-get update"))
        self.assertLess(program.index("apt-get purge -y fairbeam"), program.index('test ! -e /usr/bin/fairbeam'))
        self.assertNotRegex(program, r"(?m)^\s*(?:exec\s+)?(?:/usr/bin/)?fairbeam(?:\s|$)")


if __name__ == "__main__":
    unittest.main()
