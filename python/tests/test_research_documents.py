"""Driven Elmer document ownership and export provenance; no solver is launched."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fairbeam import documents as doc, modelfiles, research
from fairbeam.design import blank_design
from fairbeam.document_workspace import DocumentWorkspace
from fairbeam.jobs import Job, JobManager
from fairbeam.server import ApiError, App, backup_scope_for


class ResearchDocumentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.models, self.projects = self.root / "models", self.root / "projects"
        self.models.mkdir()
        self.projects.mkdir()
        self.manager = JobManager(self.root / "jobs", self.projects, autostart=False)
        self.addCleanup(self.manager.shutdown)
        self.scope = backup_scope_for(self.models)
        self.bridge = DocumentWorkspace(self.models, self.projects, self.root / "history", self.manager, backup_scope=self.scope)
        self.path = self.root / "original.fb"
        doc.save(self.path, doc.create(blank_design("shared-model", "Original"), "original.results"))
        self.opened = self.bridge.open(self.path)
        self.file = self.opened["file"]
        self.app = App.__new__(App)
        self.app.models_dir = self.models
        self.app.backup_scope = backup_scope_for(self.models)
        self.app.manager = self.manager

    def body(self, file=None):
        file = file or self.file
        return {"backend": "elmer", "path": "unused", "settings": {"analysis": "driven"},
                "design": copy.deepcopy(file["design"]),
                "source": {"model_id": file["id"], "workspace_hash": file["hash"], "backup_scope": self.app.backup_scope}}

    @staticmethod
    def prepared(body):
        return {"schema": "fairbeam-research-input-1", "backend": body["backend"], "path": body["path"],
                "settings": copy.deepcopy(body["settings"]), "design": copy.deepcopy(body["design"]),
                "normalized": {}, "binary_sha256": {}}

    def submit(self, body=None):
        body = body or self.body()
        with mock.patch("fairbeam.research.prepare", side_effect=self.prepared):
            reply = self.app.research_submit(body)
        return self.manager.get(reply["id"])

    def complete(self, job):
        bundle = {"schema": "fairbeam.project/1", "model": self.file["design"]["model"], "results": {"bands": []},
                  "experimental": {"input_sha256": job.research["snapshot_sha256"]}}
        raw = research.encode(bundle)
        job.bundle = "elmer-" + job.id + ".json"
        (self.projects / job.bundle).write_bytes(raw)
        value = {"schema": "fairbeam.elmer-driven-result/1", "backend": "elmer",
                 "research_schema": "fairbeam-research-result-1", "status": "results_exported",
                 "precision_converged": False, "frequency_hz": [2e9, 3e9], "s11_real": [0, 0], "s11_imag": [0, 0],
                 "farfield": {"directivity": [[1] * 72 for _ in range(37)], "gain_qualified": False},
                 "input_sha256": job.research["snapshot_sha256"], "bundle_sha256": doc.digest(raw)}
        output = job.dir / "research"
        output.mkdir()
        result_raw = research.encode(value)
        (output / "result.json").write_bytes(result_raw)
        job.research["result_sha256"] = doc.digest(result_raw)
        job.result, job.status = value, "done"
        job.save()
        return raw

    def save(self, opened=None, path=None, file=None):
        opened = opened or self.opened
        file = file or opened["file"]
        return self.bridge.save(path or self.path, file["id"], file["hash"],
                                base_hash=opened["document"]["revision"], document_id=opened["document"]["id"])

    def test_bound_job_preserves_original_source_and_exact_result_through_restart_and_save(self):
        body = self.body()
        job = self.submit(body)
        self.assertEqual(job.model, self.file["id"])
        self.assertEqual(job.model_id, self.file["design"]["model"]["id"])
        self.assertNotEqual(job.model, job.model_id)
        body["design"]["model"]["name"] = "Changed request after admission"
        body["source"]["model_id"] = "forged"
        _, spec = research.load_snapshot(job)
        self.assertEqual(spec["design"], self.file["design"])
        self.assertEqual(spec["source"], job.research["source"])
        self.assertEqual(spec["source"]["design_sha256"], doc.design_digest(self.file["design"]))
        self.assertEqual(Path(self.manager.input_path(job)).name, "research.json")
        raw = self.complete(job)
        restored = Job.load(self.manager.root, job.dir)
        self.assertEqual(restored.model, self.file["id"])
        self.assertTrue(self.bridge.record_completed(restored))
        saved = self.save()
        self.assertEqual([r["status"] for r in saved["results"]], ["current"])
        document, _ = doc.read(self.path)
        reference = document["results"][0]
        self.assertEqual(reference["sha256"], doc.digest(raw))
        self.assertEqual(reference["design_sha256"], doc.design_digest(self.file["design"]))
        self.assertEqual((self.root / document["results_directory"] / reference["file"]).read_bytes(), raw)
        changed = copy.deepcopy(self.file["design"])
        changed["model"]["name"] = "Later source edit"
        modelfiles.save_design(self.models, self.bridge.history_dir, self.file["id"], changed, self.file["hash"])
        edited = modelfiles.read_design_file(self.models, self.file["id"])
        self.assertEqual([r["status"] for r in self.save(saved, file=edited)["results"]], ["stale"])

    def test_source_scope_hash_design_and_shape_are_checked_before_admission(self):
        variants = []
        for field, value in (("backup_scope", "another-workspace"), ("workspace_hash", "0" * 64)):
            body = self.body()
            body["source"][field] = value
            variants.append((body, 409))
        body = self.body()
        body["design"]["model"]["name"] = "Unsaved edits"
        variants.append((body, 409))
        body = self.body()
        body["source"]["verified"] = True
        variants.append((body, 400))
        body = self.body()
        body["settings"]["analysis"] = "modes"
        variants.append((body, 400))
        body = self.body()
        del body["source"]["backup_scope"]
        variants.append((body, 400))
        for body, status in variants:
            with self.subTest(body=body), mock.patch("fairbeam.research.prepare") as prepare:
                with self.assertRaises(ApiError) as error:
                    self.app.research_submit(body)
                self.assertEqual(error.exception.status, status)
                prepare.assert_not_called()
        self.assertEqual(self.manager.list(), [])

    def test_admission_cannot_replace_the_original_source_design_or_trust_client_flags(self):
        spec = self.prepared(self.body())
        spec["design"]["model"]["name"] = "Adapted by admission"
        with mock.patch("fairbeam.research.prepare", return_value=spec), self.assertRaisesRegex(ApiError, "original source"):
            self.app.research_submit(self.body())
        body = self.body()
        body["verified"] = True
        with self.assertRaisesRegex(ApiError, "expects"):
            self.app.research_submit(body)
        with self.assertRaisesRegex(ValueError, "expects"):
            self.manager.submit_research(self.body())  # source is not a trusted method parameter
        self.assertEqual(self.manager.list(), [])

    def test_completed_capture_rejects_changed_input_result_and_bundle(self):
        for part in ("snapshot", "source", "result", "bundle", "bundle-input"):
            with self.subTest(part=part):
                job = self.submit()
                self.complete(job)
                if part == "snapshot":
                    (job.dir / "input/research.json").write_bytes(b"{}")
                elif part == "source":
                    job.research["source"]["design_sha256"] = "0" * 64
                elif part == "result":
                    output = job.dir / "research/result.json"
                    value = json.loads(output.read_bytes())
                    value["note"] = "Changed after completion"
                    output.write_bytes(research.encode(value))
                else:
                    output = self.projects / job.bundle
                    bundle = json.loads(output.read_bytes())
                    if part == "bundle":
                        bundle["model"]["name"] = "Changed exported bytes"
                    else:
                        bundle["experimental"]["input_sha256"] = "0" * 64
                    output.write_bytes(research.encode(bundle))
                    if part == "bundle-input":
                        result_path = job.dir / "research/result.json"
                        value = json.loads(result_path.read_bytes())
                        value["bundle_sha256"] = doc.digest(output.read_bytes())
                        raw = research.encode(value)
                        result_path.write_bytes(raw)
                        job.research["result_sha256"] = doc.digest(raw)
                with self.assertRaises((doc.DocumentError, ValueError)):
                    self.bridge.record_completed(job)
        saved = self.save()
        self.assertEqual(saved["results"], [])
        self.assertTrue(any("not attached" in warning for warning in saved["warnings"]))

    def test_same_model_copy_and_source_less_research_are_not_attached(self):
        copied_path = self.root / "copy.fb"
        copied = self.bridge.save(copied_path, self.file["id"], self.file["hash"],
                                  document_id=self.opened["document"]["id"], save_as=True)
        job = self.submit()
        self.complete(job)
        self.assertTrue(self.bridge.record_completed(job))
        self.assertEqual(self.save(copied, copied_path)["results"], [])
        body = self.body()
        del body["source"]
        legacy = self.submit(body)
        self.assertEqual(legacy.model, self.file["design"]["model"]["id"])
        self.assertNotIn("source", legacy.research)
        self.complete(legacy)
        self.assertFalse(self.bridge.record_completed(legacy))
        self.assertEqual(len(self.save()["results"]), 1)

    def test_reused_history_cannot_capture_or_attach_another_models_roots_research(self):
        job = self.submit()
        self.complete(job)
        self.assertTrue(self.bridge.record_completed(job))
        other_models = self.root / "other-models"
        other_models.mkdir()
        (other_models / self.file["file"]).write_text(json.dumps(self.file["design"]), encoding="utf-8")
        other_file = modelfiles.read_design_file(other_models, self.file["id"])
        other = DocumentWorkspace(other_models, self.projects, self.bridge.history_dir, self.manager,
                                  backup_scope=backup_scope_for(other_models))
        with self.assertRaisesRegex(doc.DocumentError, "workspace scope"):
            other.record_completed(job)
        self.assertEqual(other._completed(self.file["id"])[0], [])
        saved = other.save(self.root / "different-root.fb", other_file["id"], other_file["hash"])
        self.assertEqual(saved["results"], [])
        self.assertFalse(any("not attached" in warning for warning in saved["warnings"]))
        restored = DocumentWorkspace(self.models, self.projects, self.bridge.history_dir, self.manager, backup_scope=self.scope)
        self.assertEqual(len(restored._completed(self.file["id"])[0]), 1, "foreign-scope Save must retain the original provenance record")

    def test_scope_gate_does_not_reclassify_ordinary_completed_records(self):
        job = self.manager.submit(model=self.file["id"], model_id=self.file["design"]["model"]["id"],
                                  model_path=self.file["file"], design_input=json.dumps(self.file["design"]))
        job.bundle, job.status = "ordinary.json", "done"
        (self.projects / job.bundle).write_bytes(research.encode({"schema": "fairbeam.project/1", "model": self.file["design"]["model"]}))
        self.assertTrue(self.bridge.record_completed(job))
        other = DocumentWorkspace(self.models, self.projects, self.bridge.history_dir, self.manager, backup_scope="another-scope")
        records, warnings = other._completed(self.file["id"])
        self.assertEqual(len(records), 1)
        self.assertNotIn("backup_scope", records[0])
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
