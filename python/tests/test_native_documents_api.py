"""Private desktop document HTTP contract; local server only, no solver execution."""
import copy
import http.client
import io
import json
import os
import socket
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from contextlib import redirect_stdout
from unittest import mock

from fairbeam import documents as doc, modelfiles
from fairbeam.design import blank_design
from fairbeam.document_workspace import DocumentWorkspace
from fairbeam.jobs import JobManager
from fairbeam.server import App, make_server


class NativeDocumentsAPI(unittest.TestCase):
    def test_archive_requires_native_authority_scope_and_no_running_job(self):
        opened = self.opened()
        body = {"model_id": opened["file"]["id"], "path": opened["document"]["path"],
                "results_directory": opened["document"]["results_directory"], "backup_scope": self.app.backup_scope}
        self.assertEqual(self.request("archive", body, token=False)[0], 403)
        self.assertEqual(self.request("archive", body, headers={"Origin": "http://localhost"})[0], 403)
        self.assertEqual(self.request("archive", {**body, "backup_scope": "different"})[0], 409)
        with mock.patch.object(self.manager, "list", return_value=[{"model": body["model_id"], "status": "running"}]):
            self.assertEqual(self.request("archive", body)[0], 409)
        self.assertTrue(self.path.exists())
        status, result = self.request("archive", body)
        self.assertEqual(status, 200, result)
        self.assertFalse(self.path.exists())
        self.assertTrue((Path(result["recovery_path"]) / self.path.name).is_file())

    def test_first_save_accepts_dirty_snapshot_without_modifying_json(self):
        modelfiles.create_design(self.models, 'plain_draft', self.document['design'])
        _, original = self.get_design('plain_draft')
        source = modelfiles.design_path(self.models, 'plain_draft')
        before = source.read_bytes()
        draft = copy.deepcopy(original['design'])
        draft['model']['name'] = 'Unsaved native draft'
        target = self.root / 'draft.fb'
        body = {'path': str(target), 'model_id': original['id'], 'workspace_hash': original['hash'],
                'design': draft, 'save_as': True}
        raw = b' ' * 20000 + json.dumps(body).encode('utf-8')
        status, result = self.request('save', raw=raw)
        self.assertEqual(status, 200, result)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(result['file']['design'], draft)
        self.assertNotEqual(result['file']['id'], original['id'])
        self.assertEqual(doc.read(target)[0]['design'], draft)
        status, _ = self.request('save', body={**body, 'path': str(self.root / 'rejected.fb'),
                                 'model_id': result['file']['id'], 'workspace_hash': result['file']['hash'],
                                 'document_id': result['document']['id']})
        self.assertEqual(status, 400)
        self.assertFalse((self.root / 'rejected.fb').exists())

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.models = self.root / "models"
        self.models.mkdir()
        self.projects = self.root / "projects"
        self.projects.mkdir()
        self.manager = JobManager(self.root / "jobs", self.projects, autostart=False)
        self.previous_finished = mock.Mock()
        self.manager.on_finished = self.previous_finished
        with mock.patch("fairbeam.server.detect_engines", return_value=["cpu"]), \
                mock.patch("fairbeam.server._versions", return_value={}):
            self.app = App(models_dir=self.models, projects_dir=self.projects, jobs_dir=self.root / "jobs",
                           manager=self.manager)
        self.server = make_server(self.app, "127.0.0.1", 0, quiet=True)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.token = "native-document-test-token"
        self.environment = mock.patch.dict(os.environ, {"FAIRBEAM_SHUTDOWN_TOKEN": self.token})
        self.environment.start()
        self.path = self.root / "Native ünicode.fb"
        self.document = doc.create(blank_design("embedded-identity", "Native design"), "Native results")
        doc.save(self.path, self.document)

    def tearDown(self):
        self.environment.stop()
        self.server.stopping = True
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.app.close()
        self.tmp.cleanup()

    def test_model_list_labels_portable_workspace_without_hiding_other_designs(self):
        opened = self.app.documents.open(self.path)
        modelfiles.create_design(self.models, "fb_user_design", blank_design("user", "Ordinary JSON design"))
        models = {entry["key"]: entry for entry in self.app.models()["models"]}
        self.assertEqual(models[opened["file"]["id"]]["document"],
                         {"id": self.document["id"], "path": str(self.path.resolve()),
                          "results_directory": self.document["results_directory"]})
        self.assertIn("fb_user_design", models)
        self.assertNotIn("document", models["fb_user_design"])

    def request(self, action="open", body=None, headers=None, token=True, method="POST", raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json"}
        if token:
            h["X-Fairbeam-Token"] = self.token
        h.update(headers or {})
        data = raw if raw is not None else json.dumps(body if body is not None else {"path": str(self.path)}).encode()
        conn.request(method, "/api/native-document/" + action, body=data, headers=h)
        response = conn.getresponse()
        value = json.loads(response.read())
        conn.close()
        return response.status, value

    def opened(self):
        status, result = self.request()
        self.assertEqual(status, 200, result)
        return result

    def get_design(self, key):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/api/designs/" + key)
        response = conn.getresponse()
        value = json.loads(response.read())
        conn.close()
        return response.status, value

    def save_body(self, opened):
        return {"path": str(self.path), "model_id": opened["file"]["id"],
                "workspace_hash": opened["file"]["hash"], "base_hash": opened["document"]["revision"],
                "document_id": opened["document"]["id"]}

    def test_route_is_disabled_without_the_server_private_token(self):
        with mock.patch.dict(os.environ, {"FAIRBEAM_SHUTDOWN_TOKEN": ""}):
            self.assertEqual(self.request()[0], 404)
        self.assertEqual(list(self.models.glob("*.design.json")), [])

    def test_missing_and_wrong_token_are_rejected_before_body_or_path_access(self):
        with mock.patch.object(self.app.documents, "open", side_effect=AssertionError("must not access files")):
            for headers in ({}, {"X-Fairbeam-Token": "wrong"}, {"X-Fairbeam-Token": "\u00e9"}):
                status, result = self.request(token=False, headers=headers, raw=b"invalid-json")
                self.assertEqual(status, 403)
                self.assertNotIn(self.token, json.dumps(result))

    def test_browser_origin_and_fetch_headers_are_rejected_even_with_valid_token(self):
        for headers in ({"Origin": f"http://127.0.0.1:{self.port}"}, {"Origin": "http://localhost:5313"},
                        {"Origin": "https://untrusted.example"}, {"Sec-Fetch-Site": "same-origin"},
                        {"Sec-Fetch-Mode": "cors"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request(headers=headers)[0], 403)
        self.assertEqual(list(self.models.glob("*.design.json")), [])

    def test_rejection_delivers_response_when_body_arrives_after_headers(self):
        # http.client normally sends headers/body separately. Delaying the second
        # write deterministically exposed the Windows reset seen intermittently in CI.
        data = json.dumps({"path": str(self.path)}).encode()
        with mock.patch.object(self.app.documents, "open", side_effect=AssertionError("must not access files")):
            for _ in range(5):
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
                try:
                    conn.putrequest("POST", "/api/native-document/open")
                    for name, value in {"Content-Type": "application/json", "Content-Length": str(len(data)),
                                        "X-Fairbeam-Token": self.token, "Sec-Fetch-Mode": "cors"}.items():
                        conn.putheader(name, value)
                    conn.endheaders()
                    time.sleep(0.02)
                    conn.send(data)
                    response = conn.getresponse()
                    self.assertEqual(response.status, 403)
                    self.assertEqual(json.loads(response.read())["status"], 403)
                    self.assertEqual(response.getheader("Connection"), "close")
                finally:
                    conn.close()
        self.assertEqual(list(self.models.glob("*.design.json")), [])

    def test_rejection_does_not_wait_for_an_unsent_oversized_body(self):
        completed = threading.Event()
        handler = self.server.RequestHandlerClass
        finish = handler._finish_rejection

        def observe(request):
            try:
                return finish(request)
            finally:
                completed.set()

        with mock.patch.object(handler, "_finish_rejection", observe), \
                socket.create_connection(("127.0.0.1", self.port), timeout=2) as conn:
            conn.sendall((f"POST /api/native-document/open HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                          f"Content-Length: {1 << 30}\r\n\r\n").encode())
            response = conn.recv(4096)
            self.assertIn(b"403", response.split(b"\r\n", 1)[0])
            # Keep the raw peer open without sending the promised body. The handler
            # must still return instead of consuming an unbounded stream or waiting.
            self.assertTrue(completed.wait(2), "rejection waited for the missing request body")

    def test_body_limit_content_type_json_and_http_method(self):
        self.assertEqual(self.request(raw=b" " * (16 * 1024 + 1))[0], 413)
        self.assertEqual(self.request(headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request(raw=b"not-json")[0], 400)
        self.assertEqual(self.request(raw=b"[]")[0], 400)
        self.assertEqual(self.request(method="GET")[0], 405)

    def test_invalid_requests_are_rejected_without_writes(self):
        for body in ({}, {"path": []}, {"path": "relative.fb"}, {"path": str(self.path), "design": {}},
                     {"path": str(self.path.with_suffix(".json"))}):
            with self.subTest(body=body):
                self.assertEqual(self.request(body=body)[0], 400)
        body = {"path": str(self.path), "model_id": "design_key", "workspace_hash": "a" * 64}
        for updates in ({"model_id": []}, {"workspace_hash": []}, {"workspace_hash": "bad"},
                        {"save_as": 1}, {"document_id": {}}, {"base_hash": []}, {"base_hash": "bad"},
                        {"results": []}, {"design": {}}):
            with self.subTest(updates=updates):
                self.assertEqual(self.request("save", body={**body, **updates})[0], 400)
        self.assertEqual(list(self.models.glob("*.design.json")), [])

    def test_open_returns_scope_stable_key_and_original_embedded_model(self):
        first = self.opened()
        second = self.opened()
        self.assertEqual(first["file"]["id"], second["file"]["id"])
        self.assertEqual(first["file"]["backup_scope"], self.app.backup_scope)
        self.assertEqual(first["file"]["document_id"], self.document["id"])
        self.assertEqual(first["file"]["design"], self.document["design"])
        self.assertEqual(first["document"]["revision"], doc.read(self.path)[1])
        self.assertEqual(len(list(self.models.glob("*.design.json"))), 1)
        self.assertEqual(json.loads((self.projects / "index.json").read_text())["projects"], [])
        self.assertNotIn(self.token, json.dumps(first))

    def test_design_identity_survives_fresh_adapter_and_save_as_without_exposing_path(self):
        modelfiles.create_design(self.models, "plain_key", self.document["design"])
        status, legacy = self.get_design("plain_key")
        self.assertEqual(status, 200, legacy)
        self.assertNotIn("document_id", legacy)
        self.assertFalse(self.app.documents.state_dir.exists())
        target = self.root / "first-save.fb"
        status, saved = self.request("save", body={"path": str(target), "model_id": legacy["id"],
                                    "workspace_hash": legacy["hash"], "save_as": True})
        self.assertEqual(status, 200, saved)
        self.assertNotEqual(saved["file"]["id"], "plain_key")
        self.assertTrue(saved["file"]["id"].startswith("fb_"))
        self.assertEqual(saved["file"]["document_id"], saved["document"]["id"])
        self.app.documents = DocumentWorkspace(self.models, self.projects, self.app.history_dir, self.manager)
        status, restored = self.get_design(saved["file"]["id"])
        self.assertEqual(status, 200, restored)
        self.assertEqual(restored["document_id"], saved["document"]["id"])
        self.assertEqual(set(restored) - set(legacy), {"document_id"})
        self.assertNotIn(str(target), json.dumps(restored))
        status, copied = self.request("save", body={"path": str(self.root / "copy.fb"),
                                     "model_id": restored["id"], "workspace_hash": restored["hash"],
                                     "document_id": restored["document_id"], "save_as": True})
        self.assertEqual(status, 200, copied)
        self.assertNotEqual(copied["file"]["id"], restored["id"])
        self.assertNotEqual(copied["file"]["document_id"], restored["document_id"])
        self.assertEqual(self.get_design(copied["file"]["id"])[1]["document_id"], copied["document"]["id"])
        self.assertEqual(self.get_design(saved["file"]["id"])[1]["document_id"], saved["document"]["id"])
        self.assertEqual(self.get_design("plain_key")[1], legacy)

    def test_design_read_refuses_untrusted_identity_instead_of_legacy_fallback(self):
        opened = self.opened()
        with mock.patch.object(self.app.documents, "document_id_for", side_effect=doc.DocumentError("state is a link")):
            status, result = self.get_design(opened["file"]["id"])
        self.assertEqual(status, 409)
        self.assertIn("state is a link", result["error"])
        self.assertNotIn("design", result)

    def test_catalog_uses_workspace_key_and_keeps_embedded_result_identity(self):
        opened = self.opened()
        entries = self.app.models()["models"]
        entry = next(entry for entry in entries if entry["key"] == opened["file"]["id"])
        self.assertEqual(entry["model"]["id"], "embedded-identity")
        self.assertNotEqual(entry["key"], entry["model"]["id"])
        self.assertEqual(entry["kind"], "design")

    def test_open_imports_only_valid_result_and_rebuilds_index(self):
        raw = json.dumps({"schema": "fairbeam.project/1", "model": self.document["design"]["model"],
                          "results": {"bands": [{"f_center": 1e9}]}}).encode()
        folder = self.root / self.document["results_directory"]
        folder.mkdir()
        (folder / "valid.json").write_bytes(raw)
        self.document["results"] = [{"file": "valid.json", "sha256": doc.digest(raw),
                                      "design_sha256": doc.design_digest(self.document["design"])}]
        doc.save(self.path, self.document, doc.read(self.path)[1])
        opened = self.opened()
        entries = json.loads((self.projects / "index.json").read_text())["projects"]
        self.assertEqual(entries[0]["file"], opened["results"][0]["project_file"])
        self.assertEqual(entries[0]["model"], "embedded-identity")

    def test_normal_save_commits_only_the_requested_workspace_revision(self):
        opened = self.opened()
        file = opened["file"]
        design = copy.deepcopy(file["design"])
        design["model"]["name"] = "Edited native file"
        saved = modelfiles.save_design(self.models, self.app.history_dir, file["id"], design, file["hash"])
        status, result = self.request("save", body={**self.save_body(opened), "workspace_hash": saved["hash"]})
        self.assertEqual(status, 200, result)
        self.assertEqual(doc.read(self.path)[0]["design"], design)
        self.assertNotEqual(result["document"]["revision"], opened["document"]["revision"])
        self.assertEqual(result["file"]["backup_scope"], self.app.backup_scope)
        old_bytes = self.path.read_bytes()
        self.assertEqual(self.request("save", body=self.save_body(opened))[0], 409)
        self.assertEqual(self.path.read_bytes(), old_bytes)

    def test_external_document_conflict_preserves_external_bytes(self):
        opened = self.opened()
        document, revision = doc.read(self.path)
        document["design"]["model"]["name"] = "External editor"
        doc.save(self.path, document, revision)
        external_bytes = self.path.read_bytes()
        status, _ = self.request("save", body=self.save_body(opened))
        self.assertEqual(status, 409)
        self.assertEqual(self.path.read_bytes(), external_bytes)

    def test_index_failure_after_commit_returns_new_revision_with_warning(self):
        opened = self.opened()
        file = opened["file"]
        design = copy.deepcopy(file["design"])
        design["model"]["name"] = "Committed even if index unavailable"
        saved = modelfiles.save_design(self.models, self.app.history_dir, file["id"], design, file["hash"])
        with mock.patch("fairbeam.cli.rebuild_index", side_effect=OSError("index unavailable")):
            status, result = self.request("save", body={**self.save_body(opened), "workspace_hash": saved["hash"]})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["document"]["revision"], doc.read(self.path)[1])
        self.assertTrue(result["warnings"])

    def test_optional_provenance_failure_does_not_skip_studies_or_prior_callback(self):
        job = SimpleNamespace(kind="run", bundle="run.json")
        with mock.patch.object(self.app.documents, "record_completed", side_effect=TypeError("malformed legacy job")), \
                mock.patch.object(self.app.studies, "on_finished") as studies, redirect_stdout(io.StringIO()) as log:
            self.manager.on_finished(job)
        studies.assert_called_once_with(job)
        self.previous_finished.assert_called_once_with(job)
        self.assertIn("TypeError", log.getvalue())

    def test_index_io_failure_does_not_skip_studies_or_prior_callback(self):
        job = SimpleNamespace(kind="run", bundle="run.json")
        with mock.patch.object(self.app.documents, "record_completed"), \
                mock.patch("fairbeam.cli.os.replace", side_effect=OSError("read-only index")), \
                mock.patch.object(self.app.studies, "on_finished") as studies, redirect_stdout(io.StringIO()) as log:
            self.manager.on_finished(job)
        studies.assert_called_once_with(job)
        self.previous_finished.assert_called_once_with(job)
        self.assertIn("result index was not updated", log.getvalue())


if __name__ == "__main__":
    unittest.main()
