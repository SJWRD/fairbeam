"""Saving commits before preview validation; a failed preview must not lose the new hash.

No solver or external process is needed: filesystem writes and HTTP responses are real,
while worker startup, timeout, exit and invalid-response paths use a stand-in process.
"""
import copy
import http.client
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from fairbeam import modelfiles
from fairbeam.design import blank_design
from fairbeam.jobs import JobManager
from fairbeam.server import ApiError, App, PreviewWorker, PreviewWorkerError, make_server


MALFORMED_RESPONSES = {
    "utf8": b'{"ok": "\xff"}\n', "json": b'{not json}\n',
    "array": b'[]\n', "null": b'null\n', "number": b'42\n', "boolean": b'true\n',
    "string": b'"private model output must not appear in the error"\n',
}
WORKER_FAILURES = (504, 500, "start", *MALFORMED_RESPONSES)


class PostSaveValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.models = root / "models"
        self.models.mkdir()
        manager = JobManager(root / "jobs", root / "projects", autostart=False)
        with mock.patch("fairbeam.server.detect_engines", return_value=["cpu"]), \
                mock.patch("fairbeam.server._versions", return_value={}):
            self.app = App(models_dir=self.models, projects_dir=root / "projects", jobs_dir=root / "jobs",
                           manager=manager)
        self.server = make_server(self.app, "127.0.0.1", 0, quiet=True)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        modelfiles.create_model(self.models, "editable", "x = 1\n", None)
        modelfiles.create_design(self.models, "editable_design", blank_design("editable-design", "Original"))

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.app.close()
        self.tmp.cleanup()

    def request(self, method, path, body):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, json.dumps(body).encode(), {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def worker_failure(self, status):
        worker = PreviewWorker(sys.executable)
        if status == "start":
            with mock.patch.object(worker, "_start", side_effect=OSError("test interpreter is unavailable")), \
                    mock.patch.object(worker, "stop") as stop:
                with self.assertRaises(PreviewWorkerError) as caught:
                    worker.request({"op": "validate"})
                stop.assert_called_once()
            self.assertEqual(caught.exception.status, 500)
            self.assertIn("could not start", caught.exception.message)
            return caught.exception
        if status in MALFORMED_RESPONSES:
            raw = MALFORMED_RESPONSES[status]
            worker.proc = mock.Mock(stdin=io.BytesIO(), stdout=io.BytesIO(raw), stderr=io.BytesIO())
            worker.proc.poll.return_value = None
            # Let the real reader thread read the bytes: the JSON decoder/object
            # envelope check must reject them independently of a saved file.
            with mock.patch.object(worker, "stop") as stop:
                with self.assertRaises(PreviewWorkerError) as caught:
                    worker.request({"op": "validate"})
                stop.assert_called_once()
            self.assertEqual(caught.exception.status, 500)
            self.assertEqual(caught.exception.message, "the preview worker returned an invalid response")
            return caught.exception
        # A successful start followed by EOF, or a read that never completes. The
        # timeout stand-in avoids waiting 30 seconds and leaving a blocked thread.
        worker.proc = mock.Mock(stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO())
        worker.proc.poll.return_value = None
        with mock.patch.object(worker, "stop"), mock.patch("fairbeam.server.threading.Thread") as thread:
            thread.return_value.is_alive.return_value = status == 504
            with self.assertRaises(PreviewWorkerError) as caught:
                worker.request({"op": "validate"})
        self.assertEqual(caught.exception.status, status)
        return caught.exception

    def assert_saved_warning(self, result, failure):
        self.assertFalse(result["validation"]["valid"])
        self.assertEqual(result["validation"]["error"], {
            "stage": "timeout" if failure.status == 504 else "worker", "message": failure.message,
        })

    def test_python_save_returns_committed_hash_and_next_save_has_no_false_conflict(self):
        for code in WORKER_FAILURES:
            with self.subTest(worker_status=code):
                original = modelfiles.read_model(self.models, "editable")
                failure = self.worker_failure(code)
                source = original["source"] + "# saved during preview failure\n"
                with mock.patch.object(self.app.preview, "request", side_effect=failure):
                    status, saved = self.request("PUT", "/api/models/editable/source", {
                        "source": source, "base_hash": original["hash"],
                    })
                self.assertEqual(status, 200, saved)
                self.assert_saved_warning(saved, failure)
                actual = modelfiles.read_model(self.models, "editable")
                self.assertEqual((actual["source"], actual["hash"]), (source, saved["hash"]))
                self.assertNotEqual(saved["hash"], original["hash"])
                backup = self.app.history_dir / "editable" / saved["backup"]
                self.assertEqual(backup.read_text(encoding="utf-8"), original["source"])
                with mock.patch.object(self.app, "_validate_file", return_value={"valid": True}):
                    status, again = self.request("PUT", "/api/models/editable/source", {
                        "source": source + "x = 2\n", "base_hash": saved["hash"],
                    })
                self.assertEqual(status, 200, again)
                self.assertEqual(again["hash"], modelfiles.read_model(self.models, "editable")["hash"])

    def test_design_save_returns_committed_hash_and_next_save_has_no_false_conflict(self):
        for code in WORKER_FAILURES:
            with self.subTest(worker_status=code):
                original = modelfiles.read_design_file(self.models, "editable_design")
                changed = copy.deepcopy(original["design"])
                changed["model"]["name"] = f"Saved {code}"
                failure = self.worker_failure(code)
                with mock.patch.object(self.app.preview, "request", side_effect=failure):
                    status, saved = self.request("PUT", "/api/designs/editable_design", {
                        "design": changed, "base_hash": original["hash"], "backup_scope": self.app.backup_scope,
                    })
                self.assertEqual(status, 200, saved)
                self.assert_saved_warning(saved, failure)
                actual = modelfiles.read_design_file(self.models, "editable_design")
                self.assertEqual((actual["design"], actual["hash"]), (changed, saved["hash"]))
                self.assertNotEqual(saved["hash"], original["hash"])
                self.assertEqual(saved["backup_scope"], self.app.backup_scope)
                backup = self.app.history_dir / "editable_design" / saved["backup"]
                self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), original["design"])
                changed["model"]["name"] += " next"
                with mock.patch.object(self.app, "_validate_design", return_value={"valid": True}):
                    status, again = self.request("PUT", "/api/designs/editable_design", {
                        "design": changed, "base_hash": saved["hash"],
                    })
                self.assertEqual(status, 200, again)

    def test_create_and_copy_return_201_when_post_write_preview_fails(self):
        original = (self.models / "editable_design.design.json").read_bytes()
        for code in WORKER_FAILURES:
            failure = self.worker_failure(code)
            cases = (
                ("/api/models", {"id": f"copy_py_{code}", "from": "editable"}, modelfiles.read_model),
                ("/api/designs", {"id": f"new_design_{code}", "template": "empty"}, modelfiles.read_design_file),
                ("/api/examples/copy", {"id": f"copy_design_{code}", "from": "editable_design", "name": "Copy"},
                 modelfiles.read_design_file),
            )
            for endpoint, body, reader in cases:
                with self.subTest(endpoint=endpoint, worker_status=code):
                    with mock.patch.object(self.app.preview, "request", side_effect=failure), \
                            mock.patch.object(modelfiles, "BUNDLED_DESIGNS", modelfiles.BUNDLED_DESIGNS | {"editable_design"}):
                        status, created = self.request("POST", endpoint, body)
                    self.assertEqual(status, 201, created)
                    self.assert_saved_warning(created, failure)
                    actual = reader(self.models, created["id"])
                    if endpoint != "/api/examples/copy":
                        self.assertEqual(created["hash"], actual["hash"])
        self.assertEqual((self.models / "editable_design.design.json").read_bytes(), original)

    def test_failed_write_is_not_reported_as_saved_or_validated(self):
        for model_id, reader, method, endpoint, field, changed in (
            ("editable", modelfiles.read_model, "PUT", "/api/models/editable/source", "source", "x = 2\n"),
            ("editable_design", modelfiles.read_design_file, "PUT", "/api/designs/editable_design", "design",
             blank_design("editable-design", "Changed")),
        ):
            with self.subTest(model_id=model_id):
                original = reader(self.models, model_id)
                write = modelfiles._write_atomic

                def fail_target(path, *args, **kwargs):
                    if path.parent == self.models:
                        raise PermissionError("test target is not writable")
                    return write(path, *args, **kwargs)

                with mock.patch.object(modelfiles, "_write_atomic", side_effect=fail_target), \
                        mock.patch.object(self.app.preview, "request") as preview:
                    status, result = self.request(method, endpoint, {field: changed, "base_hash": original["hash"]})
                self.assertEqual(status, 500, result)
                self.assertIn("PermissionError", result["error"])
                self.assertNotIn("hash", result)
                self.assertEqual(reader(self.models, model_id)["hash"], original["hash"])
                preview.assert_not_called()

    def test_pre_write_conflicts_scope_and_structural_errors_are_not_advisory(self):
        record = modelfiles.read_design_file(self.models, "editable_design")
        for body, expected in (
            ({"design": record["design"], "base_hash": "stale"}, 409),
            ({"design": record["design"], "base_hash": record["hash"], "backup_scope": "another-workspace"}, 409),
            ({"design": {}, "base_hash": record["hash"]}, 422),
        ):
            with self.subTest(body=body):
                with mock.patch.object(self.app.preview, "request") as preview:
                    status, result = self.request("PUT", "/api/designs/editable_design", body)
                self.assertEqual(status, expected, result)
                self.assertNotIn("validation", result)
                self.assertEqual(modelfiles.read_design_file(self.models, "editable_design")["hash"], record["hash"])
                preview.assert_not_called()

    def test_unrelated_api_errors_are_not_reclassified_as_preview_warnings(self):
        record = modelfiles.read_model(self.models, "editable")
        for code in (403, 500, 504):
            with self.subTest(code=code):
                with mock.patch.object(self.app, "_validate_file", side_effect=ApiError(code, "unrelated failure")):
                    status, result = self.request("PUT", "/api/models/editable/source", {
                        "source": record["source"], "base_hash": record["hash"],
                    })
                self.assertEqual(status, code, result)
                self.assertEqual(result["error"], "unrelated failure")

    def test_unrelated_os_and_json_errors_are_not_reclassified_as_preview_warnings(self):
        record = modelfiles.read_model(self.models, "editable")
        for failure in (OSError("unrelated read failure"), json.JSONDecodeError("unrelated JSON", "", 0)):
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(self.app, "_validate_file", side_effect=failure):
                    status, result = self.request("PUT", "/api/models/editable/source", {
                        "source": record["source"], "base_hash": record["hash"],
                    })
                self.assertEqual(status, 500, result)
                self.assertIn(type(failure).__name__, result["error"])
                self.assertNotIn("validation", result)

    def test_pre_write_worker_failure_does_not_create_a_design(self):
        original = (self.models / "editable.py").read_bytes()
        for code in WORKER_FAILURES:
            failure = self.worker_failure(code)
            with self.subTest(code=code), mock.patch.object(self.app, "design_from_python", side_effect=failure):
                status, result = self.request("POST", "/api/designs", {
                    "id": "converted", "python": {"source_model": "editable"},
                })
                self.assertEqual(status, failure.status, result)
                self.assertEqual(result["error"], failure.message)
                self.assertFalse((self.models / "converted.design.json").exists())
                self.assertEqual((self.models / "editable.py").read_bytes(), original)

    def test_direct_validation_still_raises_worker_error(self):
        for code in WORKER_FAILURES:
            failure = self.worker_failure(code)
            with self.subTest(code=code), mock.patch.object(self.app.preview, "request", side_effect=failure):
                with self.assertRaises(ApiError) as caught:
                    self.app._validate_file("editable")
                self.assertIs(caught.exception, failure)

    def test_direct_preview_api_still_fails_on_worker_errors(self):
        for code in WORKER_FAILURES:
            failure = self.worker_failure(code)
            with self.subTest(code=code), mock.patch.object(self.app.preview, "request", side_effect=failure):
                status, result = self.request("POST", "/api/preview", {"design": blank_design("probe", "Probe")})
                self.assertEqual(status, failure.status, result)
                self.assertEqual(result["error"], failure.message)
                self.assertNotIn("bundle", result)

    def test_worker_accepts_object_envelopes_with_scalar_or_list_results(self):
        for response in ({"id": 1, "ok": True, "result": "pong"},
                         {"id": 1, "ok": True, "result": []},
                         {"id": 1, "ok": False, "kind": "model", "error": "invalid model"}):
            with self.subTest(response=response):
                worker = PreviewWorker(sys.executable)
                raw = (json.dumps(response) + "\n").encode()
                worker.proc = mock.Mock(stdin=io.BytesIO(), stdout=io.BytesIO(raw), stderr=io.BytesIO())
                worker.proc.poll.return_value = None
                with mock.patch.object(worker, "stop") as stop:
                    self.assertEqual(worker.request({"op": "ping"}), response)
                    stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
