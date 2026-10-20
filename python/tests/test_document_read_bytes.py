"""Bounded document reads preserve complete-byte and opened-file checks."""
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from fairbeam import documents as doc


class DocumentReadBytesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / "result.json"
        self.requests = []
        self.streams = []
        self.fdopen = doc.os.fdopen

    def tracked_fdopen(self, *args, **kwargs):
        stream = self.fdopen(*args, **kwargs)
        self.streams.append(stream)
        requests = self.requests

        class Tracked:
            def __enter__(self):
                stream.__enter__()
                return self

            def __exit__(self, *details):
                return stream.__exit__(*details)

            def fileno(self):
                return stream.fileno()

            def read(self, size):
                requests.append(size)
                return stream.read(size)

        return Tracked()

    def test_small_file_requests_its_size_plus_one_and_returns_exact_bytes(self):
        raw = b'\xef\xbb\xbf{"value":"\xc4\xb0zmir"}\r\n'
        self.path.write_bytes(raw)
        with mock.patch.object(doc.os, "fdopen", side_effect=self.tracked_fdopen):
            self.assertEqual(doc._read_bytes(self.path, doc.MAX_RESULT), raw)
        self.assertEqual(self.requests, [len(raw) + 1])
        self.assertTrue(self.streams[0].closed)

    def test_exact_limit_is_accepted(self):
        raw = b"x" * 8192
        self.path.write_bytes(raw)
        self.assertEqual(doc._read_bytes(self.path, len(raw)), raw)

    def test_empty_file_still_reads_one_byte(self):
        self.path.write_bytes(b"")
        with mock.patch.object(doc.os, "fdopen", side_effect=self.tracked_fdopen):
            self.assertEqual(doc._read_bytes(self.path, 0), b"")
        self.assertEqual(self.requests, [1])

    def test_oversized_open_file_is_rejected_before_read(self):
        self.path.write_bytes(b"12345")
        with mock.patch.object(doc.os, "fdopen", side_effect=self.tracked_fdopen):
            with self.assertRaisesRegex(doc.DocumentError, "size limit"):
                doc._read_bytes(self.path, 4)
        self.assertEqual(self.requests, [])
        self.assertTrue(self.streams[0].closed)

    def test_fstat_oversize_is_rejected_before_allocation_even_if_path_shrank(self):
        self.path.write_bytes(b"{}")
        info = SimpleNamespace(st_mode=stat.S_IFREG, st_size=doc.MAX_RESULT + 1)
        with mock.patch.object(doc.os, "fstat", return_value=info), \
                mock.patch.object(doc.os, "fdopen", side_effect=self.tracked_fdopen):
            with self.assertRaisesRegex(doc.DocumentError, "size limit"):
                doc._read_bytes(self.path, doc.MAX_RESULT)
        self.assertEqual(self.requests, [])
        self.assertTrue(self.streams[0].closed)

    def test_negative_reported_size_cannot_trigger_unbounded_read(self):
        self.path.write_bytes(b"{}")
        info = SimpleNamespace(st_mode=stat.S_IFREG, st_size=-2)
        with mock.patch.object(doc.os, "fstat", return_value=info), \
                mock.patch.object(doc.os, "fdopen", side_effect=self.tracked_fdopen):
            with self.assertRaisesRegex(doc.DocumentError, "invalid size"):
                doc._read_bytes(self.path, doc.MAX_RESULT)
        self.assertEqual(self.requests, [])

    def test_shrunk_file_is_rejected_even_when_actual_bytes_are_valid_json(self):
        self.path.write_bytes(b"{}")
        info = SimpleNamespace(st_mode=stat.S_IFREG, st_size=8)
        with mock.patch.object(doc.os, "fstat", return_value=info), \
                mock.patch.object(doc.os, "fdopen", side_effect=self.tracked_fdopen):
            with self.assertRaisesRegex(doc.DocumentError, "changed size"):
                doc._read_bytes(self.path, doc.MAX_RESULT)
        self.assertEqual(self.requests, [9])
        self.assertTrue(self.streams[0].closed)

    def test_grown_file_is_rejected_after_reading_only_one_extra_byte(self):
        self.path.write_bytes(b"{}" + b"x" * 8192)
        info = SimpleNamespace(st_mode=stat.S_IFREG, st_size=2)
        with mock.patch.object(doc.os, "fstat", return_value=info), \
                mock.patch.object(doc.os, "fdopen", side_effect=self.tracked_fdopen):
            with self.assertRaisesRegex(doc.DocumentError, "changed size"):
                doc._read_bytes(self.path, doc.MAX_RESULT)
        self.assertEqual(self.requests, [3])

    def test_zero_size_then_added_content_is_rejected(self):
        self.path.write_bytes(b"{}")
        info = SimpleNamespace(st_mode=stat.S_IFREG, st_size=0)
        with mock.patch.object(doc.os, "fstat", return_value=info), \
                mock.patch.object(doc.os, "fdopen", side_effect=self.tracked_fdopen):
            with self.assertRaisesRegex(doc.DocumentError, "changed size"):
                doc._read_bytes(self.path, doc.MAX_RESULT)
        self.assertEqual(self.requests, [1])

    def test_real_file_growth_after_opened_fstat_is_rejected(self):
        self.path.write_bytes(b"{}")
        fstat = doc.os.fstat

        def grow(fd):
            info = fstat(fd)
            with self.path.open("ab") as writer:
                writer.write(b" ")
            return info

        with mock.patch.object(doc.os, "fstat", side_effect=grow):
            with self.assertRaisesRegex(doc.DocumentError, "changed size"):
                doc._read_bytes(self.path, doc.MAX_RESULT)
        self.assertEqual(self.path.read_bytes(), b"{} ")

    def test_real_file_shrink_after_opened_fstat_is_rejected(self):
        self.path.write_bytes(b"{}  ")
        fstat = doc.os.fstat

        def shrink(fd):
            info = fstat(fd)
            with self.path.open("r+b") as writer:
                writer.truncate(2)
            return info

        with mock.patch.object(doc.os, "fstat", side_effect=shrink):
            with self.assertRaisesRegex(doc.DocumentError, "changed size"):
                doc._read_bytes(self.path, doc.MAX_RESULT)
        self.assertEqual(self.path.read_bytes(), b"{}")

    def test_growth_beyond_limit_remains_an_oversize_error(self):
        self.path.write_bytes(b"12345")
        info = SimpleNamespace(st_mode=stat.S_IFREG, st_size=4)
        with mock.patch.object(doc.os, "fstat", return_value=info):
            with self.assertRaisesRegex(doc.DocumentError, "size limit"):
                doc._read_bytes(self.path, 4)

    def test_opened_descriptor_must_still_be_regular(self):
        self.path.write_bytes(b"{}")
        info = SimpleNamespace(st_mode=stat.S_IFDIR, st_size=2)
        with mock.patch.object(doc.os, "fstat", return_value=info), \
                mock.patch.object(doc.os, "fdopen", side_effect=self.tracked_fdopen):
            with self.assertRaisesRegex(doc.DocumentError, "regular file"):
                doc._read_bytes(self.path, doc.MAX_RESULT)
        self.assertEqual(self.requests, [])
        self.assertTrue(self.streams[0].closed)

    def test_path_directory_is_rejected_before_open(self):
        with mock.patch.object(doc.os, "open", side_effect=AssertionError("must not open directory")):
            with self.assertRaisesRegex(doc.DocumentError, "regular files"):
                doc._read_bytes(self.root, doc.MAX_RESULT)


if __name__ == "__main__":
    unittest.main()
