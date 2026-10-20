"""Localized metadata is additive: admission, English diagnostics and saves stay compatible."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from fairbeam import server
from fairbeam.messages import Message, descriptor


class ServerMessagesTests(unittest.TestCase):
    def test_slug_and_settings_preserve_turkish_labels(self):
        fixtures = json.loads((Path(__file__).parent / "fixtures/run_slugs.json").read_text(encoding="utf-8"))
        for label, expected in fixtures:
            with self.subTest(label=label):
                self.assertEqual(server.slugify(label), expected)
        app = SimpleNamespace(cpu=4, physical=2, engines=["cpu"])
        for label in ("Çığ", "Işık testi", "C\u0327ıg\u0306"):
            errors = {}
            value = server.App._settings(app, {"name": label, "threads": 1}, errors)
            self.assertEqual(errors, {})
            self.assertEqual(value["label"], label)
            self.assertEqual(value["name"], server.slugify(label))
        errors = {}
        server.App._settings(app, {"name": "!!!", "threads": 1}, errors)
        self.assertEqual(errors["name"], "needs at least one letter or digit")
        self.assertEqual(descriptor(errors["name"])["code"], "field.name_letters")

    def test_busy_design_uses_display_name_and_does_not_delete(self):
        app = SimpleNamespace(_require_backup_scope=lambda body: None, models_dir=Path("models"),
            manager=SimpleNamespace(list=lambda: [{"model": "test_patch", "status": "queued"}]))
        with mock.patch.object(server.modelfiles, "read_design_file", return_value={"design": {"model": {"name": "Çığ anteni"}}}), \
             mock.patch.object(server.modelfiles, "delete_design") as remove:
            with self.assertRaises(server.ApiError) as caught:
                server.App.delete_design(app, "test_patch", {})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.extra["code"], "run_in_progress")
        self.assertEqual(caught.exception.extra["args"], {"name": "Çığ anteni"})
        self.assertIn("Çığ anteni", caught.exception.message)
        remove.assert_not_called()

    def test_invalid_sweep_has_localizable_header_and_nested_field(self):
        app = server.App.__new__(server.App)
        app.cpu, app.physical, app.engines = 4, 2, ["cpu"]
        app._validated = lambda body: ({"params": [{"key": "n", "type": "int", "default": 1}]}, {}, {})
        app._refuse_broken_design = lambda model: None
        with self.assertRaises(server.ApiError) as caught:
            app.submit_sweep({"threads": 1, "sweep": [{"key": "n", "values": [1.5]}]})
        error = caught.exception
        encoded = json.loads(json.dumps({"error": error.message, **error.extra}))
        self.assertEqual(encoded["error"], "invalid sweep")
        self.assertEqual(encoded["fields"]["n"], "1.5: must be a whole number")
        self.assertEqual(encoded["code"], "invalid_sweep")
        self.assertEqual(encoded["field_details"]["n"], {"code": "field.value", "args": {
            "value": "1.5", "reason": {"code": "field.integer", "args": {}}}})

    def test_preview_timeout_retains_english_and_seconds_metadata(self):
        worker = server.PreviewWorker("unused", timeout=30)
        worker.proc = mock.Mock()
        worker.proc.poll.return_value = None
        with mock.patch.object(server.threading, "Thread") as thread, mock.patch.object(worker, "stop"):
            thread.return_value.is_alive.return_value = True
            with self.assertRaises(server.PreviewWorkerError) as caught:
                worker.request({"op": "preview_design"})
        self.assertEqual(caught.exception.message, "geometry build did not finish within 30 s")
        self.assertEqual(caught.exception.extra, {"code": "preview_timeout", "args": {"seconds": "30"}})

    def test_old_plain_errors_and_fields_are_unchanged(self):
        error = server.ApiError(422, "older diagnostic", fields={"name": "older field"})
        self.assertEqual(error.extra, {"fields": {"name": "older field"}})
        self.assertIsNone(descriptor("plain warning"))
        self.assertEqual(json.loads(json.dumps([Message("x", "English warning", file="x.json")])), ["English warning"])


if __name__ == "__main__":
    unittest.main()
