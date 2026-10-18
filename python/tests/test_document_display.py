"""Portable project labels never read external files or hide ordinary designs."""
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest import mock

from fairbeam import documents as doc
from fairbeam.design import blank_design
from fairbeam.document_workspace import DocumentWorkspace, STATE_SCHEMA


class DocumentDisplayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bridge = DocumentWorkspace(self.root / "models", self.root / "projects", self.root / "history")
        self.identity = str(uuid.uuid4())
        self.state = {"schema": STATE_SCHEMA, "id": self.identity, "model_id": "fb_user_design",
                      "path": str(self.root / "İzmir 文档.fb"), "revision": "a" * 64}

    def write(self, state=None):
        state = self.state if state is None else state
        self.bridge.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.bridge.state_dir / (state["id"] + ".json")
        path.write_text(json.dumps(state), encoding="utf-8")
        return path

    def test_missing_metadata_is_read_only(self):
        self.assertEqual(self.bridge.display_documents({"fb_user_design"}), {})
        self.assertFalse(self.bridge.state_dir.exists())

    def test_only_requested_models_get_display_only_paths(self):
        self.write()
        with mock.patch.object(doc, "read", side_effect=AssertionError("No external reads")):
            self.assertEqual(self.bridge.display_documents({"ordinary"}), {})
            self.assertEqual(self.bridge.display_documents({"fb_user_design", "ordinary"}), {
                "fb_user_design": {"id": self.identity, "path": self.state["path"]}})
        self.assertFalse(Path(self.state["path"]).exists(), "missing/moved sources remain identifiable")

    def test_invalid_records_are_optional_not_list_failures(self):
        for field, value in [("schema", "other"), ("model_id", "../escape"), ("path", "relative.fb"),
                             ("path", []), ("path", str(self.root / "wrong.json")), ("revision", None),
                             ("revision", "invalid")]:
            with self.subTest(field=field, value=value):
                state = {**self.state, field: value}
                self.write(state)
                self.assertEqual(self.bridge.display_documents({"fb_user_design"}), {})
        path = self.write()
        path.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.bridge.display_documents({"fb_user_design"}), {})
        with mock.patch.object(Path, "lstat", side_effect=PermissionError):
            self.assertEqual(self.bridge.display_documents({"fb_user_design"}), {})

    def test_ambiguous_binding_does_not_pick_an_arbitrary_path(self):
        self.write()
        other = {**self.state, "id": str(uuid.uuid4()), "path": str(self.root / "Other.fb")}
        self.write(other)
        self.assertEqual(self.bridge.display_documents({"fb_user_design"}), {})

    def test_linked_state_file_is_not_followed(self):
        path = self.write()
        path.unlink()
        target = self.root / "outside.json"
        target.write_text(json.dumps(self.state), encoding="utf-8")
        try:
            path.symlink_to(target)
        except OSError:
            self.skipTest("symlink creation unavailable")
        self.assertEqual(self.bridge.display_documents({"fb_user_design"}), {})

    def test_linked_state_directory_is_not_followed(self):
        target = self.root / "elsewhere"
        target.mkdir()
        (target / (self.identity + ".json")).write_text(json.dumps(self.state), encoding="utf-8")
        self.bridge.state_dir.parent.mkdir()
        try:
            self.bridge.state_dir.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation unavailable")
        self.assertEqual(self.bridge.display_documents({"fb_user_design"}), {})

    def test_real_open_and_save_as_keep_distinct_labels(self):
        self.bridge.models_dir.mkdir()
        document = doc.create(blank_design("patch", "Same name"), "Original.results")
        original = self.root / "Original.fb"
        doc.save(original, document)
        opened = self.bridge.open(original)
        copied = self.bridge.save(self.root / "Copy.fb", opened["file"]["id"], opened["file"]["hash"],
                                  document_id=document["id"], save_as=True)
        keys = {opened["file"]["id"], copied["file"]["id"]}
        labels = self.bridge.display_documents(keys)
        self.assertEqual(set(labels), keys)
        self.assertEqual({Path(item["path"]).name for item in labels.values()}, {"Original.fb", "Copy.fb"})
        self.assertEqual(doc.read(original)[0], document)


if __name__ == "__main__":
    unittest.main()
