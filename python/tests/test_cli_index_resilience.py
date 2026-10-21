"""Foreign workspace files cannot turn a successfully written result into a failed run."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from fairbeam import cli, preview


class IndexResilienceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = {"schema": "fairbeam.project/1", "name": "Good result",
                       "model": {"id": "good"}, "mesh": {"total_cells": 12},
                       "results": {"bands": []}}
        (self.root / "good.json").write_text(json.dumps(self.bundle), encoding="utf-8")
        cli._INDEX_CACHE.clear()

    def test_foreign_malformed_and_appledouble_files_do_not_break_index(self):
        foreign = {"list.json": b"[1, 2]", "null.json": b"null", "broken.json": b"{",
                   "utf16.json": '{"notes":1}'.encode("utf-16"), "._good.json": b"\x00\x05\x16\x07\xff\xfe"}
        malformed = [{"model": []}, {"mesh": []}, {"results": []}, {"results": {"bands": [None]}},
                     {"results": {"bands": [{"f_center": "wrong"}]}}, {"name": []},
                     {"run": {"engine": "gpu", "host": 1}}]
        for i, change in enumerate(malformed):
            foreign[f"bad-{i}.json"] = json.dumps({**self.bundle, **change}).encode()
        for name, raw in foreign.items():
            (self.root / name).write_bytes(raw)
        with self.assertLogs("fairbeam.cli", level="WARNING"):
            cli.rebuild_index(self.root)
        index = json.loads((self.root / "index.json").read_text("utf-8"))
        self.assertEqual([item["file"] for item in index["projects"]], ["good.json"])
        for name, raw in foreign.items():
            self.assertEqual((self.root / name).read_bytes(), raw)

    def test_failed_index_publication_does_not_fail_committed_bundle(self):
        with mock.patch.object(cli, "rebuild_index", side_effect=OSError("index busy")):
            with self.assertLogs("fairbeam.cli", level="WARNING"):
                path = cli._write(self.bundle, self.root, "new-result")
        self.assertEqual(json.loads(path.read_text("utf-8")), self.bundle)

    def test_integer_beyond_float_range_is_not_a_finite_index_number(self):
        self.assertFalse(cli._finite(10 ** 1000))
        self.assertTrue(cli._finite(42))
        self.assertTrue(cli._finite(2.45e9))

    def test_nonfinite_sibling_entries_cannot_poison_standard_json_index(self):
        malformed = [
            {"mesh": {"total_cells": float("inf")}},
            {"mesh": {"total_cells": 10 ** 1000}},
            {"model": {"id": "bad", "params": [{"key": "width", "value": float("nan")}]}},
            {"model": {"id": "bad", "params": [{"key": "width", "value": 10 ** 1000}]}},
            {"run": {"speed_mcells_s": float("inf")}},
            {"results": {"bands": [{"f_center": 10 ** 1000}]}},
            {"model": {"id": "bad", "params": [{"key": "width", "value": 10 ** 1000, "default": 1}]}},
        ]
        originals = {}
        for i, change in enumerate(malformed):
            path = self.root / f"bad-number-{i}.json"
            path.write_text(json.dumps({**self.bundle, **change}), encoding="utf-8")
            originals[path] = path.read_bytes()
        with self.assertLogs("fairbeam.cli", level="WARNING"):
            cli.rebuild_index(self.root)

        def reject_constant(value):
            self.fail(f"index contains the non-standard JSON token {value}")

        index = json.loads((self.root / "index.json").read_text("utf-8"), parse_constant=reject_constant)
        self.assertEqual([item["file"] for item in index["projects"]], ["good.json"])
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)

    def test_nonfinite_unused_result_samples_do_not_hide_a_valid_index_entry(self):
        bundle = {**self.bundle, "results": {"bands": [], "unused_samples": [float("nan")]}}
        (self.root / "good.json").write_text(json.dumps(bundle), encoding="utf-8")
        cli.rebuild_index(self.root)
        entries = json.loads((self.root / "index.json").read_text("utf-8"))["projects"]
        self.assertEqual([entry["file"] for entry in entries], ["good.json"])

    def test_index_overflow_does_not_fail_committed_bundle(self):
        with mock.patch.object(cli, "rebuild_index", side_effect=OverflowError("invalid index number")):
            with self.assertLogs("fairbeam.cli", level="WARNING"):
                path = cli._write(self.bundle, self.root, "new-result")
        self.assertEqual(json.loads(path.read_text("utf-8")), self.bundle)

    def test_linked_index_inputs_are_skipped_without_reading_the_target(self):
        alias = self.root / "linked.json"
        try:
            alias.symlink_to(self.root / "good.json")
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertLogs("fairbeam.cli", level="WARNING"):
            cli.rebuild_index(self.root)
        entries = json.loads((self.root / "index.json").read_text("utf-8"))["projects"]
        self.assertEqual([entry["file"] for entry in entries], ["good.json"])
        self.assertTrue(alias.is_symlink())

    def test_appledouble_model_metadata_is_not_listed_as_broken_models(self):
        names = ("actual.py", "actual.design.json", "._actual.py", "._actual.design.json", "_helper.py")
        for name in names:
            (self.root / name).write_bytes(b"\x00\x05\x16\x07\xff\xfe" if name.startswith("._") else b"")
        self.assertEqual([path.name for path in preview.model_files(self.root)],
                         ["actual.design.json", "actual.py"])

    def test_concurrent_index_writers_use_distinct_temporary_files(self):
        replace = os.replace
        rendezvous = threading.Barrier(2)
        names = []
        lock = threading.Lock()
        def concurrent_replace(source, target):
            if Path(target) == self.root / "index.json":
                with lock:
                    first_attempt = Path(source).name not in names
                    if first_attempt:
                        names.append(Path(source).name)
                if first_attempt:
                    rendezvous.wait(timeout=5)
            return replace(source, target)
        with mock.patch.object(cli.os, "replace", side_effect=concurrent_replace):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(cli.rebuild_index, self.root) for _ in range(2)]
                for future in futures:
                    future.result(timeout=10)
        self.assertEqual(len(set(names)), 2)
        self.assertEqual(len(json.loads((self.root / "index.json").read_text("utf-8"))["projects"]), 1)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_failed_index_replace_preserves_previous_index_and_cleans_temporary(self):
        cli.rebuild_index(self.root)
        before = (self.root / "index.json").read_bytes()
        with mock.patch.object(cli.os, "replace", side_effect=OSError("index busy")):
            with self.assertRaisesRegex(OSError, "index busy"):
                cli.rebuild_index(self.root)
        self.assertEqual((self.root / "index.json").read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
