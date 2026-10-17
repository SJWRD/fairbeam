"""Portable document storage: no solver, network, native dialog, or OS association required."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fairbeam import documents as doc
from fairbeam.design import blank_design


class Documents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Hosted Windows TEMP may use an 8.3 alias. Match the canonical parent
        # used by document access before comparing destination/staging paths.
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / "İzmir patch.fb"
        self.design = blank_design("my_patch", "İzmir patch 😀")
        self.document = doc.create(self.design, "İzmir patch.results")

    def result(self):
        folder = self.root / self.document["results_directory"]
        folder.mkdir(exist_ok=True)
        data = b'{"schema":"fairbeam.project/1","results":{}}\n'
        path = folder / "run.json"
        path.write_bytes(data)
        ref = {"file": path.name, "sha256": doc.digest(data),
               "design_sha256": doc.design_digest(self.document["design"])}
        self.document["results"].append(ref)
        return path, ref

    def test_unicode_roundtrip_and_owned_snapshot(self):
        self.design["model"]["name"] = "caller changed"
        initial = doc.save(self.path, self.document)
        restored, checksum = doc.read(self.path)
        self.assertEqual(restored, self.document)
        self.assertEqual(initial, checksum)
        self.assertEqual(restored["design"]["model"]["name"], "İzmir patch 😀")
        self.assertFalse((self.root / self.document["results_directory"]).exists())

    def test_design_digest_normalizes_browser_numbers_without_mutating_design(self):
        design = copy.deepcopy(self.design)
        design["extra_metadata"] = json.loads(
            '[32.0, -0.0, 1e20, 1e-7, 1.25, 9007199254740993, true, {"nested": [-6.0, 1e100]}]')
        browser = copy.deepcopy(design)
        browser["extra_metadata"] = json.loads(
            '[32, 0, 100000000000000000000, 0.0000001, 1.25, 9007199254740992, true, '
            '{"nested": [-6, 1e100]}]')
        before = json.dumps(design)
        self.assertEqual(doc.design_digest(design), doc.design_digest(browser))
        self.assertEqual(json.dumps(design), before)
        browser["extra_metadata"][6] = 1  # Boolean metadata is not the number one.
        self.assertNotEqual(doc.design_digest(design), doc.design_digest(browser))

    def test_design_digest_rejects_nonfinite_numbers_in_nested_metadata(self):
        for number in (float("nan"), float("inf"), -float("inf"), 10 ** 400):
            with self.subTest(number=str(number)[:30]):
                design = dict(self.design, extra_metadata={"nested": [number]})
                with self.assertRaises(doc.DocumentError):
                    doc.design_digest(design)

    def test_result_byte_digest_keeps_json_number_spelling(self):
        self.assertNotEqual(doc.digest(b'{"value":32.0}'), doc.digest(b'{"value":32}'))

    def test_shared_envelope_fixtures(self):
        fixtures = json.loads((Path(__file__).parent / "fixtures/document_envelopes.json").read_text(encoding="utf-8"))
        for group, cases in fixtures.items():
            for case in cases:
                with self.subTest(case=case["name"]):
                    value = dict(self.document, **case["set"])
                    if group == "valid":
                        doc.validate(value)
                    else:
                        with self.assertRaises(doc.DocumentError):
                            doc.validate(value)

    def test_save_requires_matching_revision_and_preserves_conflict(self):
        initial = doc.save(self.path, self.document)
        edited = copy.deepcopy(self.document)
        edited["design"]["model"]["name"] = "edited"
        for stale in (None, "0" * 64):
            with self.subTest(stale=stale), self.assertRaises(doc.DocumentConflict):
                doc.save(self.path, edited, stale)
        self.assertEqual(doc.read(self.path)[0], self.document)
        updated = doc.save(self.path, edited, initial)
        self.assertNotEqual(initial, updated)
        self.assertEqual(doc.read(self.path)[0], edited)

    def test_deleted_document_is_not_silently_recreated(self):
        initial = doc.save(self.path, self.document)
        self.path.unlink()
        with self.assertRaises(doc.DocumentConflict):
            doc.save(self.path, self.document, initial)
        self.assertFalse(self.path.exists())

    def test_identical_save_does_not_replace_file(self):
        initial = doc.save(self.path, self.document)
        with mock.patch.object(doc.os, "replace", side_effect=AssertionError("unnecessary replace")):
            self.assertEqual(doc.save(self.path, self.document, initial), initial)

    def test_replace_failure_keeps_original_and_cleans_temporary(self):
        initial = doc.save(self.path, self.document)
        self.document["design"]["model"]["name"] = "edited"
        with mock.patch.object(doc.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaisesRegex(OSError, "disk error"):
                doc.save(self.path, self.document, initial)
        self.assertEqual(doc.read(self.path)[1], initial)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_fsync_failure_publishes_nothing(self):
        with mock.patch.object(doc.os, "fsync", side_effect=OSError("flush error")):
            with self.assertRaises(OSError):
                doc.save(self.path, self.document)
        self.assertEqual(list(self.root.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows readonly replacement semantics")
    def test_readonly_replacement_keeps_original_without_temporary_file(self):
        import stat
        initial = doc.save(self.path, self.document)
        self.path.chmod(stat.S_IREAD)
        self.addCleanup(lambda: self.path.chmod(stat.S_IREAD | stat.S_IWRITE))
        self.document["design"]["model"]["name"] = "edited"
        with mock.patch.object(doc, "_retry_busy", lambda action: action()):
            with self.assertRaises(PermissionError):
                doc.save(self.path, self.document, initial)
        self.assertEqual(doc.read(self.path)[1], initial)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def windows_acl(self, path, sddl, protected):
        """Install an independent native ACL fixture, including normal parent inheritance."""
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("advapi32", use_last_error=True)
        api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p)
        api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        api.GetSecurityDescriptorDacl.argtypes = (ctypes.c_void_p,
            ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.BOOL))
        api.GetSecurityDescriptorDacl.restype = wintypes.BOOL
        api.SetNamedSecurityInfoW.argtypes = (wintypes.LPWSTR, ctypes.c_int,
            wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        api.SetNamedSecurityInfoW.restype = wintypes.DWORD
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = (ctypes.c_void_p,)
        kernel.LocalFree.restype = ctypes.c_void_p
        descriptor = ctypes.c_void_p()
        if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
            if not api.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present),
                                                ctypes.byref(dacl), ctypes.byref(defaulted)):
                raise ctypes.WinError(ctypes.get_last_error())
            flags = 4 | (0x80000000 if protected else 0x20000000)
            error = api.SetNamedSecurityInfoW(str(path), 1, flags, None, None, dacl, None)
            if error:
                raise ctypes.WinError(error)
        finally:
            kernel.LocalFree(descriptor)

    @unittest.skipUnless(os.name == "nt", "Windows destination DACL preservation")
    def test_windows_replacement_preserves_protected_and_inherited_dacls(self):
        initial = doc.save(self.path, self.document)
        for protected in (True, False):
            with self.subTest(protected=protected):
                self.windows_acl(self.path, "D:(A;;FA;;;OW)", protected)
                before = doc._windows_read_dacl(self.path)
                self.document["design"]["model"]["name"] = f"edited {protected}"
                initial = doc.save(self.path, self.document, initial)
                self.assertEqual(doc._windows_read_dacl(self.path), before)
                self.assertEqual(doc.read(self.path)[0], self.document)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    @unittest.skipUnless(os.name == "nt", "Windows owner/group preservation")
    def test_windows_owner_group_is_checked_before_content_and_stays_unchanged(self):
        initial = doc.save(self.path, self.document)
        before = doc._windows_read_owner_group(self.path)
        check = doc._windows_require_owner_group
        def check_empty_stage(path, expected):
            self.assertEqual(path.stat().st_size, 0)
            self.assertEqual(expected, before)
            check(path, expected)
        self.document["design"]["model"]["name"] = "edited"
        with mock.patch.object(doc, "_windows_require_owner_group", side_effect=check_empty_stage) as checked:
            doc.save(self.path, self.document, initial)
        checked.assert_called_once()
        self.assertEqual(doc._windows_read_owner_group(self.path), before)
        self.assertEqual(doc.read(self.path)[0], self.document)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    @unittest.skipUnless(os.name == "nt", "Windows owner/group refusal")
    def test_windows_different_owner_or_group_refuses_before_bytes_or_dacl_changes(self):
        initial = doc.save(self.path, self.document)
        before = doc._windows_read_owner_group(self.path)
        before_acl = doc._windows_read_dacl(self.path)
        read = doc._windows_read_owner_group
        self.document["design"]["model"]["name"] = "edited"
        for index in (0, 1):
            with self.subTest(identity=("owner", "group")[index]):
                different = list(before)
                # Change one subauthority byte, retaining a structurally valid distinct SID.
                sid = bytearray(different[index]); sid[-1] ^= 1
                different[index] = bytes(sid)
                def destination_differs(path):
                    if path == self.path:
                        return tuple(different)
                    self.assertEqual(path.stat().st_size, 0)
                    return read(path)
                with mock.patch.object(doc, "_windows_read_owner_group", side_effect=destination_differs), \
                        mock.patch.object(doc, "_windows_apply_dacl") as apply:
                    with self.assertRaisesRegex(doc.DocumentError, "owner and group"):
                        doc.save(self.path, self.document, initial)
                    apply.assert_not_called()
                self.assertEqual(doc.read(self.path)[1], initial)
                self.assertEqual(read(self.path), before)
                self.assertEqual(doc._windows_read_dacl(self.path), before_acl)
                self.assertEqual(list(self.root.iterdir()), [self.path])

    @unittest.skipUnless(os.name == "nt", "Windows ownership read failure")
    def test_windows_unreadable_owner_group_keeps_original_and_cleans_empty_stage(self):
        initial = doc.save(self.path, self.document)
        before = doc._windows_read_owner_group(self.path)
        read = doc._windows_read_owner_group
        self.document["design"]["model"]["name"] = "edited"
        for target in ("destination", "stage"):
            with self.subTest(target=target):
                def refused(path):
                    if (path == self.path) == (target == "destination"):
                        if path != self.path:
                            self.assertEqual(path.stat().st_size, 0)
                        raise PermissionError("owner/group read refused")
                    return read(path)
                with mock.patch.object(doc, "_windows_read_owner_group", side_effect=refused):
                    with self.assertRaisesRegex(PermissionError, "owner/group read refused"):
                        doc.save(self.path, self.document, initial)
                self.assertEqual(doc.read(self.path)[1], initial)
                self.assertEqual(read(self.path), before)
                self.assertEqual(list(self.root.iterdir()), [self.path])

    @unittest.skipUnless(os.name == "nt", "Windows DACL failure handling")
    def test_windows_acl_failure_never_publishes_and_cleans_empty_stage(self):
        initial = doc.save(self.path, self.document)
        self.document["design"]["model"]["name"] = "edited"
        original_acl = doc._windows_read_dacl(self.path)
        for helper in ("_windows_read_dacl", "_windows_apply_dacl"):
            with self.subTest(helper=helper):
                def refused(*args):
                    for staging in self.root.glob("*.tmp"):
                        self.assertEqual(staging.stat().st_size, 0)
                    raise PermissionError("ACL refused")
                with mock.patch.object(doc, helper, side_effect=refused):
                    with self.assertRaisesRegex(PermissionError, "ACL refused"):
                        doc.save(self.path, self.document, initial)
                self.assertEqual(doc.read(self.path)[1], initial)
                self.assertEqual(doc._windows_read_dacl(self.path), original_acl)
                self.assertEqual(list(self.root.iterdir()), [self.path])

    @unittest.skipUnless(os.name == "nt", "Windows staging permissions")
    def test_windows_stage_gets_destination_dacl_before_document_bytes(self):
        initial = doc.save(self.path, self.document)
        self.windows_acl(self.path, "D:(A;;FA;;;OW)", True)
        before = doc._windows_read_dacl(self.path)
        apply = doc._windows_apply_dacl
        def restrict(path, descriptor):
            self.assertEqual(path.stat().st_size, 0)
            self.assertEqual(descriptor, before)
            apply(path, descriptor)
            self.assertEqual(doc._windows_read_dacl(path), before)
        self.document["design"]["model"]["name"] = "edited"
        with mock.patch.object(doc, "_windows_apply_dacl", side_effect=restrict) as applied:
            doc.save(self.path, self.document, initial)
        applied.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Windows DACL verification failure")
    def test_windows_unverifiable_dacl_keeps_original_and_cleans_empty_stage(self):
        initial = doc.save(self.path, self.document)
        self.windows_acl(self.path, "D:(A;;FA;;;OW)", True)
        before = doc._windows_read_dacl(self.path)
        read = doc._windows_read_dacl
        def unverifiable(path):
            if path == self.path:
                return read(path)
            self.assertEqual(path.stat().st_size, 0)
            return b"different staging DACL"
        self.document["design"]["model"]["name"] = "edited"
        with mock.patch.object(doc, "_windows_read_dacl", side_effect=unverifiable):
            with self.assertRaisesRegex(doc.DocumentError, "DACL exactly"):
                doc.save(self.path, self.document, initial)
        self.assertEqual(doc.read(self.path)[1], initial)
        self.assertEqual(doc._windows_read_dacl(self.path), before)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    @unittest.skipUnless(os.name == "nt", "Windows cleanup with restrictive DACL")
    def test_windows_replace_failure_removes_stage_even_if_dacl_denies_delete(self):
        initial = doc.save(self.path, self.document)
        self.windows_acl(self.path, "D:(D;;SD;;;OW)(A;;FA;;;OW)", True)
        self.addCleanup(lambda: self.windows_acl(self.path, "D:(A;;FA;;;OW)", True))
        before = doc._windows_read_dacl(self.path)
        self.document["design"]["model"]["name"] = "edited"
        with mock.patch.object(doc.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaisesRegex(OSError, "disk error"):
                doc.save(self.path, self.document, initial)
        self.assertEqual(doc.read(self.path)[1], initial)
        self.assertEqual(doc._windows_read_dacl(self.path), before)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    @unittest.skipIf(os.name == "nt", "POSIX permission preservation")
    def test_posix_mode_survives_atomic_replacement(self):
        import stat
        initial = doc.save(self.path, self.document)
        self.path.chmod(0o640)
        self.document["design"]["model"]["name"] = "edited"
        doc.save(self.path, self.document, initial)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)

    def test_only_one_concurrent_writer_can_save_same_revision(self):
        initial = doc.save(self.path, self.document)
        start = threading.Barrier(2)
        results = []
        def write(name):
            changed = copy.deepcopy(self.document)
            changed["design"]["model"]["name"] = name
            start.wait(timeout=5)
            try:
                doc.save(self.path, changed, initial)
                results.append("saved")
            except doc.DocumentConflict:
                results.append("conflict")
        threads = [threading.Thread(target=write, args=(name,)) for name in ("one", "two")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(results, ["saved", "conflict"])
        self.assertIn(doc.read(self.path)[0]["design"]["model"]["name"], {"one", "two"})

    def test_missing_sidecar_does_not_block_design(self):
        result_path, reference = self.result()
        doc.save(self.path, self.document)
        result_path.unlink()
        result_path.parent.rmdir()
        self.assertEqual(doc.read(self.path)[0], self.document)
        with self.assertRaises(FileNotFoundError):
            doc.read_result(self.path, self.document, reference)

    def test_result_checksum_and_membership(self):
        result_path, reference = self.result()
        self.assertEqual(doc.read_result(self.path, self.document, reference)["results"], {})
        with self.assertRaisesRegex(doc.DocumentError, "belong"):
            doc.read_result(self.path, self.document, dict(reference, file="other.json"))
        result_path.write_bytes(b'{"schema":"fairbeam.project/1","results":{"changed":true}}')
        with self.assertRaisesRegex(doc.DocumentError, "checksum"):
            doc.read_result(self.path, self.document, reference)

    def test_result_hash_does_not_claim_current_geometry(self):
        _, reference = self.result()
        old = reference["design_sha256"]
        self.document["design"]["parts"][2]["primitives"][0]["stop"][0] = "W"
        doc.validate(self.document)
        self.assertNotEqual(old, doc.design_digest(self.document["design"]))
        self.assertEqual(doc.read_result(self.path, self.document, reference)["schema"], "fairbeam.project/1")

    def test_checksums_do_not_bypass_result_schema_or_size_checks(self):
        result_path, reference = self.result()
        for data in (b'[]', b'{"schema":"other"}', b'{"schema":"fairbeam.project/1","x":NaN}',
                     b'{"schema":"fairbeam.project/1","x":1,"x":2}'):
            result_path.write_bytes(data)
            reference["sha256"] = doc.digest(data)
            with self.subTest(data=data), self.assertRaises(doc.DocumentError):
                doc.read_result(self.path, self.document, reference)
        with mock.patch.object(doc, "MAX_RESULT", 10), self.assertRaisesRegex(doc.DocumentError, "limit"):
            doc.read_result(self.path, self.document, reference)

    def test_portable_reference_names(self):
        for name in ("../outside", "/tmp", "C:\\outside", "x/y", "x\\y", "x:stream", "NUL", "COM1.txt", "LPT²", "..", "x.", "x ", "a\0b"):
            with self.subTest(name=name), self.assertRaises(doc.DocumentError):
                doc.create(self.design, name)
        doc.create(self.design, "😀" * 45)
        for name in ("😀" * 46, "a" * 181, "unpaired\ud800"):
            with self.subTest(name=repr(name)), self.assertRaises(doc.DocumentError):
                doc.create(self.design, name)

    def test_reject_ambiguous_and_unknown_envelope(self):
        for change in ({"schema": "fairbeam.document/2"}, {"id": "not-uuid"}, {"results": {}}, {"extra": True}, {"design": None}):
            with self.subTest(change=change), self.assertRaises(doc.DocumentError):
                doc.validate(dict(self.document, **change))
        _, ref = self.result()
        self.document["results"].append(dict(ref, file="RUN.json"))
        with self.assertRaisesRegex(doc.DocumentError, "distinct"):
            doc.validate(self.document)

    def test_reject_malformed_nonfinite_duplicate_json(self):
        serialized = json.dumps(self.document)
        for data in (b'\xff', b'[]', b'{', serialized.replace('"schema":', '"schema":"duplicate","schema":', 1).encode()):
            with self.subTest(data=data[:30]):
                self.path.write_bytes(data)
                with self.assertRaises(doc.DocumentError):
                    doc.read(self.path)
        self.document["design"]["extra_metadata"] = float("nan")
        with self.assertRaises(doc.DocumentError):
            doc.save(self.path, self.document, doc.digest(self.path.read_bytes()))

    def test_read_bom_and_limits(self):
        self.path.write_text(json.dumps(self.document), encoding="utf-8-sig")
        self.assertEqual(doc.read(self.path)[0], self.document)
        with mock.patch.object(doc, "MAX_DOCUMENT", 10), self.assertRaisesRegex(doc.DocumentError, "limit"):
            doc.read(self.path)
        with mock.patch.object(doc, "MAX_DOCUMENT", 10), self.assertRaisesRegex(doc.DocumentError, "limit"):
            doc.save(self.root / "too-large.fb", self.document)
        self.assertFalse((self.root / "too-large.fb").exists())

    def test_extension_and_directory_refused(self):
        with self.assertRaises(doc.DocumentError):
            doc.save(self.root / "old.json", self.document)
        self.path.mkdir()
        with self.assertRaises(doc.DocumentError):
            doc.read(self.path)

    def make_link(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError as error:
            self.skipTest(f"OS cannot create symlink: {error}")

    def test_document_link_refused_without_touching_target(self):
        target = self.root / "target.fb"
        initial = doc.save(target, self.document)
        self.make_link(self.path, target)
        for action in (lambda: doc.read(self.path), lambda: doc.save(self.path, self.document, initial)):
            with self.assertRaises(doc.DocumentError):
                action()
        self.assertEqual(doc.read(target)[1], initial)

    def test_result_link_refused(self):
        result_path, reference = self.result()
        target = self.root / "outside.json"
        result_path.rename(target)
        self.make_link(result_path, target)
        with self.assertRaises(doc.DocumentError):
            doc.read_result(self.path, self.document, reference)

    def test_result_directory_link_refused(self):
        result_path, reference = self.result()
        folder = result_path.parent
        outside = self.root / "outside"
        folder.rename(outside)
        self.make_link(folder, outside, directory=True)
        with self.assertRaisesRegex(doc.DocumentError, "link"):
            doc.read_result(self.path, self.document, reference)
        self.assertTrue((outside / result_path.name).exists())

    def test_directory_reparse_flag_refused(self):
        _, reference = self.result()
        real_lstat = Path.lstat
        folder = self.root / self.document["results_directory"]
        inspected = []
        def lstat(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if path == folder:
                inspected.append(path)
                return mock.Mock(st_mode=info.st_mode, st_file_attributes=0x400)
            return info
        with mock.patch.object(Path, "lstat", lstat), self.assertRaisesRegex(doc.DocumentError, "link"):
            doc.read_result(self.path, self.document, reference)
        self.assertEqual(inspected, [folder])


if __name__ == "__main__":
    unittest.main()
