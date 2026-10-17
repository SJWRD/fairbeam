"""Attempt-owned UUID bindings after failed native document creation."""
import copy
from pathlib import Path
import unittest
from unittest import mock

from fairbeam import documents as doc, modelfiles
import test_document_workspace as fixtures


class ProvisionalDocumentBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DocumentWorkspaceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def attempt(self, clone):
        case = fixtures.DocumentWorkspaceTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        if clone:
            case.write_document()
            opened = case.bridge.open(case.path)
            source = case.saved_edit(opened["file"])
            options = {"document_id": opened["document"]["id"], "save_as": True}
            target = case.root / "copy.fb"
        else:
            source = case.workspace_design()
            options = {"save_as": True}
            target = case.path
        return case, source, target, options

    def assert_source_unchanged(self, case, source):
        self.assertEqual(modelfiles.read_design_file(case.models, source["id"]), source)

    def test_failed_first_save_keeps_original_key_unbound_and_retry_reuses_it(self):
        case = self.fixture
        original = case.workspace_design()
        original_bytes = modelfiles.design_path(case.models, original["id"]).read_bytes()
        with mock.patch.object(doc, "_publish_new", side_effect=OSError("publication unavailable")):
            with self.assertRaisesRegex(OSError, "publication unavailable"):
                case.bridge.save(case.path, original["id"], original["hash"], save_as=True)
        self.assertFalse(case.path.exists())
        self.assertIsNone(case.bridge.document_id_for(original["id"]))
        self.assertEqual(list(case.bridge.state_dir.glob("*.json")), [])
        self.assertEqual(modelfiles.design_path(case.models, original["id"]).read_bytes(), original_bytes)
        retried = case.bridge.save(case.path, original["id"], original["hash"], save_as=True)
        self.assertEqual(retried["file"]["id"], original["id"])
        self.assertEqual(case.bridge.document_id_for(original["id"]), retried["document"]["id"])
        self.assertEqual(len(list(case.models.glob("*.design.json"))), 1)
        self.assertEqual(retried["warnings"], [])

    def test_metadata_failure_before_or_after_state_publication_cleans_only_new_binding(self):
        for clone in (False, True):
            for publish_state in (False, True):
                with self.subTest(clone=clone, publish_state=publish_state):
                    case, source, target, options = self.attempt(clone)
                    before = {p.name: p.read_bytes() for p in case.bridge.state_dir.glob("*.json")}
                    write = case.bridge._write_state
                    def fail(state):
                        if publish_state:
                            write(state)
                        raise OSError("metadata write failed")
                    with mock.patch.object(case.bridge, "_write_state", side_effect=fail):
                        with self.assertRaisesRegex(OSError, "metadata write failed"):
                            case.bridge.save(target, source["id"], source["hash"], **options)
                    self.assertFalse(target.exists())
                    self.assertEqual({p.name: p.read_bytes() for p in case.bridge.state_dir.glob("*.json")}, before)
                    self.assertEqual(len(list(case.models.glob("*.design.json"))), 1)
                    self.assert_source_unchanged(case, source)

    def test_failed_clone_publication_retry_has_no_extra_clone_or_binding(self):
        case, source, target, options = self.attempt(True)
        before = {p.name: p.read_bytes() for p in case.bridge.state_dir.glob("*.json")}
        with mock.patch.object(doc, "_publish_new", side_effect=OSError("publication unavailable")):
            with self.assertRaisesRegex(OSError, "publication unavailable"):
                case.bridge.save(target, source["id"], source["hash"], **options)
        self.assertEqual({p.name: p.read_bytes() for p in case.bridge.state_dir.glob("*.json")}, before)
        self.assertEqual(len(list(case.models.glob("*.design.json"))), 1)
        retried = case.bridge.save(target, source["id"], source["hash"], **options)
        self.assertEqual(len(list(case.models.glob("*.design.json"))), 2)
        self.assertEqual(len(list(case.bridge.state_dir.glob("*.json"))), 2)
        self.assertEqual(case.bridge.open(target)["file"]["id"], retried["file"]["id"])
        self.assert_source_unchanged(case, source)

    def test_error_after_portable_publication_preserves_binding_and_reopens_original_key(self):
        for clone in (False, True):
            with self.subTest(clone=clone):
                case, source, target, options = self.attempt(clone)
                save = doc.save
                captured = {}
                def fail(path, document, base_hash):
                    captured.update(case.bridge._state(document["id"]))
                    save(path, document, base_hash)
                    raise OSError("error after publication")
                with mock.patch.object(doc, "save", side_effect=fail):
                    with self.assertRaisesRegex(OSError, "error after publication"):
                        case.bridge.save(target, source["id"], source["hash"], **options)
                self.assertEqual(case.bridge._state(captured["id"]), captured)
                self.assertEqual(case.bridge.open(target)["file"]["id"], captured["model_id"])
                self.assertEqual(len(list(case.models.glob("*.design.json"))), 2 if clone else 1)
                self.assert_source_unchanged(case, source)

    def test_changed_state_is_preserved_before_deleting_a_clone(self):
        for clone in (False, True):
            for equivalent_json in (False, True):
                with self.subTest(clone=clone, equivalent_json=equivalent_json):
                    case, source, target, options = self.attempt(clone)
                    captured = {}
                    def fail(path, document, base_hash):
                        state_path = case.bridge._state_path(document["id"])
                        state = case.bridge._state(document["id"])
                        raw = state_path.read_bytes()
                        if equivalent_json:
                            raw += b"\n"
                        else:
                            state["revision"] = "f" * 64
                            raw = doc._json_bytes(state)
                        state_path.write_bytes(raw)
                        captured.update(path=state_path, raw=raw, key=state["model_id"])
                        raise OSError("publication unavailable")
                    with mock.patch.object(doc, "save", side_effect=fail):
                        with self.assertRaisesRegex(OSError, "publication unavailable"):
                            case.bridge.save(target, source["id"], source["hash"], **options)
                    self.assertEqual(captured["path"].read_bytes(), captured["raw"])
                    self.assertTrue(modelfiles.design_path(case.models, captured["key"]).is_file())
                    self.assert_source_unchanged(case, source)

    def test_concurrent_design_edit_survives_and_only_unchanged_clone_can_be_removed(self):
        for clone in (False, True):
            with self.subTest(clone=clone):
                case, source, target, options = self.attempt(clone)
                captured = {}
                def fail(path, document, base_hash):
                    state = case.bridge._state(document["id"])
                    file = modelfiles.read_design_file(case.models, state["model_id"])
                    captured.update(state=state, file=case.saved_edit(file, "Concurrent saved edit"))
                    raise OSError("publication unavailable")
                with mock.patch.object(doc, "save", side_effect=fail):
                    with self.assertRaisesRegex(OSError, "publication unavailable"):
                        case.bridge.save(target, source["id"], source["hash"], **options)
                self.assertEqual(modelfiles.read_design_file(case.models, captured["file"]["id"]), captured["file"])
                if clone:
                    self.assertEqual(case.bridge._state(captured["state"]["id"]), captured["state"])
                    self.assert_source_unchanged(case, source)
                else:
                    self.assertIsNone(case.bridge.document_id_for(source["id"]))

    def test_existing_destination_entry_retains_recovery_without_reading_it(self):
        for clone in (False, True):
            for entry in ("file", "directory", "broken-link"):
                with self.subTest(clone=clone, entry=entry):
                    case, source, target, options = self.attempt(clone)
                    captured = {}
                    def fail(path, document, base_hash):
                        captured.update(case.bridge._state(document["id"]))
                        if entry == "file":
                            path.write_bytes(b"unrelated external bytes")
                        elif entry == "directory":
                            path.mkdir()
                        else:
                            try:
                                path.symlink_to(path.parent / "missing-target.fb")
                            except OSError as exc:
                                self.skipTest(str(exc))
                        raise OSError("publication unavailable")
                    with mock.patch.object(doc, "save", side_effect=fail):
                        with self.assertRaisesRegex(OSError, "publication unavailable"):
                            case.bridge.save(target, source["id"], source["hash"], **options)
                    self.assertEqual(case.bridge._state(captured["id"]), captured)
                    self.assertTrue(modelfiles.design_path(case.models, captured["model_id"]).is_file())
                    self.assert_source_unchanged(case, source)

    def test_unreadable_destination_probe_preserves_recovery_and_original_error(self):
        for clone in (False, True):
            with self.subTest(clone=clone):
                case, source, target, options = self.attempt(clone)
                target = doc._path(target)
                lstat = Path.lstat
                captured = {}
                def unreadable(path, *args, **kwargs):
                    if captured.get("probe") and path == target:
                        raise PermissionError("uncertain destination")
                    return lstat(path, *args, **kwargs)
                def fail(path, document, base_hash):
                    captured.update(case.bridge._state(document["id"]))
                    captured["probe"] = True
                    raise OSError("publication unavailable")
                with mock.patch.object(doc, "save", side_effect=fail), mock.patch.object(Path, "lstat", unreadable):
                    with self.assertRaisesRegex(OSError, "publication unavailable"):
                        case.bridge.save(target, source["id"], source["hash"], **options)
                captured.pop("probe")
                self.assertEqual(case.bridge._state(captured["id"]), captured)
                self.assertTrue(modelfiles.design_path(case.models, captured["model_id"]).is_file())
                self.assert_source_unchanged(case, source)

    def test_changed_parent_after_publication_preserves_moved_document_recovery(self):
        for clone in (False, True):
            with self.subTest(clone=clone):
                case, source, target, options = self.attempt(clone)
                folder = case.root / "selected-folder"
                folder.mkdir()
                target = folder / target.name
                moved = case.root / "moved-folder"
                save = doc.save
                captured = {}
                def fail(path, document, base_hash):
                    captured.update(case.bridge._state(document["id"]))
                    save(path, document, base_hash)
                    folder.rename(moved)
                    folder.mkdir()
                    raise OSError("error after publication")
                with mock.patch.object(doc, "save", side_effect=fail):
                    with self.assertRaisesRegex(OSError, "error after publication"):
                        case.bridge.save(target, source["id"], source["hash"], **options)
                self.assertFalse(target.exists())
                self.assertEqual(case.bridge._state(captured["id"]), captured)
                self.assertEqual(case.bridge.open(moved / target.name)["file"]["id"], captured["model_id"])
                self.assert_source_unchanged(case, source)

    def test_existing_uuid_metadata_is_not_overwritten_or_deleted(self):
        case = self.fixture
        source = case.workspace_design()
        document = doc.create(source["design"], "existing.results")
        state = {"schema": "fairbeam.document-workspace/1", "id": document["id"],
                 "model_id": "other_workspace", "results": []}
        case.bridge._write_state(state)
        with mock.patch.object(doc, "create", return_value=copy.deepcopy(document)):
            with self.assertRaisesRegex(doc.DocumentConflict, "already has workspace metadata"):
                case.bridge.save(case.path, source["id"], source["hash"])
        self.assertEqual(case.bridge._state(document["id"]), state)
        self.assertEqual(len(list(case.models.glob("*.design.json"))), 1)
        self.assert_source_unchanged(case, source)


if __name__ == "__main__":
    unittest.main()
