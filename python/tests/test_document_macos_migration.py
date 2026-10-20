"""Legacy portable result migration, including Finder metadata on native macOS."""
import json
import plistlib
import subprocess
import sys
import unittest

from fairbeam import documents as doc
import test_document_workspace as fixtures


class PortableResultMigrationTests(unittest.TestCase):
    def exercise_migration(self, finder=False):
        case = fixtures.DocumentWorkspaceTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        old_ref, old_bytes = case.sidecar(file="legacy-run.json")
        case.write_document()
        root = case.root / case.document["results_directory"]
        old_folder = root / "runs" / "legacy-v1"
        old_folder.mkdir(parents=True)
        sentinel = old_folder / "metadata.json"
        sentinel.write_bytes(b'{"legacy":true}')
        attrs = {}
        if finder:
            attrs = {"com.apple.lastuseddate#PS": b"1234567890123456",
                     "com.apple.metadata:_kMDItemUserTags": plistlib.dumps(["Fairbeam\n6"], fmt=plistlib.FMT_BINARY)}
            for name, value in attrs.items():
                subprocess.run(["xattr", "-wx", name, value.hex(), str(case.path)], check=True)
        opened = case.bridge.open(case.path)
        # Model a completion for the saved workspace; no user draft Save is involved.
        job = case.completed(opened["file"], job_id="new-v2-run")
        case.bridge.record_completed(job)
        saved = case.save_opened(opened)
        self.assertFalse(saved["warnings"], saved["warnings"])
        document, _ = doc.read(case.path)
        self.assertEqual(len(document["results"]), 2)
        self.assertIn(old_ref, document["results"])
        self.assertEqual((root / "legacy-run.json").read_bytes(), old_bytes)
        self.assertEqual(sentinel.read_bytes(), b'{"legacy":true}')
        for ref in document["results"]:
            folder = root / "runs" / ("run-" + ref["sha256"] + "-" + ref["design_sha256"][:16] + "-v2")
            meta = json.loads((folder / "metadata" / "run.json").read_text())
            bundle = (folder / "metadata" / meta["bundle_file"]).resolve()
            self.assertEqual(doc.digest(bundle.read_bytes()), ref["sha256"])
        for name, value in attrs.items():
            # Python's os.*xattr API is Linux-only; use macOS's native tool.
            result = subprocess.run(
                ["xattr", "-px", name, str(case.path)],
                check=True, capture_output=True, text=True,
            )
            self.assertEqual(bytes.fromhex(result.stdout), value)
        self.assertEqual(len(case.bridge.open(case.path)["results"]), 2)

    def test_v1_results_survive_completion_and_v2_export(self):
        self.exercise_migration()

    @unittest.skipUnless(sys.platform == "darwin", "native Finder metadata")
    def test_finder_opened_v1_document_accepts_results_without_losing_xattrs(self):
        self.exercise_migration(finder=True)
