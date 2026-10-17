"""One validated result byte snapshot is shared by parsing and portable-file copying."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from fairbeam import documents as doc
from fairbeam.design import blank_design
from fairbeam.document_workspace import _result_bytes


class DocumentResultContentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "snapshot.fb"
        self.document = doc.create(blank_design("source-model", "Source"), "snapshot.results")
        self.folder = self.root / self.document["results_directory"]
        self.folder.mkdir()
        self.result_path = self.folder / "run.json"
        # Keep BOM, whitespace and number spelling: copying must not reserialize the result.
        self.raw = (b'\xef\xbb\xbf{ "schema": "fairbeam.project/1", "model": {"id": "source-model"}, '
                    b'"results": {"value": 32.0, "negative_zero": -0.0} }\r\n')
        self.reference = {"file": self.result_path.name, "sha256": doc.digest(self.raw),
                          "design_sha256": doc.design_digest(self.document["design"])}
        self.document["results"] = [self.reference]
        self.result_path.write_bytes(self.raw)

    def test_workspace_copies_exact_bytes_with_one_read_and_one_checksum(self):
        with mock.patch.object(doc, "_read_bytes", wraps=doc._read_bytes) as read, \
                mock.patch.object(doc, "digest", wraps=doc.digest) as digest:
            raw = _result_bytes(self.path, self.document, self.reference)
        self.assertEqual(raw, self.raw)
        read.assert_called_once_with(self.result_path, doc.MAX_RESULT)
        digest.assert_called_once_with(self.raw)

    def test_public_reader_still_returns_only_the_parsed_result(self):
        expected = json.loads(self.raw.decode("utf-8-sig"))
        self.assertEqual(doc.read_result(self.path, self.document, self.reference), expected)
        raw, parsed = doc._read_result_content(self.path, self.document, self.reference)
        self.assertEqual((raw, parsed), (self.raw, expected))

    def test_changed_bytes_fail_before_json_is_parsed(self):
        self.result_path.write_bytes(b'{"schema":"fairbeam.project/1","changed":true}')
        with mock.patch.object(doc.json, "loads", side_effect=AssertionError("unverified data parsed")):
            with self.assertRaisesRegex(doc.DocumentError, "checksum"):
                _result_bytes(self.path, self.document, self.reference)

    def test_matching_checksum_does_not_admit_invalid_json_or_schema(self):
        samples = (
            b"\xff", b"{", b"[]", b"null", b'{"schema":"other"}',
            b'{"schema":"fairbeam.project/1","schema":"fairbeam.project/1"}',
            b'{"schema":"fairbeam.project/1","value":NaN}',
            b'{"schema":"fairbeam.project/1","value":Infinity}',
            b'{"schema":"fairbeam.project/1","value":1e999}',
        )
        for raw in samples:
            with self.subTest(raw=raw):
                self.result_path.write_bytes(raw)
                self.reference["sha256"] = doc.digest(raw)
                with self.assertRaises(doc.DocumentError):
                    _result_bytes(self.path, self.document, self.reference)

    def test_workspace_model_identity_check_is_retained(self):
        raw = self.raw.replace(b"source-model", b"other-model")
        self.result_path.write_bytes(raw)
        self.reference["sha256"] = doc.digest(raw)
        # The public storage reader keeps its schema-only model contract.
        self.assertEqual(doc.read_result(self.path, self.document, self.reference)["model"]["id"],
                         "other-model")
        with self.assertRaisesRegex(doc.DocumentError, "model identity"):
            _result_bytes(self.path, self.document, self.reference)

    def test_membership_and_portable_path_are_checked_before_reading(self):
        foreign = {**self.reference, "file": "another.json"}
        invalid_document = copy.deepcopy(self.document)
        invalid_document["results_directory"] = "../elsewhere"
        for document, reference in ((self.document, foreign), (invalid_document, self.reference)):
            with self.subTest(document=document), \
                    mock.patch.object(doc, "_read_bytes", side_effect=AssertionError("path read")):
                with self.assertRaises(doc.DocumentError):
                    _result_bytes(self.path, document, reference)

    def test_size_and_regular_file_guards_are_retained(self):
        with mock.patch.object(doc, "MAX_RESULT", len(self.raw) - 1):
            with self.assertRaisesRegex(doc.DocumentError, "size limit"):
                _result_bytes(self.path, self.document, self.reference)
        self.result_path.unlink()
        self.result_path.mkdir()
        with self.assertRaisesRegex(doc.DocumentError, "regular files"):
            _result_bytes(self.path, self.document, self.reference)

    def test_linked_result_is_still_refused(self):
        outside = self.root / "outside.json"
        outside.write_bytes(self.raw)
        self.result_path.unlink()
        try:
            os.symlink(outside, self.result_path)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symbolic links unavailable: {exc}")
        with self.assertRaisesRegex(doc.DocumentError, "not links"):
            _result_bytes(self.path, self.document, self.reference)

    def test_concurrent_replacement_cannot_change_validated_copy_bytes(self):
        read_complete, replaced = threading.Event(), threading.Event()
        writer_errors = []
        changed = b"a concurrent writer replaced the selected result"

        def replace_result():
            try:
                if not read_complete.wait(timeout=5):
                    raise TimeoutError("result was not read")
                replacement = self.folder / "replacement.tmp"
                replacement.write_bytes(changed)
                os.replace(replacement, self.result_path)
            except BaseException as exc:
                writer_errors.append(exc)
            finally:
                replaced.set()

        actual_read = doc._read_bytes

        def read_then_replace(path, limit):
            raw = actual_read(path, limit)
            read_complete.set()
            if not replaced.wait(timeout=5):
                raise TimeoutError("replacement did not finish")
            return raw

        writer = threading.Thread(target=replace_result, daemon=True)
        writer.start()
        try:
            with mock.patch.object(doc, "_read_bytes", side_effect=read_then_replace) as read:
                copied = _result_bytes(self.path, self.document, self.reference)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(copied, self.raw)
        finally:
            read_complete.set()
            writer.join(timeout=5)
        self.assertFalse(writer.is_alive())
        self.assertEqual(writer_errors, [])
        self.assertEqual(self.result_path.read_bytes(), changed)
        # There is no cached authorization: a subsequent read validates the new bytes again.
        with self.assertRaisesRegex(doc.DocumentError, "checksum"):
            _result_bytes(self.path, self.document, self.reference)


if __name__ == "__main__":
    unittest.main()
