"""Job metadata commits under overlapping completion/study updates; no solver."""

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fairbeam.jobs import Job


class JobPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job = Job(self.root, id="job", model="patch", label="before")
        self.job.save()

    def start(self, fn, name):
        errors = []
        done = threading.Event()
        def run():
            try:
                fn()
            except BaseException as error:
                errors.append(error)
            finally:
                done.set()
        thread = threading.Thread(target=run, name=name, daemon=True)
        thread.start()
        return thread, done, errors

    def join_all(self, *tasks):
        for thread, _, _ in tasks:
            thread.join(5)
        self.assertFalse(any(thread.is_alive() for thread, _, _ in tasks), "save must not deadlock")
        self.assertEqual([error for _, _, errors in tasks for error in errors], [])

    def read(self):
        return json.loads((self.job.dir / "job.json").read_text(encoding="utf-8"))

    def test_overlapping_saves_do_not_commit_an_older_snapshot_last(self):
        captured, release, second_started = threading.Event(), threading.Event(), threading.Event()
        to_dict = self.job.to_dict
        def snapshot(*args, **kwargs):
            data = to_dict(*args, **kwargs)
            if threading.current_thread().name == "first-save":
                captured.set()
                if not release.wait(5):
                    raise TimeoutError("first snapshot barrier")
            return data
        def second_save():
            self.job.label = "latest"
            second_started.set()
            self.job.save()
        tasks = []
        with mock.patch.object(self.job, "to_dict", side_effect=snapshot):
            try:
                tasks.append(self.start(self.job.save, "first-save"))
                self.assertTrue(captured.wait(5))
                tasks.append(self.start(second_save, "second-save"))
                self.assertTrue(second_started.wait(5))
                # The old implementation deterministically lets this save commit
                # while the first has a stale snapshot waiting to overwrite it.
                second_committed_early = tasks[1][1].wait(0.2)
            finally:
                release.set()
                self.join_all(*tasks)
        self.assertFalse(second_committed_early, "a later save must wait for the earlier commit")
        self.assertEqual(self.read()["label"], "latest")

    def test_separately_loaded_jobs_never_share_a_temporary_file(self):
        other = Job.load(self.root, self.job.dir)
        self.job.label, other.label = "first", "second"
        first_replace, second_replaced = threading.Event(), threading.Event()
        sources = []
        replace = os.replace
        def interleaved_replace(source, target):
            sources.append(Path(source))
            if threading.current_thread().name == "first-save":
                first_replace.set()
                if not second_replaced.wait(5):
                    raise TimeoutError("second replacement barrier")
                return replace(source, target)
            result = replace(source, target)
            second_replaced.set()
            return result
        tasks = []
        with mock.patch("os.replace", side_effect=interleaved_replace):
            try:
                tasks.append(self.start(self.job.save, "first-save"))
                self.assertTrue(first_replace.wait(5))
                tasks.append(self.start(other.save, "second-save"))
            finally:
                self.join_all(*tasks)
        self.assertEqual(len(set(sources)), 2)
        self.assertEqual(self.read()["label"], "first")
        self.assertEqual(list(self.job.dir.glob("*.tmp")), [])

    def test_save_does_not_wait_for_the_job_state_lock(self):
        # _finish owns job.lock before study callbacks; a callback saving a
        # different job must not invert that ordering by acquiring its job.lock.
        with self.job.lock:
            task = self.start(self.job.save, "callback-save")
            completed = task[1].wait(2)
        self.join_all(task)
        self.assertTrue(completed, "persistence must not acquire the job state lock")

    def test_failed_replace_preserves_metadata_and_removes_temporary_file(self):
        before = (self.job.dir / "job.json").read_bytes()
        self.job.label = "not committed"
        with mock.patch("os.replace", side_effect=OSError("write failed")):
            with self.assertRaisesRegex(OSError, "write failed"):
                self.job.save()
        self.assertEqual((self.job.dir / "job.json").read_bytes(), before)
        self.assertEqual(list(self.job.dir.glob("*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "Windows sharing violations are retried")
    def test_transient_windows_sharing_failure_retries_the_same_commit(self):
        self.job.label = "after sharing violation"
        replace = os.replace
        calls = []
        def busy_then_replace(source, target):
            calls.append(Path(source))
            if len(calls) < 3:
                raise PermissionError("temporary sharing violation")
            return replace(source, target)
        with mock.patch("os.replace", side_effect=busy_then_replace):
            self.job.save()
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(set(calls)), 1)
        self.assertEqual(self.read()["label"], "after sharing violation")
        self.assertEqual(list(self.job.dir.glob("*.tmp")), [])

    def test_nonfinite_metadata_does_not_replace_the_previous_record(self):
        before = (self.job.dir / "job.json").read_bytes()
        self.job.stats = {"invalid": float("nan")}
        with self.assertRaises(ValueError):
            self.job.save()
        self.assertEqual((self.job.dir / "job.json").read_bytes(), before)
        self.assertEqual(list(self.job.dir.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
