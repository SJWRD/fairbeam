"""Portable document storage: no solver, network, native dialog, or OS association required."""
import copy
import ctypes
import errno
import json
import os
from pathlib import Path
import stat
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

    def test_macos_lock_serializes_aliases_and_other_documents_in_same_directory(self):
        other = self.root / "other"
        other.mkdir()
        with mock.patch.object(doc.sys, "platform", "darwin"):
            keys = {doc._document_lock_key(self.root / name) for name in (
                "É.fb", "e\u0301.fb", "é.fb", "unrelated.fb", "A.fb", "A\u200c.fb")}
            self.assertEqual(len(keys), 1)
            self.assertNotIn(doc._document_lock_key(other / "É.fb"), keys)
        info = self.root.stat()
        self.assertEqual(keys, {(f"{info.st_dev}:{info.st_ino}", "fb-document-directory")})

    def test_non_macos_document_lock_keeps_existing_identity(self):
        for platform in ("linux", "win32"):
            with self.subTest(platform=platform), mock.patch.object(doc.sys, "platform", platform):
                self.assertEqual(doc._document_lock_key(self.path),
                                 (os.path.normcase(str(self.path)), "fb-document"))

    def test_macos_exclusive_rename_ffi_and_existing_destination_error(self):
        source = self.root / "é staging.tmp"
        rename = mock.Mock(return_value=0)
        with mock.patch.object(ctypes, "CDLL", return_value=mock.Mock(renamex_np=rename)) as library:
            doc._macos_rename_exclusive(source, self.path)
            library.assert_called_once_with(None, use_errno=True)
            rename.assert_called_once_with(os.fsencode(source), os.fsencode(self.path), 4)
            self.assertEqual(rename.argtypes, (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint))
            self.assertEqual(rename.restype, ctypes.c_int)
            rename.return_value = -1
            with mock.patch.object(ctypes, "get_errno", return_value=errno.EEXIST):
                with self.assertRaises(FileExistsError) as raised:
                    doc._macos_rename_exclusive(source, self.path)
            self.assertEqual(raised.exception.errno, errno.EEXIST)
            self.assertEqual(raised.exception.filename, str(self.path))

    def test_missing_macos_exclusive_rename_symbol_is_unsupported(self):
        with mock.patch.object(ctypes, "CDLL", return_value=object()):
            with self.assertRaises(OSError) as raised:
                doc._macos_rename_exclusive(self.root / "stage.tmp", self.path)
        self.assertEqual(raised.exception.errno, errno.ENOSYS)

    def test_macos_create_uses_exclusive_rename_without_hardlinks(self):
        def publish(source, destination):
            self.assertFalse(destination.exists())
            # Windows' retained DELETE handle requires share-delete readers;
            # check the complete flushed size here and the exact bytes afterward.
            self.assertEqual(source.stat().st_size, len(doc._json_bytes(self.document)))
            os.rename(source, destination)
        with mock.patch.object(doc.sys, "platform", "darwin"), \
                mock.patch.object(doc, "_macos_rename_exclusive", side_effect=publish) as rename, \
                mock.patch.object(doc.os, "link", side_effect=AssertionError("hardlinks unavailable")):
            revision = doc.save(self.path, self.document)
        rename.assert_called_once()
        self.assertEqual(doc.read(self.path), (self.document, revision))
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_macos_unsupported_exclusive_rename_falls_back_to_complete_hardlink(self):
        link = os.link
        def publish(source, destination):
            self.assertFalse(destination.exists())
            self.assertEqual(source.stat().st_size, len(doc._json_bytes(self.document)))
            link(source, destination)
        for error in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS):
            with self.subTest(error=error), mock.patch.object(doc.sys, "platform", "darwin"), \
                    mock.patch.object(doc, "_macos_rename_exclusive", side_effect=OSError(error, "unsupported")), \
                    mock.patch.object(doc.os, "link", side_effect=publish) as linked:
                doc.save(self.path, self.document)
            linked.assert_called_once()
            self.assertEqual(doc.read(self.path)[0], self.document)
            self.assertEqual(list(self.root.iterdir()), [self.path])
            self.path.unlink()

    def test_macos_unsupported_publication_never_creates_partial_destination(self):
        with mock.patch.object(doc.sys, "platform", "darwin"), \
                mock.patch.object(doc, "_macos_rename_exclusive", side_effect=OSError(errno.ENOTSUP, "unsupported")), \
                mock.patch.object(doc.os, "link", side_effect=OSError(errno.ENOTSUP, "unsupported")):
            with self.assertRaises(doc.DocumentError) as raised:
                doc.save(self.path, self.document)
        self.assertEqual(
            str(raised.exception),
            "This drive does not support the atomic file creation required by Fairbeam. "
            "Save on an internal disk, then copy the .fb file and its adjacent results folder together.",
        )
        self.assertEqual(list(self.root.iterdir()), [])

    def test_macos_rename_errors_do_not_fall_back(self):
        for error in (errno.EACCES, errno.EIO, errno.EXDEV, errno.EINVAL):
            with self.subTest(error=error), mock.patch.object(doc.sys, "platform", "darwin"), \
                    mock.patch.object(doc, "_macos_rename_exclusive", side_effect=OSError(error, "refused")), \
                    mock.patch.object(doc.os, "link") as linked:
                with self.assertRaises(OSError) as raised:
                    doc.save(self.path, self.document)
                self.assertEqual(raised.exception.errno, error)
                linked.assert_not_called()
            self.assertEqual(list(self.root.iterdir()), [])

    def test_macos_competing_create_is_a_conflict_without_overwrite(self):
        winner = b"another writer created this file"
        def competing(source, destination):
            destination.write_bytes(winner)
            raise FileExistsError(errno.EEXIST, "destination exists", str(destination))
        with mock.patch.object(doc.sys, "platform", "darwin"), \
                mock.patch.object(doc, "_macos_rename_exclusive", side_effect=competing), \
                mock.patch.object(doc.os, "link") as linked:
            with self.assertRaises(doc.DocumentConflict):
                doc.save(self.path, self.document)
            linked.assert_not_called()
        self.assertEqual(self.path.read_bytes(), winner)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_result_normalization_preserves_reference_spelling(self):
        reference = {"file": "e\u0301.json", "sha256": "0" * 64, "design_sha256": "1" * 64}
        self.document["results"] = [reference]
        doc.validate(self.document)
        doc.save(self.path, self.document)
        self.assertEqual(doc.read(self.path)[0]["results"], [reference])

    def macos_metadata_api(self, attributes=0, acl=None):
        def absent(*args):
            ctypes.set_errno(errno.ENOENT)
            return None
        return mock.Mock(flistxattr=mock.Mock(return_value=attributes),
                         acl_get_fd_np=mock.Mock(side_effect=absent) if acl is None else mock.Mock(return_value=acl),
                         acl_free=mock.Mock(return_value=0))

    def test_posix_plain_file_metadata_is_read_from_open_descriptor(self):
        self.path.write_bytes(b"original")
        with mock.patch.object(doc.sys, "platform", "linux"), \
                mock.patch.object(doc.os, "listxattr", return_value=[], create=True) as attributes:
            info = doc._posix_plain_metadata(self.path)
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertIsInstance(attributes.call_args.args[0], int)
        self.assertEqual(self.path.read_bytes(), b"original")

    def test_macos_metadata_snapshot_reads_binary_attributes_and_acl(self):
        acl_bytes = b"!#acl 1\nuser:uuid:allow:read\n"
        acl_buffer = ctypes.create_string_buffer(acl_bytes)
        api = self.macos_metadata_api(acl=1234)
        def raw(value, buffer, size):
            if buffer is not None:
                ctypes.memmove(buffer, value, len(value))
            return len(value)
        api.flistxattr.side_effect = lambda fd, buf, size, options: raw(b"tag\0", buf, size)
        api.fgetxattr.side_effect = lambda fd, key, buf, size, pos, options: raw(b"\0binary\xff", buf, size)
        def text(acl, size):
            ctypes.cast(size, ctypes.POINTER(ctypes.c_ssize_t))[0] = len(acl_bytes)
            return ctypes.addressof(acl_buffer)
        api.acl_to_text.side_effect = text
        with mock.patch.object(ctypes, "CDLL", return_value=api):
            self.assertEqual(doc._macos_metadata(17), ({b"tag": b"\0binary\xff"}, acl_bytes))
        api.acl_free.assert_has_calls([mock.call(ctypes.addressof(acl_buffer)), mock.call(1234)])
        self.assertEqual(api.fgetxattr.call_args.args[0:2], (17, b"tag"))
        self.assertEqual(api.fgetxattr.call_args.args[4:], (0, 0x20))

    def test_macos_metadata_snapshot_refuses_incomplete_reads_and_acl_errors(self):
        api = self.macos_metadata_api()
        api.flistxattr.side_effect = [4, 3]
        with mock.patch.object(ctypes, "CDLL", return_value=api), self.assertRaises(OSError):
            doc._macos_metadata(17)
        api = self.macos_metadata_api(acl=1234)
        api.acl_to_text.return_value = None
        with mock.patch.object(ctypes, "CDLL", return_value=api), self.assertRaises(OSError):
            doc._macos_metadata(17)
        api.acl_free.assert_called_once_with(1234)

    def test_macos_absent_acl_is_distinct_from_explicit_empty_acl_with_flags(self):
        api = self.macos_metadata_api()
        with mock.patch.object(ctypes, "CDLL", return_value=api):
            self.assertEqual(doc._macos_metadata(17), ({}, None))
        text_bytes = b"!#acl 1 no_inherit\n"
        buffer = ctypes.create_string_buffer(text_bytes)
        api = self.macos_metadata_api(acl=1234)
        def text(acl, size):
            ctypes.cast(size, ctypes.POINTER(ctypes.c_ssize_t))[0] = len(text_bytes)
            return ctypes.addressof(buffer)
        api.acl_to_text.side_effect = text
        with mock.patch.object(ctypes, "CDLL", return_value=api):
            self.assertEqual(doc._macos_metadata(17), ({}, text_bytes))

    def test_macos_metadata_query_errors_are_not_treated_as_absence(self):
        for operation in ("flistxattr", "acl_get_fd_np"):
            for error in (errno.EACCES, errno.ENOTSUP, 0):
                api = self.macos_metadata_api()
                def failed(*args):
                    ctypes.set_errno(error)
                    return None if operation == "acl_get_fd_np" else -1
                getattr(api, operation).side_effect = failed
                with self.subTest(operation=operation, error=error), \
                        mock.patch.object(ctypes, "CDLL", return_value=api), self.assertRaises(OSError):
                    doc._macos_metadata(17)

    def test_posix_xattrs_acls_and_query_errors_refuse_without_mutation(self):
        self.path.write_bytes(b"original")
        for names in (["user.tag"], ["system.posix_acl_access"]):
            with self.subTest(names=names), mock.patch.object(doc.sys, "platform", "linux"), \
                    mock.patch.object(doc.os, "listxattr", return_value=names, create=True):
                with self.assertRaisesRegex(doc.DocumentError, "save a separate copy"):
                    doc._posix_plain_metadata(self.path)
        for error in (PermissionError("denied"), OSError(errno.ENOTSUP, "unsupported"), AttributeError("unavailable")):
            with self.subTest(error=error), mock.patch.object(doc.sys, "platform", "linux"), \
                    mock.patch.object(doc.os, "listxattr", side_effect=error, create=True):
                with self.assertRaisesRegex(doc.DocumentError, "Cannot verify"):
                    doc._posix_plain_metadata(self.path)
        self.assertEqual(self.path.read_bytes(), b"original")
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_posix_file_flags_refuse_without_metadata_changes(self):
        self.path.write_bytes(b"original")
        with mock.patch.object(doc.os, "fstat", return_value=mock.Mock(st_mode=stat.S_IFREG, st_flags=2)), \
                mock.patch.object(doc.os, "listxattr", create=True) as attributes:
            with self.assertRaisesRegex(doc.DocumentError, "file flags; save a separate copy"):
                doc._posix_plain_metadata(self.path)
        attributes.assert_not_called()
        self.assertEqual(self.path.read_bytes(), b"original")

    def test_posix_special_mode_bits_refuse_before_permission_changes_or_content(self):
        self.path.write_bytes(b"original")
        for mode in (stat.S_ISUID, stat.S_ISGID, stat.S_ISVTX):
            with self.subTest(mode=mode), \
                    mock.patch.object(doc.os, "fstat", return_value=mock.Mock(st_mode=stat.S_IFREG | mode, st_flags=0)), \
                    mock.patch.object(doc.os, "listxattr", create=True) as attributes, \
                    mock.patch.object(doc.os, "chmod") as chmod:
                with self.assertRaisesRegex(doc.DocumentError, "special mode bits; save a separate copy"):
                    doc._posix_plain_metadata(self.path)
                attributes.assert_not_called()
                chmod.assert_not_called()
            self.assertEqual(self.path.read_bytes(), b"original")

    def test_posix_owner_group_mismatch_refuses_empty_stage_before_chmod(self):
        self.path.write_bytes(b"")
        expected = mock.Mock(st_uid=501, st_gid=20, st_mode=stat.S_IFREG | 0o640)
        for uid, gid in ((502, 20), (501, 21)):
            with self.subTest(uid=uid, gid=gid), \
                    mock.patch.object(doc, "_posix_plain_metadata", return_value=mock.Mock(st_uid=uid, st_gid=gid)), \
                    mock.patch.object(doc.os, "chmod") as chmod:
                with self.assertRaisesRegex(doc.DocumentError, "owner and group; save a separate copy"):
                    doc._posix_prepare_staging(self.path, expected)
                chmod.assert_not_called()
                self.assertEqual(self.path.stat().st_size, 0)

    @unittest.skipIf(os.name == "nt", "POSIX replacement metadata policy")
    def test_posix_metadata_failure_preserves_original_and_cleans_empty_stage(self):
        initial = doc.save(self.path, self.document)
        self.document["design"]["model"]["name"] = "edited"
        plain = doc._posix_plain_metadata
        for target in ("destination", "stage"):
            with self.subTest(target=target):
                def refused(path):
                    if (path == self.path) == (target == "destination"):
                        if path != self.path:
                            self.assertEqual(path.stat().st_size, 0)
                        raise doc.DocumentError("Cannot verify metadata; save a separate copy")
                    return plain(path)
                with mock.patch.object(doc, "_posix_plain_metadata", side_effect=refused), \
                        mock.patch.object(doc.os, "replace") as replace:
                    with self.assertRaisesRegex(doc.DocumentError, "save a separate copy"):
                        doc.save(self.path, self.document, initial)
                    replace.assert_not_called()
                self.assertEqual(doc.read(self.path)[1], initial)
                self.assertEqual(list(self.root.iterdir()), [self.path])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux native extended attribute refusal")
    def test_linux_replacement_preserves_existing_xattr_by_refusing(self):
        initial = doc.save(self.path, self.document)
        try:
            os.setxattr(self.path, "user.fairbeam-test", b"kept")
        except OSError as exc:
            if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
                self.skipTest("Test volume does not support extended attributes")
            raise
        self.document["design"]["model"]["name"] = "edited"
        with self.assertRaisesRegex(doc.DocumentError, "save a separate copy"):
            doc.save(self.path, self.document, initial)
        self.assertEqual(doc.read(self.path)[1], initial)
        self.assertEqual(os.getxattr(self.path, "user.fairbeam-test"), b"kept")
        self.assertEqual(list(self.root.iterdir()), [self.path])

    @unittest.skipIf(os.name == "nt", "POSIX owner/group replacement refusal")
    def test_posix_save_owner_mismatch_preserves_original_and_removes_empty_stage(self):
        initial = doc.save(self.path, self.document)
        self.document["design"]["model"]["name"] = "edited"
        plain = doc._posix_plain_metadata
        def different_owner(path):
            info = plain(path)
            if path == self.path:
                return mock.Mock(st_uid=info.st_uid + 1, st_gid=info.st_gid, st_mode=info.st_mode)
            self.assertEqual(path.stat().st_size, 0)
            return info
        with mock.patch.object(doc, "_posix_plain_metadata", side_effect=different_owner), \
                mock.patch.object(doc.os, "replace") as replace:
            with self.assertRaisesRegex(doc.DocumentError, "owner and group"):
                doc.save(self.path, self.document, initial)
            replace.assert_not_called()
        self.assertEqual(doc.read(self.path)[1], initial)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    @unittest.skipUnless(sys.platform == "darwin", "macOS native metadata preservation")
    def test_macos_replacement_preserves_acl_finder_tags_and_lastuseddate(self):
        import plistlib
        import subprocess
        initial = doc.save(self.path, self.document)
        self.path.chmod(0o640)
        subprocess.run(["chmod", "+a", "group:staff allow read,readattr", str(self.path)], check=True)
        tag = "com.apple.metadata:_kMDItemUserTags"
        value = plistlib.dumps(["Fairbeam\n6"], fmt=plistlib.FMT_BINARY).hex()
        subprocess.run(["xattr", "-wx", tag, value, str(self.path)], check=True)
        subprocess.run(["xattr", "-wx", "com.apple.lastuseddate#PS", "01000000000000000200000000000000",
                        str(self.path)], check=True)
        with self.path.open("rb") as source:
            before = doc._macos_metadata(source.fileno())
        owner = self.path.stat()
        self.document["design"]["model"]["name"] = "edited"
        changed = doc.save(self.path, self.document, initial)
        self.assertNotEqual(changed, initial)
        self.assertEqual(doc.read(self.path)[0], self.document)
        with self.path.open("rb") as source:
            self.assertEqual(doc._macos_metadata(source.fileno()), before)
        after = self.path.stat()
        self.assertEqual((after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)),
                         (owner.st_uid, owner.st_gid, 0o640))
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_macos_descriptor_copy_verifies_metadata_and_rejects_copy_failure(self):
        self.path.write_bytes(b"original")
        stage = self.root / "stage"
        stage.write_bytes(b"")
        metadata = ({b"tag": b"value\0"}, b"acl text")
        api = mock.Mock(fcopyfile=mock.Mock(return_value=0))
        with stage.open("wb") as target, mock.patch.object(ctypes, "CDLL", return_value=api), \
                mock.patch.object(doc.os, "O_NOFOLLOW", 0, create=True), \
                mock.patch.object(doc, "_macos_metadata", return_value=metadata):
            expected = self.path.stat()
            self.assertEqual(doc._macos_preserve_metadata(self.path, target.fileno(), expected), metadata)
            self.assertEqual(api.fcopyfile.call_args.args[1:], (target.fileno(), None, 5))
            self.assertEqual(api.fcopyfile.argtypes,
                             (ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32))
            self.assertEqual(api.fcopyfile.restype, ctypes.c_int)
            api.fcopyfile.return_value = -1
            with self.assertRaisesRegex(doc.DocumentError, "Cannot preserve"):
                doc._macos_preserve_metadata(self.path, target.fileno(), expected)
        self.assertEqual(self.path.read_bytes(), b"original")
        self.assertEqual(stage.read_bytes(), b"")

    def test_macos_metadata_mismatch_never_accepts_staging(self):
        self.path.write_bytes(b"original")
        stage = self.root / "stage"
        stage.write_bytes(b"")
        original = ({b"tag": b"kept"}, b"acl")
        for changed in (({b"tag": b"lost"}, b"acl"), ({b"tag": b"kept"}, b"different ACL")):
            with self.subTest(changed=changed), stage.open("wb") as target, \
                    mock.patch.object(doc.os, "O_NOFOLLOW", 0, create=True), \
                    mock.patch.object(doc, "_macos_metadata", side_effect=[original, original, changed]):
                with self.assertRaisesRegex(doc.DocumentError, "Cannot verify"):
                    doc._macos_preserve_metadata(self.path, target.fileno(), self.path.stat(), original)
        self.assertEqual(self.path.read_bytes(), b"original")

    @unittest.skipUnless(sys.platform == "darwin", "macOS save failure preservation")
    def test_macos_copy_or_verification_failure_preserves_original(self):
        initial = doc.save(self.path, self.document)
        self.document["design"]["model"]["name"] = "edited"
        preserve = doc._macos_preserve_metadata
        for fail_on in (1, 2):
            calls = 0
            def fail(*args):
                nonlocal calls
                calls += 1
                if calls == fail_on:
                    raise doc.DocumentError("Cannot preserve metadata")
                return preserve(*args)
            with self.subTest(fail_on=fail_on), \
                    mock.patch.object(doc, "_macos_preserve_metadata", side_effect=fail), \
                    mock.patch.object(doc.os, "replace") as replace:
                with self.assertRaisesRegex(doc.DocumentError, "Cannot preserve"):
                    doc.save(self.path, self.document, initial)
                replace.assert_not_called()
            self.assertEqual(doc.read(self.path)[1], initial)
            self.assertEqual(list(self.root.iterdir()), [self.path])

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
