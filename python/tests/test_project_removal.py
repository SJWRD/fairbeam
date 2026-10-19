import copy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fairbeam import documents as doc, modelfiles
from fairbeam.design import blank_design
from fairbeam.document_workspace import DocumentWorkspace
from fairbeam.project_removal import archive


class ProjectRemovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.models, self.projects, self.history = (self.root / name for name in ("models", "projects", "history"))
        self.models.mkdir()
        self.projects.mkdir()
        self.workspace = DocumentWorkspace(self.models, self.projects, self.history)
        self.path = self.root / "Türkçe patch.fb"
        self.document = doc.create(blank_design("patch_test", "Patch"), "Türkçe patch.results")
        doc.save(self.path, self.document)
        self.opened = self.workspace.open(self.path)
        self.folder = self.root / self.document["results_directory"]
        self.folder.mkdir()
        (self.folder / "S11.csv").write_bytes(b"preserve results")
        self.key = self.opened["file"]["id"]

    def remove(self):
        return archive(self.workspace, self.key, str(self.path), self.folder.name)

    def test_document_results_and_workspace_leave_original_locations_recoverably(self):
        before = self.path.read_bytes()
        result = self.remove()
        recovery = Path(result["recovery_path"])
        self.assertFalse(self.path.exists())
        self.assertFalse(self.folder.exists())
        self.assertFalse((self.models / (self.key + ".design.json")).exists())
        self.assertEqual((recovery / self.path.name).read_bytes(), before)
        self.assertEqual((recovery / self.folder.name / "S11.csv").read_bytes(), b"preserve results")
        self.assertTrue(Path(result["moved_to"]).is_file())

    def test_failure_restores_document_and_results(self):
        with mock.patch.object(modelfiles, "delete_design", side_effect=OSError("busy")):
            with self.assertRaisesRegex(OSError, "busy"):
                self.remove()
        self.assertTrue(self.path.is_file())
        self.assertTrue((self.folder / "S11.csv").is_file())
        self.assertTrue((self.models / (self.key + ".design.json")).is_file())

    def test_shared_results_are_not_moved(self):
        other = copy.deepcopy(self.document)
        other["id"] = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
        doc.save(self.root / "another.fb", other)
        with self.assertRaisesRegex(doc.DocumentConflict, "shares"):
            self.remove()
        self.assertTrue(self.path.exists())
        self.assertTrue(self.folder.exists())

    def test_changed_document_or_displayed_paths_are_rejected(self):
        with self.assertRaises(doc.DocumentConflict):
            archive(self.workspace, self.key, str(self.path), "wrong.results")
        changed = copy.deepcopy(self.document)
        changed["design"]["model"]["name"] = "New name"
        doc.save(self.path, changed, doc.read(self.path)[1])
        with self.assertRaises(doc.DocumentConflict):
            self.remove()
        self.assertTrue(self.path.exists())

    def test_link_in_results_is_not_followed(self):
        target = self.root / "unrelated.txt"
        target.write_bytes(b"keep")
        try:
            (self.folder / "link").symlink_to(target)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(doc.DocumentError):
            self.remove()
        self.assertEqual(target.read_bytes(), b"keep")
        self.assertTrue(self.path.exists())

    def test_absent_results_folder_is_allowed(self):
        (self.folder / "S11.csv").unlink()
        self.folder.rmdir()
        result = self.remove()
        self.assertTrue((Path(result["recovery_path"]) / self.path.name).exists())
