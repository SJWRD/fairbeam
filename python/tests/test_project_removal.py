import copy
import os
import posixpath
import subprocess
from pathlib import Path
import tempfile
import unittest
import unicodedata
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

    def _shared_results_alias(self, name, *, posix_comparison=False):
        alias = self.root / name
        if not alias.exists() or not os.path.samefile(alias, self.folder):
            self.skipTest("filesystem does not resolve this case/Unicode directory alias")
        other = doc.create(blank_design("other_patch", "Other"), name)
        sibling = self.root / "other.fb"
        doc.save(sibling, other)
        before = {p: p.read_bytes() for p in (self.path, sibling, self.folder / "S11.csv")}
        # NTFS can exercise the Darwin string-comparison failure without claiming
        # native macOS coverage. The alias still has a real filesystem identity.
        comparator = posixpath.normcase if posix_comparison else os.path.normcase
        with mock.patch("fairbeam.project_removal.os.path.normcase", comparator):
            with self.assertRaisesRegex(doc.DocumentConflict, "shares"):
                self.remove()
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)
        self.assertFalse((self.root / "Fairbeam Deleted").exists())

    def test_shared_results_case_alias_uses_physical_identity(self):
        self._shared_results_alias(self.folder.name.swapcase(), posix_comparison=True)

    def test_shared_results_native_case_alias(self):
        self._shared_results_alias(self.folder.name.swapcase())

    def test_shared_results_native_nfd_alias(self):
        self._shared_results_alias(unicodedata.normalize("NFD", self.folder.name))

    def test_distinct_physical_results_are_not_shared(self):
        other = doc.create(blank_design("other_patch", "Other"), "other.results")
        doc.save(self.root / "other.fb", other)
        other_folder = self.root / other["results_directory"]
        other_folder.mkdir()
        (other_folder / "keep.txt").write_bytes(b"other project")
        self.remove()
        self.assertEqual((other_folder / "keep.txt").read_bytes(), b"other project")
        self.assertTrue((self.root / "other.fb").is_file())

    def test_linked_sibling_results_are_not_followed_or_moved(self):
        alias = self.root / "linked.results"
        try:
            alias.symlink_to(self.folder, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation unavailable")
        other = doc.create(blank_design("other_patch", "Other"), alias.name)
        doc.save(self.root / "other.fb", other)
        with self.assertRaises(doc.DocumentConflict):
            self.remove()
        self.assertEqual((self.folder / "S11.csv").read_bytes(), b"preserve results")
        self.assertTrue(self.path.is_file())
        self.assertTrue(alias.is_symlink())

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_junction_sibling_results_are_not_followed_or_moved(self):
        alias = self.root / "junction.results"
        created = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(self.folder)],
                                 capture_output=True, check=False)
        if created.returncode:
            self.skipTest("junction creation unavailable")
        self.addCleanup(lambda: os.rmdir(alias) if alias.exists() else None)
        other = doc.create(blank_design("other_patch", "Other"), alias.name)
        doc.save(self.root / "other.fb", other)
        with self.assertRaises(doc.DocumentConflict):
            self.remove()
        self.assertTrue(doc._linked(alias.lstat()))
        self.assertEqual((self.folder / "S11.csv").read_bytes(), b"preserve results")
        self.assertTrue(self.path.is_file())

    def _remove_with_directory_alias(self, alias_name):
        alias = self.root / alias_name
        # APFS/exFAT can resolve this spelling natively. On filesystems that
        # distinguish the names, use a hard link to supply the same file identity.
        if not alias.exists():
            os.link(self.path, alias)
        self.assertTrue(os.path.samefile(alias, self.path))
        before = self.path.read_bytes()
        iterdir = Path.iterdir

        def entries(parent):
            if parent == self.root:
                return iter([alias])
            return iterdir(parent)

        with mock.patch.object(Path, "iterdir", entries):
            result = self.remove()
        recovery = Path(result["recovery_path"])
        self.assertEqual((recovery / self.path.name).read_bytes(), before)
        self.assertEqual((recovery / self.folder.name / "S11.csv").read_bytes(), b"preserve results")

    def test_nfd_directory_entry_for_nfc_opened_document(self):
        alias_name = unicodedata.normalize("NFD", self.path.name)
        self.assertNotEqual(alias_name, self.path.name)
        self._remove_with_directory_alias(alias_name)

    def test_case_alias_directory_entry_for_opened_document(self):
        self._remove_with_directory_alias(self.path.name.upper())

    def test_finder_duplicate_with_same_document_id_still_blocks(self):
        duplicate = self.root / "Finder duplicate.fb"
        duplicate.write_bytes(self.path.read_bytes())
        self.assertFalse(os.path.samefile(duplicate, self.path))
        with self.assertRaisesRegex(doc.DocumentConflict, "shares"):
            self.remove()
        self.assertTrue(self.path.exists())
        self.assertTrue(self.folder.exists())
        self.assertFalse((self.root / "Fairbeam Deleted").exists())

    def test_appledouble_siblings_are_not_read_as_documents(self):
        # AppleDouble magic plus non-UTF-8 metadata, as on exFAT/FAT/SMB.
        metadata = b"\x00\x05\x16\x07\x00\x02\x00\x00\xff\xfe"
        sibling = self.root / ("._" + self.path.name)
        sibling.write_bytes(metadata)
        with mock.patch.object(doc, "read", wraps=doc.read) as read:
            result = self.remove()
        self.assertNotIn(sibling, [call.args[0] for call in read.call_args_list])
        self.assertTrue((Path(result["recovery_path"]) / self.path.name).is_file())
        self.assertTrue((Path(result["recovery_path"]) / self.folder.name / "S11.csv").is_file())

    def test_corrupt_real_sibling_is_named_and_nothing_moves(self):
        sibling = self.root / "Unfinished download.fb"
        sibling.write_bytes(b"\xff\xfe")
        with self.assertRaisesRegex(doc.DocumentConflict, "Unfinished download\\.fb"):
            self.remove()
        self.assertTrue(self.path.is_file())
        self.assertTrue(self.folder.is_dir())
        self.assertFalse((self.root / "Fairbeam Deleted").exists())

    def test_unreadable_real_sibling_is_named_and_nothing_moves(self):
        sibling = self.root / "Protected.fb"
        sibling.write_bytes(b"placeholder")
        read = doc.read
        def unreadable(path):
            if path == sibling:
                raise PermissionError("access denied")
            return read(path)
        with mock.patch.object(doc, "read", side_effect=unreadable):
            with self.assertRaisesRegex(doc.DocumentConflict, "Protected\\.fb"):
                self.remove()
        self.assertTrue(self.path.is_file())
        self.assertTrue(self.folder.is_dir())
        self.assertFalse((self.root / "Fairbeam Deleted").exists())

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
