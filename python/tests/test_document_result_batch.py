"""A result import owns its manifest, without caching sidecar authorization or bytes."""
from collections import UserDict
import copy
import json
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
import unittest
from unittest import mock

from fairbeam import documents as doc
from fairbeam.design import blank_design
from fairbeam.document_workspace import DocumentWorkspace


class DocumentResultBatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "batch.fb"
        self.document = doc.create(blank_design("source-model", "Source"), "batch.results")
        self.folder = self.root / self.document["results_directory"]
        self.folder.mkdir()
        self.current = doc.design_digest(self.document["design"])
        self.projects = self.root / "projects"
        self.projects.mkdir()
        self.bridge = DocumentWorkspace(self.root / "models", self.projects, self.root / "history")
        self.reference, self.raw = self.result("first.json")

    def result(self, name, *, source=None, model="source-model", raw=None):
        if raw is None:
            raw = (b'\xef\xbb\xbf{ "schema": "fairbeam.project/1", "model": {"id": "'
                   + model.encode() + b'"}, "results": {"value": 32.0}, "run": '
                   + json.dumps(name).encode() + b' }\r\n')
        reference = {"file": name, "sha256": doc.digest(raw),
                     "design_sha256": self.current if source is None else source}
        self.document["results"].append(reference)
        (self.folder / name).write_bytes(raw)
        return reference, raw

    def test_reader_owns_validated_manifest_and_yields_independent_references(self):
        original = copy.deepcopy(self.document)
        reader = doc._ResultReader(self.path, self.document)
        self.document["id"] = "00000000-0000-0000-0000-000000000000"
        self.document["design"]["model"]["id"] = "changed-model"
        self.document["results_directory"] = "changed.results"
        self.reference["sha256"] = "0" * 64
        self.document["results"].clear()
        exposed = list(reader.references())
        exposed[0]["file"] = "changed.json"
        self.assertEqual(list(reader.references()), original["results"])
        self.assertEqual(reader.document_id, original["id"])
        self.assertEqual(reader.model_id, "source-model")
        self.assertEqual(reader.design_sha256, self.current)
        self.assertEqual(reader.read(original["results"][0])[0], self.raw)
        with self.assertRaisesRegex(doc.DocumentError, "does not belong"):
            reader.read(exposed[0])

    def test_incoming_reference_is_snapshotted_before_filesystem_read(self):
        reader = doc._ResultReader(self.path, self.document)
        reference = dict(self.reference)
        actual_read = doc._read_bytes

        def mutate_after_selection(path, limit):
            reference.update(file="other.json", sha256="0" * 64, design_sha256="0" * 64)
            return actual_read(path, limit)

        with mock.patch.object(doc, "_read_bytes", side_effect=mutate_after_selection):
            self.assertEqual(reader.read(reference)[0], self.raw)
        with self.assertRaisesRegex(doc.DocumentError, "does not belong"):
            reader.read(reference)

    def test_exact_reference_membership_includes_equal_mapping_copies(self):
        reader = doc._ResultReader(self.path, self.document)
        for reference in (dict(self.reference), UserDict(self.reference), MappingProxyType(self.reference)):
            with self.subTest(type=type(reference).__name__):
                self.assertIn(reference, self.document["results"])
                self.assertEqual(reader.read(reference)[0], self.raw)
        foreign = [None, [], {**self.reference, "extra": True},
                   {key: value for key, value in self.reference.items() if key != "file"}]
        for field in ("file", "sha256", "design_sha256"):
            foreign.extend(({**self.reference, field: "foreign"}, {**self.reference, field: []}))
        with mock.patch.object(doc, "_read_bytes", side_effect=AssertionError("foreign reference read")):
            for reference in foreign:
                with self.subTest(reference=reference):
                    with self.assertRaisesRegex(doc.DocumentError, "does not belong"):
                        reader.read(reference)

    def test_invalid_manifest_is_rejected_before_any_result_read(self):
        invalid = []
        for field, value in (("id", "invalid"), ("results_directory", "../outside"),
                             ("schema", "other"), ("results", [self.reference] * 2)):
            document = copy.deepcopy(self.document)
            document[field] = value
            invalid.append(document)
        document = copy.deepcopy(self.document)
        document["results"].append({**self.reference, "file": "bad.json", "design_sha256": "invalid"})
        invalid.append(document)
        with mock.patch.object(doc, "_read_bytes", side_effect=AssertionError("invalid manifest read")):
            for document in invalid:
                with self.subTest(document=document):
                    with self.assertRaises(doc.DocumentError) as expected:
                        doc.validate(document)
                    with self.assertRaises(doc.DocumentError) as actual:
                        doc._ResultReader(self.path, document)
                    self.assertEqual(str(actual.exception), str(expected.exception))

    def test_constructor_does_not_require_result_folder_and_reads_recheck_it(self):
        self.folder.rename(self.root / "parked.results")
        reader = doc._ResultReader(self.path, self.document)
        with self.assertRaises(FileNotFoundError):
            reader.read(self.reference)
        (self.root / "parked.results").rename(self.folder)
        self.assertEqual(reader.read(self.reference)[0], self.raw)
        self.folder.rename(self.root / "parked.results")
        self.folder.write_bytes(b"not a directory")
        with self.assertRaisesRegex(doc.DocumentError, "real directory"):
            reader.read(self.reference)

    def test_constructor_does_not_authorize_later_linked_result_folder(self):
        reader = doc._ResultReader(self.path, self.document)
        outside = self.root / "outside.results"
        self.folder.rename(outside)
        try:
            os.symlink(outside, self.folder, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symbolic links unavailable: {exc}")
        with self.assertRaisesRegex(doc.DocumentError, "real directory"):
            reader.read(self.reference)

    def test_constructor_does_not_authorize_later_linked_result_file(self):
        reader = doc._ResultReader(self.path, self.document)
        path = self.folder / self.reference["file"]
        outside = self.root / "outside.json"
        path.rename(outside)
        try:
            os.symlink(outside, path)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symbolic links unavailable: {exc}")
        with self.assertRaisesRegex(doc.DocumentError, "not links"):
            reader.read(self.reference)

    def test_each_read_checks_current_bytes_size_and_regular_file(self):
        reader = doc._ResultReader(self.path, self.document)
        self.assertEqual(reader.read(self.reference)[0], self.raw)
        path = self.folder / self.reference["file"]
        path.write_bytes(self.raw.replace(b"32.0", b"33.0"))
        with self.assertRaisesRegex(doc.DocumentError, "checksum"):
            reader.read(self.reference)
        path.write_bytes(self.raw)
        with mock.patch.object(doc, "MAX_RESULT", len(self.raw) - 1):
            with self.assertRaisesRegex(doc.DocumentError, "size limit"):
                reader.read(self.reference)
        path.unlink()
        path.mkdir()
        with self.assertRaisesRegex(doc.DocumentError, "regular files"):
            reader.read(self.reference)

    def test_standalone_public_reader_revalidates_each_call(self):
        doc.read_result(self.path, self.document, self.reference)
        self.document["results"].append({**self.reference, "file": "second.json", "sha256": "invalid"})
        with mock.patch.object(doc, "_read_bytes", side_effect=AssertionError("invalid manifest read")):
            with self.assertRaisesRegex(doc.DocumentError, "SHA-256"):
                doc.read_result(self.path, self.document, self.reference)

    def test_batch_keeps_current_stale_missing_corrupt_and_model_errors_independent(self):
        stale, stale_raw = self.result("stale.json", source="0" * 64)
        missing, _ = self.result("missing.json")
        (self.folder / missing["file"]).unlink()
        corrupt, _ = self.result("corrupt.json")
        (self.folder / corrupt["file"]).write_bytes(b"changed")
        self.result("other-model.json", model="other-model")
        self.result("duplicate.json", raw=b'{"schema":"fairbeam.project/1","schema":"fairbeam.project/1"}')
        original = copy.deepcopy(self.document)
        with mock.patch.object(doc, "validate", wraps=doc.validate) as validate:
            records, statuses, warnings = self.bridge._import_results(self.path, self.document)
        validate.assert_called_once()
        self.assertIsNot(validate.call_args.args[0], self.document)
        self.assertEqual(self.document, original)
        self.assertEqual([value["status"] for value in statuses],
                         ["current", "stale", "missing", "invalid", "invalid", "invalid"])
        self.assertEqual([value["file"] for value in records], [value["file"] for value in original["results"]])
        self.assertEqual(len(warnings), 4)
        self.assertIn("checksum", warnings[1])
        self.assertIn("model identity", warnings[2])
        self.assertIn("not valid JSON", warnings[3])
        self.assertEqual((self.projects / records[0]["project_file"]).read_bytes(), self.raw)
        self.assertEqual((self.projects / records[1]["project_file"]).read_bytes(), stale_raw)
        self.assertEqual(len(list(self.projects.iterdir())), 2)
        self.assertEqual(stale["design_sha256"], "0" * 64)

    def test_batch_uses_owned_identity_and_reference_order_after_caller_mutation(self):
        second, second_raw = self.result("second.json", source="0" * 64)
        original = copy.deepcopy(self.document)
        actual_reader = doc._ResultReader

        def construct_then_mutate(path, document):
            reader = actual_reader(path, document)
            document["id"] = "00000000-0000-0000-0000-000000000000"
            document["design"]["model"]["id"] = "changed-model"
            document["results_directory"] = "changed.results"
            document["results"].reverse()
            document["results"][0]["sha256"] = "0" * 64
            return reader

        with mock.patch.object(doc, "_ResultReader", side_effect=construct_then_mutate):
            records, statuses, warnings = self.bridge._import_results(self.path, self.document)
        self.assertEqual(warnings, [])
        self.assertEqual([value["status"] for value in statuses], ["current", "stale"])
        self.assertEqual([value["file"] for value in records], [value["file"] for value in original["results"]])
        for record, raw in zip(records, (self.raw, second_raw)):
            self.assertEqual(record["project_file"], self.bridge._project_name(original["id"], record["sha256"]))
            self.assertEqual((self.projects / record["project_file"]).read_bytes(), raw)

    def test_batch_source_digest_comes_from_the_same_validated_snapshot(self):
        actual_reader = doc._ResultReader

        def mutate_then_construct(path, document):
            document["design"]["model"]["name"] = "A changed design before the snapshot"
            return actual_reader(path, document)

        with mock.patch.object(doc, "_ResultReader", side_effect=mutate_then_construct):
            records, statuses, warnings = self.bridge._import_results(self.path, self.document)
        self.assertEqual(warnings, [])
        self.assertEqual(statuses[0]["status"], "stale")
        self.assertEqual(records[0]["design_sha256"], self.current)
        self.assertNotEqual(doc.design_digest(self.document["design"]), self.current)


if __name__ == "__main__":
    unittest.main()
