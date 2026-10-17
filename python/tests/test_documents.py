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
        self.root = Path(self.tmp.name)
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
        def lstat(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if path == folder:
                return mock.Mock(st_mode=info.st_mode, st_file_attributes=0x400)
            return info
        with mock.patch.object(Path, "lstat", lstat), self.assertRaisesRegex(doc.DocumentError, "link"):
            doc.read_result(self.path, self.document, reference)


if __name__ == "__main__":
    unittest.main()
