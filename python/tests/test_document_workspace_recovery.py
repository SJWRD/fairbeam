"""Commit/recovery boundaries for native document saves; no solver or native UI."""
import json
from unittest import mock
import unittest

from fairbeam import documents as doc, modelfiles
import test_document_workspace as fixtures


class DocumentWorkspaceRecovery(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DocumentWorkspaceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def opened_edit(self):
        case = self.fixture
        case.write_document()
        opened = case.bridge.open(case.path)
        return opened, case.saved_edit(opened["file"])

    def save_as(self, opened, edited):
        case = self.fixture
        return case.bridge.save(case.root / "copy.fb", edited["id"], edited["hash"],
                                document_id=opened["document"]["id"], save_as=True)

    def assert_committed_reply(self, reply, path, design):
        document, revision = doc.read(path)
        self.assertEqual(document["design"], design)
        self.assertEqual(reply["document"]["revision"], revision)
        self.assertEqual(reply["document"]["id"], document["id"])
        self.assertTrue(reply["warnings"])
        self.assertEqual(reply["file"]["design"], design)

    def test_clone_failure_happens_before_portable_publication(self):
        case = self.fixture
        opened, edited = self.opened_edit()
        original = case.path.read_bytes()
        with mock.patch.object(case.bridge, "_create_design", side_effect=OSError("clone disk failure")):
            with self.assertRaisesRegex(OSError, "clone disk failure"):
                self.save_as(opened, edited)
        self.assertFalse((case.root / "copy.fb").exists())
        self.assertEqual(case.path.read_bytes(), original)
        self.assertEqual(modelfiles.read_design_file(case.models, edited["id"])["design"], edited["design"])

    def test_existing_save_metadata_failure_returns_committed_revision_and_retry_works(self):
        case = self.fixture
        opened, edited = self.opened_edit()
        case.bridge.record_completed(case.completed(edited))
        with mock.patch.object(case.bridge, "_write_state", side_effect=OSError("metadata disk failure")):
            reply = case.save_opened(opened, edited)
        self.assert_committed_reply(reply, case.path, edited["design"])
        self.assertNotEqual(reply["document"]["revision"], opened["document"]["revision"])
        references = doc.read(case.path)[0]["results"]
        self.assertEqual(len(references), 1)
        self.assertEqual(case.bridge._state(opened["document"]["id"])["results"], [])
        # The already committed envelope must retain its references even if completion
        # metadata is unavailable; the stale workspace state predates this result.
        with mock.patch.object(case.bridge, "_completed", return_value=([], [])):
            again = case.save_opened(reply, edited)
        self.assertEqual(again["document"]["revision"], reply["document"]["revision"])
        self.assertEqual(doc.read(case.path)[0]["results"], references)
        self.assertEqual(again["results"][0]["status"], "current")

    def test_failed_publication_cleans_only_the_unchanged_private_clone(self):
        case = self.fixture
        opened, edited = self.opened_edit()
        original = case.path.read_bytes()
        for change_clone in (False, True):
            created = {}
            def fail_publish(path, document, base_hash):
                created["identity"] = document["id"]
                key = "fb_" + document["id"].replace("-", "")
                created["key"] = key
                clone = modelfiles.read_design_file(case.models, key)
                if change_clone:
                    created["file"] = case.saved_edit(clone, "Concurrent clone edit")
                raise OSError("portable disk failure")
            with self.subTest(concurrent_clone_edit=change_clone):
                with mock.patch.object(doc, "save", side_effect=fail_publish):
                    with self.assertRaisesRegex(OSError, "portable disk failure"):
                        self.save_as(opened, edited)
                self.assertFalse((case.root / "copy.fb").exists())
                self.assertEqual(case.path.read_bytes(), original)
                if change_clone:
                    self.assertEqual(modelfiles.read_design_file(case.models, created["key"]), created["file"])
                else:
                    self.assertFalse(modelfiles.design_path(case.models, created["key"]).exists())
                    self.assertIsNone(case.bridge._state(created["identity"]))

    def test_save_as_target_metadata_failure_returns_committed_copy_and_recovers(self):
        case = self.fixture
        opened, edited = self.opened_edit()
        write = case.bridge._write_state
        calls = []
        def fail_final_target(state):
            calls.append(state["id"])
            if len(calls) == 2:
                raise OSError("target metadata disk failure")
            return write(state)
        with mock.patch.object(case.bridge, "_write_state", side_effect=fail_final_target):
            reply = self.save_as(opened, edited)
        target = case.root / "copy.fb"
        self.assert_committed_reply(reply, target, edited["design"])
        self.assertNotEqual(reply["file"]["id"], edited["id"])
        self.assertEqual(case.bridge.open(target)["document"]["revision"], reply["document"]["revision"])
        self.assertEqual(case.bridge.open(case.path)["file"]["design"], case.design)
        self.assertEqual(modelfiles.read_design_file(case.models, reply["file"]["id"])["design"], edited["design"])

    def test_save_as_source_metadata_failure_returns_committed_copy_and_recovers(self):
        case = self.fixture
        opened, edited = self.opened_edit()
        write = case.bridge._write_state
        def fail_source(state):
            if state["id"] == opened["document"]["id"]:
                raise OSError("source metadata disk failure")
            return write(state)
        with mock.patch.object(case.bridge, "_write_state", side_effect=fail_source):
            reply = self.save_as(opened, edited)
        self.assert_committed_reply(reply, case.root / "copy.fb", edited["design"])
        self.assertEqual(case.bridge.open(case.path)["file"]["design"], case.design)
        self.assertEqual(modelfiles.read_design_file(case.models, reply["file"]["id"])["design"], edited["design"])

    def test_linked_documents_ancestor_is_rejected_before_nested_metadata_access(self):
        case = self.fixture
        file = case.workspace_design()
        job = case.completed(file)
        case.history.mkdir()
        outside = case.root / "outside-state"
        outside.mkdir()
        try:
            (case.history / "documents").symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(str(exc))
        # A regular file beneath the linked ancestor must not make that ancestor trusted.
        state = {"schema": "fairbeam.document-workspace/1", "id": case.document["id"],
                 "model_id": file["id"], "results": []}
        manifest = outside / (case.document["id"] + ".json")
        manifest.write_text(json.dumps(state), encoding="utf-8")
        case.write_document()
        for name, action in (("record", lambda: case.bridge.record_completed(job)),
                             ("save", lambda: case.bridge.save(case.path, file["id"], file["hash"])),
                             ("open", lambda: case.bridge.open(case.path))):
            with self.subTest(operation=name):
                with self.assertRaisesRegex(doc.DocumentError, "link"):
                    action()
        self.assertEqual(list(outside.iterdir()), [manifest])


if __name__ == "__main__":
    unittest.main()
