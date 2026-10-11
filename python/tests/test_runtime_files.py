"""Retry behavior for Windows runtime file operations."""

import ctypes
from ctypes import wintypes
import importlib.util
import json
from pathlib import Path
import sys
import shutil
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO


INSTALL_PATH = Path(__file__).resolve().parents[2] / "runtime" / "install.py"
PINS_PATH = Path(__file__).resolve().parents[2] / "runtime" / "pins.json"
SPEC = importlib.util.spec_from_file_location("fairbeam_runtime_install", INSTALL_PATH)
install = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install)


def win_error(code):
    error = OSError(f"Windows error {code}")
    error.winerror = code
    return error


class RuntimeEngineSelection(unittest.TestCase):
    def test_gpu_pin_is_separate_from_cpu_pin(self):
        pins = json.loads(PINS_PATH.read_text(encoding="utf-8"))
        cpu_group, cpu = install.select_openems_pin(pins, "windows-x64", "cpu")
        gpu_group, gpu = install.select_openems_pin(pins, "windows-x64", "gpu")

        self.assertEqual(cpu_group, pins["openems"])
        self.assertEqual(cpu["version"] if "version" in cpu else cpu_group["version"], "v0.37.0-rc3")
        self.assertEqual(gpu_group["version"], "v0.37.0-beta1+gpu")
        self.assertEqual(gpu["sha256"], "ca308c03de41054056b20b4d40c3fe51c07f2a7fb5702fe1042d6406f741764f")
        self.assertEqual(gpu["size"], 66009500)
        self.assertEqual(install.select_openems_pin(pins, "macos-arm64", "gpu")[1], {})

    def test_gpu_engine_help_marker_is_specific(self):
        self.assertTrue(install.gpu_engine_listed("engines:\n  cpu: multithreaded\n  gpu: CUDA"))
        self.assertFalse(install.gpu_engine_listed("engines:\n  cpu: multithreaded\n  gpu backend disabled"))
        self.assertFalse(install.gpu_engine_listed("engines:\n  cpu: multithreaded"))

    def test_bad_gpu_archive_hash_preserves_existing_manifest_and_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "gpu-runtime-test"
            py = root / "venv" / "Scripts" / "python.exe"
            uv = root / "uv" / "uv.exe"
            py.parent.mkdir(parents=True)
            uv.parent.mkdir(parents=True)
            py.write_bytes(b"mock python")
            uv.write_bytes(b"mock uv")
            existing_manifest = root / "manifest.json"
            original_manifest = json.dumps({
                "marker": install.MARKER,
                "engine": "gpu",
                "openems": "existing working build",
            }).encode("utf-8")
            existing_manifest.write_bytes(original_manifest)
            openems_exe = root / "openEMS" / "openEMS.exe"
            openems_exe.parent.mkdir()
            openems_exe.write_bytes(b"existing working engine")
            bad_archive = Path(tmp) / "wrong-openems.zip"
            bad_archive.write_bytes(b"not the pinned archive")
            stderr = StringIO()
            args = [
                "install.py", "--runtime-root", str(root), "--resources",
                str(INSTALL_PATH.parents[1]), "--engine", "gpu", "--openems-archive",
                str(bad_archive),
            ]

            with mock.patch.object(install.sys, "platform", "win32"), \
                    mock.patch.object(install, "platform_key", return_value="windows-x64"), \
                    mock.patch.object(install.sys, "argv", args), \
                    redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    install.main()

            self.assertEqual(raised.exception.code, 1)
            self.assertIn("SHA-256 does not match", stderr.getvalue())
            self.assertEqual(existing_manifest.read_bytes(), original_manifest)
            self.assertEqual(openems_exe.read_bytes(), b"existing working engine")


class RuntimeLibsCleanup(unittest.TestCase):
    legacy = "antenlab_openems_libs"
    replacement = "fairbeam_openems_libs"

    def test_current_pack_keeps_its_libs_without_querying_the_venv(self):
        for names in ({self.legacy}, {self.legacy, self.replacement},
                      {self.legacy.replace("_", "-")}, {"csxcad", "openems"}):
            with self.subTest(names=names), mock.patch.object(install.subprocess, "run") as run:
                self.assertIsNone(install.remove_stale_openems_libs(Path("python"), Path("runtime"), names))
                run.assert_not_called()

    def test_replacement_pack_without_stale_distribution_skips_uninstall(self):
        with mock.patch.object(install.subprocess, "run", return_value=
                               install.subprocess.CompletedProcess([], 0, "False\n", "")) as run:
            self.assertEqual(install.remove_stale_openems_libs(Path("python"), Path("runtime"),
                                                              {self.replacement}), self.replacement)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][:2], ["python", "-c"])

    def test_verify_requires_both_core_libraries_from_the_replacement_folder(self):
        from importlib import metadata

        folder = Path(tempfile.gettempdir()) / "runtime-libs-test" / self.replacement
        old_folder = folder.with_name(self.legacy)
        for csxcad_folder, openems_folder in ((folder, folder), (old_folder, folder), (folder, old_folder)):
            with self.subTest(csxcad_folder=csxcad_folder, openems_folder=openems_folder):
                paths = [csxcad_folder / "libCSXCAD.dylib", openems_folder / "libopenEMS.dylib"]
                dyld = SimpleNamespace(
                    _dyld_get_image_name=mock.Mock(side_effect=lambda i: str(paths[i]).encode()),
                    _dyld_image_count=mock.Mock(return_value=len(paths)),
                )
                modules = {name: SimpleNamespace(__version__="1.0")
                           for name in ("CSXCAD", "openEMS", "fairbeam")}
                modules["ctypes"] = SimpleNamespace(CDLL=lambda _: dyld, c_char_p=object(), c_uint32=object())

                def run(cmd, **kwargs):
                    stdout = StringIO()
                    try:
                        with redirect_stdout(stdout):
                            exec(cmd[2], {})
                    except RuntimeError as e:
                        return install.subprocess.CompletedProcess(cmd, 1, stdout.getvalue(), str(e))
                    return install.subprocess.CompletedProcess(cmd, 0, stdout.getvalue(), "")

                stderr = StringIO()
                with mock.patch.object(install.sys, "platform", "darwin"), \
                        mock.patch.dict(sys.modules, modules), \
                        mock.patch.object(metadata, "version", return_value="1.0"), \
                        mock.patch.object(metadata, "distribution", return_value=
                                          SimpleNamespace(locate_file=lambda _: folder)), \
                        mock.patch.object(install.subprocess, "run", side_effect=run), \
                        mock.patch.object(install, "progress"), redirect_stderr(stderr):
                    if csxcad_folder == openems_folder == folder:
                        self.assertEqual(install.verify(Path("python"), Path("runtime"), self.replacement)["openems"], "1.0")
                    else:
                        with self.assertRaises(SystemExit):
                            install.verify(Path("python"), Path("runtime"), self.replacement)
                        self.assertIn("was not loaded from", stderr.getvalue())

    def run_install(self, *, cleanup_result=0, verify_result=0, existing_build=False, repair=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = (Path(tmp) / "runtime").resolve()
            resources = Path(tmp) / "resources"
            py = install.venv_python(root)
            uv = install.uv_exe(root)
            for tool in (py, uv):
                tool.parent.mkdir(parents=True, exist_ok=True)
                tool.write_bytes(b"fake tool")
            (resources / "runtime").mkdir(parents=True)
            pins = {"openems": {"version": "test", "macos-arm64": {
                "url": "https://example.invalid/pack.tar.gz", "sha256": "test-digest",
                "kind": "macos-pack",
            }}}
            (resources / "runtime" / "pins.json").write_text(json.dumps(pins), encoding="utf-8")
            oems = root / "openEMS"
            oems.mkdir()
            tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
            for name in ("CSXCAD", "openEMS", self.replacement):
                (oems / f"{name}-1.0-{tag}-{tag}-macosx_27_0_arm64.whl").touch()
            if existing_build:
                (oems / install.STAMP).write_text('{"sha256": "test-digest"}', encoding="utf-8")
            events = []
            stderr = StringIO()

            def run(cmd, **kwargs):
                self.assertEqual(kwargs["env"], install.child_env(root))
                self.assertEqual(kwargs["stdin"], install.subprocess.DEVNULL)
                if cmd[0] == str(uv):
                    events.append("cleanup")
                    self.assertEqual(cmd, [str(uv), "pip", "uninstall", "--python", str(py),
                                           self.legacy.replace("_", "-")])
                    if isinstance(cleanup_result, OSError):
                        raise cleanup_result
                    return install.subprocess.CompletedProcess(cmd, cleanup_result, "", "cleanup error")
                self.assertEqual(cmd[:2], [str(py), "-c"])
                if "m.distributions()" in cmd[2]:
                    events.append("query")
                    return install.subprocess.CompletedProcess(cmd, 0, "True\n", "")
                events.append("verify")
                self.assertIn("import json, CSXCAD, openEMS, fairbeam", cmd[2])
                return install.subprocess.CompletedProcess(cmd, verify_result,
                    '{"openems": "1.0", "csxcad": "1.0", "fairbeam": "1.0"}\n', "import failed")

            def install_wheels(cmd, step, runtime_root):
                events.append(step)
                if step == "install-openems-wheels":
                    self.assertEqual("--reinstall" in cmd, repair or not existing_build)

            args = ["install.py", "--runtime-root", str(root), "--resources", str(resources)]
            if repair:
                args.append("--repair")
            with mock.patch.object(install.sys, "argv", args), \
                    mock.patch.object(install, "platform_key", return_value="macos-arm64"), \
                    mock.patch.object(install, "download"), \
                    mock.patch.object(install, "unpack_openems", return_value=oems), \
                    mock.patch.object(install, "run", side_effect=install_wheels), \
                    mock.patch.object(install, "install_app", return_value="1.0"), \
                    mock.patch.object(install.subprocess, "run", side_effect=run), \
                    mock.patch.object(install, "progress"), redirect_stderr(stderr):
                if verify_result:
                    with self.assertRaises(SystemExit):
                        install.main()
                else:
                    install.main()
            self.assertEqual(events, ["install-packages", "install-openems-wheels", "query", "cleanup", "verify"])
            self.assertEqual((root / "manifest.json").exists(), not verify_result)
            return stderr.getvalue()

    def test_cleanup_precedes_fresh_import_verification_and_preserves_reinstall_logic(self):
        for existing_build, repair in ((False, False), (True, False), (True, True)):
            with self.subTest(existing_build=existing_build, repair=repair):
                self.assertEqual(self.run_install(existing_build=existing_build, repair=repair), "")

    def test_uninstall_failure_continues_only_when_imports_pass(self):
        for cleanup_result in (1, OSError("cannot launch uv")):
            for verify_result in (0, 1):
                with self.subTest(cleanup_result=cleanup_result, verify_result=verify_result):
                    stderr = self.run_install(cleanup_result=cleanup_result, verify_result=verify_result)
                    self.assertIn("could not remove the obsolete libs wheel", stderr)
                    if verify_result:
                        self.assertIn("the runtime does not import", stderr)


class WindowsFileRetry(unittest.TestCase):
    def test_transient_failures_then_success_return_result(self):
        result = object()
        outcomes = [win_error(5), win_error(32), result]
        calls = []

        def operation():
            calls.append(1)
            value = outcomes.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        with mock.patch.object(install.sys, "platform", "win32"):
            actual = install._retry_windows_file_operation(
                operation, attempts=3, delay=0,
            )
        self.assertIs(actual, result)
        self.assertEqual(len(calls), 3)

    def test_exhausted_retries_raise_final_exception(self):
        errors = [win_error(32), win_error(33), win_error(5)]
        calls = []

        def operation():
            calls.append(1)
            raise errors[len(calls) - 1]

        with mock.patch.object(install.sys, "platform", "win32"):
            with self.assertRaises(OSError) as raised:
                install._retry_windows_file_operation(
                    operation, attempts=3, delay=0,
                )
        self.assertIs(raised.exception, errors[-1])
        self.assertEqual(len(calls), 3)

    def test_unrelated_windows_error_is_not_retried(self):
        error = win_error(2)
        calls = []

        def operation():
            calls.append(1)
            raise error

        with mock.patch.object(install.sys, "platform", "win32"):
            with self.assertRaises(OSError) as raised:
                install._retry_windows_file_operation(
                    operation, attempts=4, delay=0,
                )
        self.assertIs(raised.exception, error)
        self.assertEqual(len(calls), 1)

    def test_non_windows_error_is_not_retried(self):
        error = win_error(32)
        calls = []

        def operation():
            calls.append(1)
            raise error

        with mock.patch.object(install.sys, "platform", "linux"):
            with self.assertRaises(OSError) as raised:
                install._retry_windows_file_operation(
                    operation, attempts=4, delay=0,
                )
        self.assertIs(raised.exception, error)
        self.assertEqual(len(calls), 1)


class RuntimePublicationRetry(unittest.TestCase):
    def test_unpack_openems_retries_atomic_flat_directory_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "İzmir çalışma" / "runtime"
            root.mkdir(parents=True)
            archive = root / "tiny-openems.zip"
            with zipfile.ZipFile(archive, "w") as packed:
                packed.writestr("archive-folder/bin/openEMS.exe", b"fake executable")
                packed.writestr("archive-folder/bin/solver.dll", b"fake library")
            target = root / "openEMS"
            real_replace = install.os.replace
            publications = []

            def replace_once(source, destination):
                if Path(destination) != target:
                    return real_replace(source, destination)
                source = Path(source)
                publications.append(source)
                self.assertTrue(source.is_dir())
                self.assertEqual((source / "openEMS.exe").read_bytes(), b"fake executable")
                self.assertFalse((source / "openEMS").exists())
                if len(publications) == 1:
                    raise win_error(32)
                self.assertEqual(source, publications[0])
                return real_replace(source, destination)

            with mock.patch.object(install.sys, "platform", "win32"), \
                    mock.patch.object(install.time, "sleep") as sleep, \
                    mock.patch.object(install.os, "replace", side_effect=replace_once):
                result = install.unpack_openems(archive, "msvc-zip", root, "test-digest")

            self.assertEqual(result, target)
            self.assertEqual(len(publications), 2)
            sleep.assert_called_once()
            self.assertEqual((target / "openEMS.exe").read_bytes(), b"fake executable")
            self.assertEqual((target / "solver.dll").read_bytes(), b"fake library")
            self.assertFalse((target / "openEMS").exists())
            self.assertEqual(json.loads((target / install.STAMP).read_text(encoding="utf-8"))["sha256"],
                             "test-digest")

    def test_install_app_retries_directory_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "İzmir çalışma"
            root = base / "runtime"
            package = base / "resources" / "python" / "fairbeam"
            site = base / "site packages"
            root.mkdir(parents=True)
            package.mkdir(parents=True)
            site.mkdir()
            (package / "__init__.py").write_text("from ._meta import __version__\n", encoding="utf-8")
            (package / "_meta.py").write_text("__version__ = '1.2.3'\n", encoding="utf-8")
            (package.parent / "fairbeam_elmer.py").write_text("# standalone adapter\n", encoding="utf-8")
            app = root / "app"
            staging = root / "app.new"
            real_replace = install.os.replace
            publications = []

            def replace_once(source, destination):
                self.assertEqual(Path(source), staging)
                self.assertEqual(Path(destination), app)
                publications.append(1)
                self.assertTrue((staging / "fairbeam" / "_meta.py").is_file())
                if len(publications) == 1:
                    raise win_error(32)
                return real_replace(source, destination)

            with mock.patch.object(install.sys, "platform", "win32"), \
                    mock.patch.object(install.time, "sleep") as sleep, \
                    mock.patch.object(install.os, "replace", side_effect=replace_once), \
                    mock.patch.object(install, "site_packages", return_value=site):
                version = install.install_app(root, base / "resources", Path("unused-python"))

            self.assertEqual(version, "1.2.3")
            self.assertEqual(len(publications), 2)
            sleep.assert_called_once()
            self.assertTrue((app / "fairbeam" / "__init__.py").is_file())
            self.assertEqual((app / "fairbeam_elmer.py").read_text(encoding="utf-8"), "# standalone adapter\n")
            self.assertFalse(staging.exists())
            self.assertEqual((site / "fairbeam.pth").read_text(encoding="utf-8"), str(app) + "\n")

    def test_packaged_elmer_adapter_imports_without_checkout_on_pythonpath(self):
        repo = INSTALL_PATH.parent.parent
        resources_map = json.loads((repo / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))["bundle"]["resources"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            resources = Path(tmp) / "resources"
            site = Path(tmp) / "site"
            root.mkdir(); site.mkdir()
            for source, target in resources_map.items():
                if not target.startswith("python/"):
                    continue
                for item in (repo / "src-tauri").glob(source):
                    dest = resources / target
                    if target.endswith("/"):
                        dest /= item.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dest)
            with mock.patch.object(install, "site_packages", return_value=site):
                install.install_app(root, resources, Path(sys.executable))
            code = "import sys; sys.path.insert(0, sys.argv[1]); import fairbeam_elmer; from pathlib import Path; assert Path(fairbeam_elmer.__file__).parent == Path(sys.argv[1]); from fairbeam import research; assert callable(fairbeam_elmer.run_cavity); assert research.probe({'backend':'elmer','path':sys.argv[2]})['available'] is False"
            result = subprocess.run([sys.executable, "-I", "-c", code, str(root / "app"), str(root / "missing-elmer")],
                                    cwd=tmp, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_adapter_does_not_replace_existing_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"
            app = root / "app"
            app.mkdir(parents=True)
            (app / "keep.txt").write_text("existing runtime")
            package = Path(tmp) / "resources/python/fairbeam"
            package.mkdir(parents=True)
            (package / "__init__.py").touch()
            with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                install.install_app(root, Path(tmp) / "resources", Path(sys.executable))
            self.assertEqual((app / "keep.txt").read_text(), "existing runtime")

    def test_manifest_replacement_retries_with_temp_source_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "İzmir çalışma" / "runtime"
            root.mkdir(parents=True)
            target = root / "manifest.json"
            target.write_text('{"old": true}', encoding="utf-8")
            source = root / "manifest.json.tmp"
            manifest = {"marker": "fairbeam runtime v1", "fairbeam": "1.2.3"}
            real_replace = install.os.replace
            publications = []

            def replace_once(actual_source, destination):
                self.assertEqual(Path(actual_source), source)
                self.assertEqual(Path(destination), target)
                publications.append(1)
                self.assertEqual(json.loads(source.read_text(encoding="utf-8")), manifest)
                if len(publications) == 1:
                    raise win_error(32)
                return real_replace(actual_source, destination)

            with mock.patch.object(install.sys, "platform", "win32"), \
                    mock.patch.object(install.time, "sleep") as sleep, \
                    mock.patch.object(install.os, "replace", side_effect=replace_once):
                install.write_manifest(root, manifest)

            self.assertEqual(len(publications), 2)
            sleep.assert_called_once()
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), manifest)
            self.assertFalse(source.exists())


class WindowsLockedFileIntegration(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows file sharing only")
    def test_locked_file_is_deleted_after_handle_releases(self):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        )
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        generic_read = 0x80000000
        share_read_write = 0x00000001 | 0x00000002  # Deliberately omit FILE_SHARE_DELETE.
        open_existing = 3
        file_attribute_normal = 0x80

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "İzmir çalışma"
            folder.mkdir()
            target = folder / "openEMS archive.zip"
            target.write_bytes(b"temporary")
            handle = kernel32.CreateFileW(
                str(target), generic_read, share_read_write, None,
                open_existing, file_attribute_normal, None,
            )
            if handle == wintypes.HANDLE(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())

            first_failure = threading.Event()
            close_errors = []
            observed_errors = []
            calls = []

            def release_handle():
                first_failure.wait(5)
                if not kernel32.CloseHandle(handle):
                    close_errors.append(ctypes.WinError(ctypes.get_last_error()))

            releaser = threading.Thread(target=release_handle, daemon=True)
            releaser.start()

            def delete_locked_file():
                calls.append(1)
                try:
                    target.unlink()
                except OSError as error:
                    observed_errors.append(error.winerror)
                    first_failure.set()
                    raise

            try:
                install._retry_windows_file_operation(
                    delete_locked_file, attempts=8, delay=0.05,
                )
            finally:
                first_failure.set()
                releaser.join(timeout=5)

            self.assertFalse(releaser.is_alive())
            self.assertEqual(close_errors, [])
            self.assertTrue(observed_errors)
            self.assertIn(observed_errors[0], {5, 32, 33})
            self.assertGreaterEqual(len(calls), 2)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
