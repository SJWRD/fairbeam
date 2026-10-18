"""Sidecar publication shares the document's atomic no-replace platform contract."""
import errno
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fairbeam import document_workspace as workspace, documents as doc


class ResultPublication(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "result.json"
        self.data = b'{"schema":"fairbeam.project/1"}'

    def test_macos_sidecar_uses_exclusive_rename_without_hardlinks(self):
        def publish(source, destination):
            self.assertFalse(destination.exists())
            self.assertEqual(source.read_bytes(), self.data)
            os.rename(source, destination)

        with mock.patch.object(doc.sys, "platform", "darwin"), \
                mock.patch.object(doc, "_macos_rename_exclusive", side_effect=publish) as rename, \
                mock.patch.object(doc.os, "link", side_effect=AssertionError("hardlinks unavailable")):
            workspace._immutable(self.path, self.data)
        rename.assert_called_once()
        self.assertEqual(self.path.read_bytes(), self.data)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_unsupported_volume_leaves_no_partial_sidecar(self):
        with mock.patch.object(doc.sys, "platform", "darwin"), \
                mock.patch.object(doc, "_macos_rename_exclusive", side_effect=OSError(errno.ENOTSUP, "unsupported")), \
                mock.patch.object(doc.os, "link", side_effect=OSError(errno.ENOTSUP, "unsupported")):
            with self.assertRaisesRegex(doc.DocumentError, "atomic file creation"):
                workspace._immutable(self.path, self.data)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_competing_publication_accepts_only_identical_complete_bytes(self):
        for competing in (self.data, b"other result"):
            with self.subTest(competing=competing):
                def publish(source, destination):
                    destination.write_bytes(competing)
                    raise FileExistsError(errno.EEXIST, "already published")

                with mock.patch.object(doc, "_publish_new", side_effect=publish):
                    if competing == self.data:
                        workspace._immutable(self.path, self.data)
                    else:
                        with self.assertRaises(doc.DocumentConflict):
                            workspace._immutable(self.path, self.data)
                self.assertEqual(self.path.read_bytes(), competing)
                self.assertEqual(list(self.root.iterdir()), [self.path])
                self.path.unlink()

    def test_existing_matching_result_does_not_rewrite_or_publish(self):
        self.path.write_bytes(self.data)
        with mock.patch.object(doc, "_publish_new", side_effect=AssertionError("unnecessary publication")):
            workspace._immutable(self.path, self.data)
        self.assertEqual(self.path.read_bytes(), self.data)


if __name__ == "__main__":
    unittest.main()
