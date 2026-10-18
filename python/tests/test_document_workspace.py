"""Native document/workspace integration without a solver, server, or native dialogs."""
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from fairbeam import documents as doc, modelfiles
from fairbeam.design import blank_design
from fairbeam.document_workspace import DocumentWorkspace


class DocumentWorkspaceTests(unittest.TestCase):
    def test_optional_section_export_failure_keeps_document_saved(self):
        self.sidecar()
        self.write_document()
        opened = self.bridge.open(self.path)
        with mock.patch('fairbeam.document_exports.export_sections', side_effect=OSError('disk full')):
            result = self.bridge.save(self.path, opened['file']['id'], opened['file']['hash'],
                                      opened['document']['revision'], self.document['id'])
        self.assertTrue(any('Organized result export' in w for w in result['warnings']))
        self.assertEqual(len(doc.read(self.path)[0]['results']), 1)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.models = self.root / "models"
        self.projects = self.root / "projects"
        self.history = self.root / "history"
        self.models.mkdir()
        self.projects.mkdir()
        self.path = self.root / "İzmir patch.fb"
        self.design = blank_design("original-model", "İzmir patch")
        self.document = doc.create(self.design, "İzmir patch.results")
        self.manager = SimpleNamespace(input_path=lambda job: job.snapshot_path)
        self.bridge = DocumentWorkspace(self.models, self.projects, self.history, self.manager)

    def write_document(self):
        return doc.save(self.path, self.document)

    def sidecar(self, design=None, file="run.json", raw=None):
        design = self.design if design is None else design
        raw = raw or json.dumps({"schema": "fairbeam.project/1", "model": design["model"],
                                 "results": {"bands": []}}, ensure_ascii=False).encode("utf-8")
        folder = self.root / self.document["results_directory"]
        folder.mkdir(exist_ok=True)
        (folder / file).write_bytes(raw)
        reference = {"file": file, "sha256": doc.digest(raw), "design_sha256": doc.design_digest(design)}
        self.document["results"].append(reference)
        return reference, raw

    def workspace_design(self):
        modelfiles.create_design(self.models, "my_patch", self.design)
        return modelfiles.read_design_file(self.models, "my_patch")

    def saved_edit(self, file, name="Edited patch"):
        design = copy.deepcopy(file["design"])
        design["model"]["name"] = name
        modelfiles.save_design(self.models, self.history, file["id"], design, file["hash"])
        return modelfiles.read_design_file(self.models, file["id"])

    def completed(self, file, name="result.json", job_id="job-one"):
        source = self.root / (job_id + ".design.json")
        raw = json.dumps(file["design"], ensure_ascii=False).encode("utf-8")
        source.write_bytes(raw)
        result = {"schema": "fairbeam.project/1", "model": file["design"]["model"], "results": {"bands": []}}
        (self.projects / name).write_text(json.dumps(result), encoding="utf-8")
        return SimpleNamespace(status="done", bundle=name, input_snapshot={"sha256": doc.digest(raw)},
                               kind="run", model=file["id"], id=job_id, snapshot_path=str(source))

    def save_opened(self, opened, file=None):
        file = file or opened["file"]
        return self.bridge.save(self.path, file["id"], file["hash"],
                                base_hash=opened["document"]["revision"], document_id=opened["document"]["id"])

    def test_identity_lookup_does_not_create_metadata_for_legacy_design(self):
        file = self.workspace_design()
        with mock.patch.object(Path, "mkdir", side_effect=AssertionError("read must not create directories")):
            self.assertIsNone(self.bridge.document_id_for(file["id"]))
        self.assertFalse(self.bridge.state_dir.exists())

    def test_identity_lookup_rejects_ambiguous_workspace_binding(self):
        file = self.workspace_design()
        saved = self.bridge.save(self.path, file["id"], file["hash"])
        duplicate = self.bridge._state(saved["document"]["id"])
        duplicate["id"] = doc.create(file["design"], "another.results")["id"]
        self.bridge._write_state(duplicate)
        with self.assertRaisesRegex(doc.DocumentConflict, "Multiple documents"):
            self.bridge.document_id_for(file["id"])

    def test_unbound_save_after_restart_copies_key_without_reading_original_document(self):
        file = self.workspace_design()
        saved = self.bridge.save(self.path, file["id"], file["hash"])
        self.assertEqual(saved["file"]["id"], file["id"])
        original = self.path.read_bytes()
        edited = self.saved_edit(file)
        self.bridge.record_completed(self.completed(edited))
        restarted = DocumentWorkspace(self.models, self.projects, self.history, self.manager)
        with mock.patch.object(doc, "read", side_effect=AssertionError("unbound save must not read the original")):
            copied = restarted.save(self.root / "unbound-copy.fb", edited["id"], edited["hash"], save_as=True)
        self.assertNotEqual(copied["file"]["id"], file["id"])
        self.assertTrue(copied["file"]["id"].startswith("fb_"))
        self.assertNotEqual(copied["file"]["document_id"], saved["file"]["document_id"])
        self.assertEqual(restarted.document_id_for(file["id"]), saved["document"]["id"])
        self.assertEqual(restarted.document_id_for(copied["file"]["id"]), copied["document"]["id"])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(copied["file"]["design"], edited["design"])
        self.assertEqual([result["status"] for result in copied["results"]], ["current"])
        self.assertTrue(any("explicitly opening the original" in warning for warning in copied["warnings"]))

    def test_identity_lookup_rejects_linked_state_directory_and_file(self):
        file = self.workspace_design()
        saved = self.bridge.save(self.path, file["id"], file["hash"])
        state = self.bridge._state_path(saved["document"]["id"])
        outside = self.root / "outside-state.json"
        state.rename(outside)
        try:
            state.symlink_to(outside)
        except OSError as exc:
            self.skipTest(str(exc))
        with self.assertRaises(doc.DocumentError):
            self.bridge.document_id_for(file["id"])
        state.unlink()
        outside.rename(state)
        folder = self.root / "outside-state"
        self.bridge.state_dir.rename(folder)
        self.bridge.state_dir.symlink_to(folder, target_is_directory=True)
        with self.assertRaisesRegex(doc.DocumentError, "link"):
            self.bridge.document_id_for(file["id"])
        self.assertEqual(list(folder.iterdir()), [folder / state.name])

    def test_open_reopen_restart_preserves_key_embedded_identity_and_bytes(self):
        reference, raw = self.sidecar()
        self.write_document()
        first = self.bridge.open(self.path)
        restarted = DocumentWorkspace(self.models, self.projects, self.history, self.manager)
        again = restarted.open(self.path)
        self.assertEqual(first["file"]["id"], again["file"]["id"])
        self.assertEqual(first["file"]["design"], self.design)
        self.assertEqual(len(list(self.models.glob("*.design.json"))), 1)
        self.assertEqual(len(list(self.projects.glob("*.json"))), 1)
        self.assertEqual((self.projects / first["results"][0]["project_file"]).read_bytes(), raw)
        self.assertEqual(first["results"][0]["status"], "current")
        self.assertEqual(first["results"][0]["design_sha256"], reference["design_sha256"])

    def test_move_and_copy_same_document_uuid_reuse_workspace_key(self):
        self.write_document()
        first = self.bridge.open(self.path)
        moved = self.root / "moved.fb"
        self.path.rename(moved)
        self.assertEqual(self.bridge.open(moved)["file"]["id"], first["file"]["id"])
        self.assertEqual(len(list(self.models.glob("*.design.json"))), 1)

    def test_missing_and_corrupt_sidecars_recover_editable_design(self):
        self.sidecar(file="missing.json")
        self.sidecar(file="corrupt.json")
        folder = self.root / self.document["results_directory"]
        (folder / "missing.json").unlink()
        (folder / "corrupt.json").write_text("broken", encoding="utf-8")
        self.write_document()
        opened = self.bridge.open(self.path)
        self.assertEqual(opened["file"]["design"], self.design)
        self.assertEqual([r["status"] for r in opened["results"]], ["missing", "invalid"])
        edited = self.saved_edit(opened["file"])
        saved = self.save_opened(opened, edited)
        self.assertEqual(len(saved["results"]), 2)
        self.assertEqual(doc.read(self.path)[0]["results"], self.document["results"])

    def test_wrong_model_sidecar_is_not_imported(self):
        other = blank_design("someone-else", "Other")
        self.sidecar(design=other)
        self.write_document()
        opened = self.bridge.open(self.path)
        self.assertEqual(opened["results"][0]["status"], "invalid")
        self.assertEqual(list(self.projects.glob("*.json")), [])

    def test_old_results_stay_stale_after_edit_save_and_reopen(self):
        reference, _ = self.sidecar()
        self.write_document()
        opened = self.bridge.open(self.path)
        edited = self.saved_edit(opened["file"])
        saved = self.save_opened(opened, edited)
        self.assertEqual(saved["results"][0]["status"], "stale")
        self.assertEqual(saved["results"][0]["design_sha256"], reference["design_sha256"])
        reopened = self.bridge.open(self.path)
        self.assertEqual(reopened["results"][0]["status"], "stale")
        self.assertEqual(reopened["file"]["design"], edited["design"])

    def test_first_native_save_binds_existing_workspace_key(self):
        file = self.workspace_design()
        saved = self.bridge.save(self.path, file["id"], file["hash"])
        reopened = self.bridge.open(self.path)
        self.assertEqual(saved["file"]["id"], file["id"])
        self.assertEqual(reopened["file"]["id"], file["id"])
        self.assertEqual(len(list(self.models.glob("*.design.json"))), 1)

    def test_long_unicode_filename_gets_a_portable_adjacent_results_name(self):
        file = self.workspace_design()
        path = self.root / (("ü" * 88) + ".fb")
        saved = self.bridge.save(path, file["id"], file["hash"])
        self.assertLessEqual(len(saved["document"]["results_directory"].encode("utf-8")), 180)
        self.assertEqual(doc.read(path)[0]["design"], file["design"])

    def test_first_native_save_of_readonly_example_creates_editable_workspace_copy(self):
        design = blank_design("yagi-867", "Bundled example")
        source = self.models / "yagi_867.design.json"
        source.write_text(modelfiles._design_text(design), encoding="utf-8")
        file = modelfiles.read_design_file(self.models, "yagi_867")
        self.assertTrue(file["readonly"])
        saved = self.bridge.save(self.path, file["id"], file["hash"], save_as=True)
        self.assertNotEqual(saved["file"]["id"], file["id"])
        self.assertFalse(saved["file"]["readonly"])
        self.assertEqual(saved["file"]["design"], design)
        self.assertEqual(modelfiles.read_design_file(self.models, "yagi_867")["hash"], file["hash"])
        self.assertFalse(self.bridge.open(self.path)["file"]["readonly"])

    def test_save_as_makes_independent_uuid_workspace_and_copies_original_result(self):
        reference, raw = self.sidecar()
        self.write_document()
        opened = self.bridge.open(self.path)
        edited = self.saved_edit(opened["file"])
        other = self.root / "Separate copy.fb"
        copied = self.bridge.save(other, edited["id"], edited["hash"],
                                  document_id=opened["document"]["id"], save_as=True)
        self.assertNotEqual(copied["document"]["id"], opened["document"]["id"])
        self.assertNotEqual(copied["file"]["id"], opened["file"]["id"])
        self.assertEqual(copied["file"]["design"], edited["design"])
        self.assertEqual(copied["results"][0]["status"], "stale")
        self.assertEqual(copied["results"][0]["design_sha256"], reference["design_sha256"])
        self.assertEqual((other.parent / copied["document"]["results_directory"] / reference["file"]).read_bytes(), raw)
        self.assertEqual(doc.read(self.path)[0]["design"], self.design)
        self.assertEqual(self.bridge.open(other)["file"]["id"], copied["file"]["id"])
        self.assertEqual(self.bridge.open(self.path)["file"]["design"], self.design)
        self.assertEqual(modelfiles.read_design_file(self.models, copied["file"]["id"])["design"], edited["design"])

    def test_interrupted_open_reuses_reserved_binding(self):
        self.write_document()
        create = self.bridge._create_design

        def interrupt_after_create(key, design):
            create(key, design)
            raise OSError("interrupted after workspace creation")

        with mock.patch.object(self.bridge, "_create_design", side_effect=interrupt_after_create):
            with self.assertRaises(OSError):
                self.bridge.open(self.path)
        reopened = self.bridge.open(self.path)
        self.assertEqual(reopened["file"]["design"], self.design)
        self.assertEqual(len(list(self.models.glob("*.design.json"))), 1)

    def test_saves_refuse_old_workspace_hash_and_destination_overwrite(self):
        file = self.workspace_design()
        edited = self.saved_edit(file)
        with self.assertRaises(doc.DocumentConflict):
            self.bridge.save(self.path, file["id"], file["hash"])
        self.assertFalse(self.path.exists())
        self.write_document()
        original = self.path.read_bytes()
        with self.assertRaises(doc.DocumentConflict):
            self.bridge.save(self.path, edited["id"], edited["hash"])
        self.assertEqual(self.path.read_bytes(), original)

    def test_external_document_change_is_not_overwritten(self):
        revision = self.write_document()
        opened = self.bridge.open(self.path)
        changed = copy.deepcopy(self.document)
        changed["design"]["model"]["name"] = "External change"
        doc.save(self.path, changed, revision)
        edited = self.saved_edit(opened["file"])
        with self.assertRaises(doc.DocumentConflict):
            self.save_opened(opened, edited)
        self.assertEqual(doc.read(self.path)[0], changed)
        self.assertEqual(modelfiles.read_design_file(self.models, edited["id"])["design"], edited["design"])

    def test_failed_document_publication_does_not_erase_newer_workspace_edits_on_reopen(self):
        self.write_document()
        opened = self.bridge.open(self.path)
        edited = self.saved_edit(opened["file"])
        with mock.patch.object(doc, "save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.save_opened(opened, edited)
        with self.assertRaises(doc.DocumentConflict):
            self.bridge.open(self.path)
        self.assertEqual(modelfiles.read_design_file(self.models, edited["id"])["design"], edited["design"])

    def test_open_refuses_collision_with_an_unrelated_workspace_design(self):
        self.write_document()
        key = "fb_" + self.document["id"].replace("-", "")
        modelfiles.create_design(self.models, key, blank_design("other", "Other"))
        with self.assertRaises(doc.DocumentConflict):
            self.bridge.open(self.path)
        self.assertEqual(modelfiles.read_design_file(self.models, key)["design"]["model"]["name"], "Other")

    def test_completed_run_uses_its_admitted_design_not_current_workspace_design(self):
        file = self.workspace_design()
        job = self.completed(file)
        self.assertTrue(self.bridge.record_completed(job))
        edited = self.saved_edit(file)
        saved = self.bridge.save(self.path, edited["id"], edited["hash"])
        self.assertEqual(len(saved["results"]), 1)
        self.assertEqual(saved["results"][0]["status"], "stale")
        self.assertEqual(saved["results"][0]["design_sha256"], doc.design_digest(file["design"]))
        self.assertEqual(saved["results"][0]["provenance"], "source-design")

    def test_named_result_replacement_cannot_reuse_old_provenance(self):
        file = self.workspace_design()
        first_job = self.completed(file)
        self.bridge.record_completed(first_job)
        edited = self.saved_edit(file)
        second_job = self.completed(edited, job_id="job-two")
        self.bridge.record_completed(second_job)
        saved = self.bridge.save(self.path, edited["id"], edited["hash"])
        self.assertEqual(len(saved["results"]), 1)
        self.assertEqual(saved["results"][0]["status"], "current")
        self.assertEqual(saved["results"][0]["design_sha256"], doc.design_digest(edited["design"]))
        self.assertTrue(saved["warnings"])

    def test_bound_save_skips_new_result_replaced_after_capture(self):
        file = self.workspace_design()
        saved = self.bridge.save(self.path, file["id"], file["hash"])
        job = self.completed(file)
        self.bridge.record_completed(job)
        (self.projects / job.bundle).write_bytes(b"changed after capture")
        again = self.save_opened(saved)
        self.assertEqual(again["results"], [])
        self.assertEqual(doc.read(self.path)[0]["results"], [])
        self.assertTrue(any("not attached" in warning for warning in again["warnings"]))

    def test_save_as_keeps_existing_reference_when_recovered_cache_meets_sidecar_conflict(self):
        reference, raw = self.sidecar()
        self.write_document()
        opened = self.bridge.open(self.path)
        (self.projects / opened["results"][0]["project_file"]).unlink()
        target = self.root / "copy.fb"
        folder = self.root / "copy.results"
        folder.mkdir()
        conflict = folder / reference["file"]
        conflict.write_bytes(b"foreign content")
        copied = self.bridge.save(target, opened["file"]["id"], opened["file"]["hash"],
                                  document_id=opened["document"]["id"], save_as=True)
        self.assertEqual(doc.read(target)[0]["results"], [reference])
        self.assertEqual([result["status"] for result in copied["results"]], ["invalid"])
        self.assertTrue(any("unavailable" in warning for warning in copied["warnings"]))
        self.assertEqual(conflict.read_bytes(), b"foreign content")
        self.assertEqual((self.root / self.document["results_directory"] / reference["file"]).read_bytes(), raw)

    def test_legacy_and_damaged_input_snapshots_do_not_invent_provenance(self):
        file = self.workspace_design()
        job = self.completed(file)
        job.input_snapshot = None
        self.assertFalse(self.bridge.record_completed(job))
        job.input_snapshot = {"sha256": "0" * 64}
        with self.assertRaises(doc.DocumentError):
            self.bridge.record_completed(job)
        self.manager.list = lambda: [{"id": job.id, "model": file["id"], "status": "done", "bundle": job.bundle}]
        saved = self.bridge.save(self.path, file["id"], file["hash"])
        self.assertEqual(saved["results"], [])
        self.assertTrue(any("source design was not recorded" in warning for warning in saved["warnings"]))

    def test_exported_old_named_run_survives_workspace_output_replacement(self):
        file = self.workspace_design()
        self.bridge.record_completed(self.completed(file))
        saved = self.bridge.save(self.path, file["id"], file["hash"])
        edited = self.saved_edit(file)
        self.bridge.record_completed(self.completed(edited, job_id="newer-job"))
        again = self.save_opened(saved, edited)
        self.assertEqual(len(again["results"]), 2)
        self.assertEqual([r["status"] for r in again["results"]], ["stale", "current"])
        for result in again["results"]:
            self.assertEqual(doc.digest((self.projects / result["project_file"]).read_bytes()), result["sha256"])

    def test_imported_cache_corruption_is_not_exported_as_valid(self):
        self.sidecar()
        self.write_document()
        opened = self.bridge.open(self.path)
        (self.projects / opened["results"][0]["project_file"]).write_text("changed", encoding="utf-8")
        saved = self.save_opened(opened)
        self.assertEqual(saved["results"][0]["status"], "invalid")
        self.assertEqual(len(doc.read(self.path)[0]["results"]), 1)

    def test_existing_sidecar_is_never_overwritten_by_save(self):
        file = self.workspace_design()
        job = self.completed(file)
        self.bridge.record_completed(job)
        saved = self.bridge.save(self.path, file["id"], file["hash"])
        sidecar = self.path.parent / saved["document"]["results_directory"] / saved["results"][0]["file"]
        sidecar.write_bytes(b"foreign data")
        again = self.save_opened(saved)
        self.assertEqual(sidecar.read_bytes(), b"foreign data")
        self.assertEqual(again["results"][0]["status"], "invalid")

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symbolic links")
    def test_linked_results_directory_does_not_block_design_recovery(self):
        self.sidecar()
        folder = self.root / self.document["results_directory"]
        other = self.root / "outside"
        folder.rename(other)
        try:
            folder.symlink_to(other, target_is_directory=True)
        except OSError as exc:
            self.skipTest(str(exc))
        self.write_document()
        opened = self.bridge.open(self.path)
        self.assertEqual(opened["file"]["design"], self.design)
        self.assertEqual(opened["results"][0]["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
