"""Windows stream refusal: actual owned files plus explicit enumeration failures."""
import ctypes
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fairbeam import documents as doc
from fairbeam.design import blank_design


@unittest.skipUnless(os.name == "nt", "Windows data streams")
class DocumentWindowsStreams(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / "Owned stream test.fb"
        self.document = doc.create(blank_design("stream-test", "Before"), "owned.results")
        self.revision = doc.save(self.path, self.document)

    def changed(self):
        self.document["design"]["model"]["name"] = "After"

    def stream(self):
        stream = Path(str(self.path) + ":fairbeam-test:$DATA")
        try:
            stream.write_bytes(b"Harmless owned test data; preserve this stream.\n")
        except OSError as exc:
            self.skipTest(f"Test volume does not support named streams: {exc}")
        return stream

    def snapshot(self):
        info = self.path.stat()
        return (self.path.read_bytes(), info.st_ino, info.st_size, info.st_mtime_ns,
                info.st_file_attributes, doc._windows_read_owner_group(self.path),
                doc._windows_read_dacl(self.path))

    def assert_no_stage(self):
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_plain_overwrite_succeeds_with_owner_group_and_dacl(self):
        before = self.snapshot()
        self.changed()
        check = doc._windows_require_unnamed_stream
        with mock.patch.object(doc, "_windows_require_unnamed_stream", wraps=check) as checked:
            new = doc.save(self.path, self.document, self.revision)
        self.assertEqual(checked.call_count, 2)
        self.assertNotEqual(new, self.revision)
        self.assertEqual(doc.read(self.path)[0], self.document)
        self.assertEqual(self.snapshot()[-2:], before[-2:])
        self.assert_no_stage()

    def test_named_stream_refuses_before_stage_and_save_as_keeps_original(self):
        stream = self.stream()
        before, stream_before = self.snapshot(), stream.read_bytes()
        self.changed()
        with mock.patch.object(doc.tempfile, "mkstemp", side_effect=AssertionError("must not stage")):
            with self.assertRaisesRegex(doc.DocumentError, "named data streams.*Save As"):
                doc.save(self.path, self.document, self.revision)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(stream.read_bytes(), stream_before)
        copy = self.root / "Separate copy.fb"
        doc.save(copy, self.document)
        self.assertEqual(doc.read(copy)[0], self.document)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(stream.read_bytes(), stream_before)
        self.assert_no_stage()

    def test_identical_save_does_not_replace_or_query_existing_stream(self):
        stream = self.stream()
        before, stream_before = self.snapshot(), stream.read_bytes()
        with mock.patch.object(doc, "_windows_require_unnamed_stream", side_effect=AssertionError("no replacement")):
            self.assertEqual(doc.save(self.path, self.document, self.revision), self.revision)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(stream.read_bytes(), stream_before)

    def test_first_query_errors_refuse_without_staging(self):
        before = self.snapshot()
        self.changed()
        for error in (5, 87, 38, 2):
            api = mock.Mock()
            def failed(*args):
                ctypes.set_last_error(error)
                return ctypes.c_void_p(-1).value
            api.FindFirstStreamW.side_effect = failed
            with self.subTest(error=error), mock.patch("ctypes.WinDLL", return_value=api), \
                    mock.patch.object(doc.tempfile, "mkstemp", side_effect=AssertionError("must not stage")):
                with self.assertRaisesRegex(doc.DocumentError, "Cannot inspect.*Save As") as caught:
                    doc.save(self.path, self.document, self.revision)
                self.assertEqual(caught.exception.__cause__.winerror, error)
            self.assertEqual(self.snapshot(), before)
            api.FindClose.assert_not_called()
            self.assert_no_stage()

    def test_next_query_error_refuses_and_closes_enumerator(self):
        before = self.snapshot()
        self.changed()
        api = mock.Mock()
        def first(path, level, pointer, flags):
            pointer._obj.name = "::$DATA"
            return 123
        def failed(*args):
            ctypes.set_last_error(5)
            return False
        api.FindFirstStreamW.side_effect = first
        api.FindNextStreamW.side_effect = failed
        api.FindClose.return_value = True
        with mock.patch("ctypes.WinDLL", return_value=api):
            with self.assertRaisesRegex(doc.DocumentError, "Cannot inspect.*Save As"):
                doc.save(self.path, self.document, self.revision)
        api.FindClose.assert_called_once_with(123)
        self.assertEqual(self.snapshot(), before)
        self.assert_no_stage()

    def test_stream_added_during_staging_refuses_and_cleans_stage(self):
        before = self.snapshot()
        self.changed()
        real_fsync = doc.os.fsync
        stream = Path(str(self.path) + ":fairbeam-test:$DATA")
        def add_stream(fd):
            real_fsync(fd)
            stream.write_bytes(b"Added after initial check")
        with mock.patch.object(doc.os, "fsync", side_effect=add_stream):
            with self.assertRaisesRegex(doc.DocumentError, "named data streams.*Save As"):
                doc.save(self.path, self.document, self.revision)
        # Adding an ADS can change the parent file's timestamps; compare content,
        # identity, attributes, and security, then verify the new stream itself.
        after = self.snapshot()
        self.assertEqual(after[:3] + after[4:], before[:3] + before[4:])
        self.assertEqual(stream.read_bytes(), b"Added after initial check")
        self.assert_no_stage()

    def test_second_query_failure_keeps_original_and_cleans_stage(self):
        before = self.snapshot()
        self.changed()
        with mock.patch.object(doc, "_windows_require_unnamed_stream", side_effect=[None, doc.DocumentError("Cannot inspect")]):
            with self.assertRaisesRegex(doc.DocumentError, "Cannot inspect"):
                doc.save(self.path, self.document, self.revision)
        self.assertEqual(self.snapshot(), before)
        self.assert_no_stage()

    def test_busy_replacement_rechecks_streams_before_retry(self):
        before = self.snapshot()
        self.changed()
        stream = Path(str(self.path) + ":fairbeam-test:$DATA")
        def busy(*args):
            stream.write_bytes(b"Added while replacement was busy")
            raise PermissionError("busy destination")
        with mock.patch.object(doc.os, "replace", side_effect=busy) as replaced:
            with self.assertRaisesRegex(doc.DocumentError, "named data streams.*Save As"):
                doc.save(self.path, self.document, self.revision)
        self.assertEqual(replaced.call_count, 1)
        after = self.snapshot()
        self.assertEqual(after[:3] + after[4:], before[:3] + before[4:])
        self.assertEqual(stream.read_bytes(), b"Added while replacement was busy")
        self.assert_no_stage()

    def test_readonly_original_stays_unchanged(self):
        self.path.chmod(stat.S_IREAD)
        self.addCleanup(self.path.chmod, stat.S_IREAD | stat.S_IWRITE)
        before = self.snapshot()
        self.changed()
        with self.assertRaises(PermissionError):
            doc.save(self.path, self.document, self.revision)
        self.assertEqual(self.snapshot(), before)
        self.assert_no_stage()


if __name__ == "__main__":
    unittest.main()
