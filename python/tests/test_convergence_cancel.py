"""Stop at the completion/admission boundaries, with real jobs and no solver process."""
import json
from pathlib import Path
import tempfile
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fairbeam import convergence as cv
from fairbeam.jobs import JobManager
from fairbeam.server import Handler
from test_convergence import fake_bundle


class ConvergenceCancellationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.manager = JobManager(self.root / "jobs", self.root, autostart=False)
        self.studies = cv.ServerStudies(self.manager, self.root)
        self.manager.on_finished = self.studies.on_finished

    def start(self, study_id="study"):
        return self.studies.start(study_id=study_id, name="Mesh", model={"id": "patch"},
            job_kw={"model": "patch", "model_path": "patch.design.json", "threads": 1},
            planned=[15, 20, 30], tol=dict(cv.DEFAULT_TOL), settings={})[1]

    def result(self, job):
        job.bundle = f"{job.id}.json"
        (self.root / job.bundle).write_text(json.dumps(fake_bundle(2.4e9)), encoding="utf-8")

    def stop_via_handler(self, study_id="study"):
        responses = []
        handler = SimpleNamespace(app=SimpleNamespace(manager=self.manager, studies=self.studies),
                                  _body=lambda: {}, _json=lambda code, data: responses.append((code, data)))
        Handler._cancel_sweep(handler, study_id)
        self.assertEqual(responses[0][0], 202)
        return responses[0][1]["runs"]

    def thread(self, fn):
        errors = []
        def run():
            try:
                fn()
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, errors

    def join(self, task):
        thread, errors = task
        thread.join(5)
        self.assertFalse(thread.is_alive(), "Stop/completion must not deadlock")
        if errors:
            raise errors[0]

    def test_stop_after_terminal_status_before_finished_hook_does_not_admit_next_density(self):
        first = self.start()
        self.result(first)
        terminal, resume = threading.Event(), threading.Event()
        def finished(job):
            terminal.set()
            if not resume.wait(5):
                raise TimeoutError("test completion barrier")
            self.studies.on_finished(job)
        self.manager.on_finished = finished
        task = self.thread(lambda: self.manager._finish(first, "done", error=None))
        try:
            self.assertTrue(terminal.wait(5))
            self.assertTrue(first.terminal)
            self.stop_via_handler()
        finally:
            resume.set()
            self.join(task)
        self.assertEqual(len(self.manager.jobs), 1, "Stop must cover jobs a delayed hook would enqueue")
        study = self.studies.get("study")
        self.assertEqual(study["convergence"]["reason"], "cancelled")
        self.assertIsNone(study["convergence"]["next"])
        self.assertEqual(study["members"][0]["status"], "done", "keep the completed result")
        self.assertEqual(study["members"][0]["file"], first.bundle)
        self.assertTrue(self.studies.load("study")["stop_requested"])
        self.assertEqual(first.sweep["reason"], "cancelled")

    def test_stop_is_seen_while_result_metrics_hold_the_study_lock(self):
        first = self.start()
        self.result(first)
        reading, resume, requested = threading.Event(), threading.Event(), threading.Event()
        class ObservedSet(set):
            def add(self, value):
                super().add(value)
                requested.set()
        self.studies._stop_requested = ObservedSet()
        original = cv.metrics
        def metrics(bundle):
            reading.set()
            if not resume.wait(5):
                raise TimeoutError("test metrics barrier")
            return original(bundle)
        with mock.patch.object(cv, "metrics", side_effect=metrics):
            completing = self.thread(lambda: self.manager._finish(first, "done", error=None))
            stopping = None
            try:
                self.assertTrue(reading.wait(5))
                stopping = self.thread(self.stop_via_handler)
                self.assertTrue(requested.wait(5))
            finally:
                resume.set()
                self.join(completing)
                if stopping:
                    self.join(stopping)
        self.assertEqual(len(self.manager.jobs), 1)
        self.assertEqual(self.studies.get("study")["convergence"]["reason"], "cancelled")

    def test_submission_already_admitted_is_included_in_cancellation(self):
        first = self.start()
        self.result(first)
        admitted, resume, stopping = threading.Event(), threading.Event(), threading.Event()
        submit = self.manager.submit
        def slow_submit(**kwargs):
            admitted.set()
            if not resume.wait(5):
                raise TimeoutError("test admission barrier")
            return submit(**kwargs)
        def stop():
            stopping.set()
            self.stop_via_handler()
        with mock.patch.object(self.manager, "submit", side_effect=slow_submit):
            completing = self.thread(lambda: self.manager._finish(first, "done", error=None))
            cancelling = None
            try:
                self.assertTrue(admitted.wait(5))
                cancelling = self.thread(stop)
                self.assertTrue(stopping.wait(5))
            finally:
                resume.set()
                self.join(completing)
                if cancelling:
                    self.join(cancelling)
        self.assertEqual(len(self.manager.jobs), 2)
        self.assertEqual([j.status for j in self.manager.jobs.values()], ["done", "cancelled"])
        self.assertEqual(self.studies.get("study")["convergence"]["reason"], "cancelled")

    def test_stop_is_idempotent_persisted_and_does_not_cancel_another_study(self):
        first = self.start()
        other = self.start("other")
        self.stop_via_handler()
        self.stop_via_handler()
        self.assertEqual(first.status, "cancelled")
        self.assertEqual(other.status, "queued")
        reloaded = cv.ServerStudies(self.manager, self.root)
        reloaded.on_finished(first)
        self.assertEqual(len(self.manager.jobs), 2)
        self.assertEqual(reloaded.get("study")["convergence"]["reason"], "cancelled")
        self.assertEqual(len(reloaded.get("study")["members"]), 1)
        self.assertEqual(self.studies._stop_requested, set())
        self.assertEqual(self.studies.cancel("unknown"), [])
        self.assertEqual(self.studies._stop_requested, set())

    def test_completed_verdict_and_ordinary_sweep_are_unchanged(self):
        first = self.start()
        self.result(first)
        self.manager._finish(first, "done", error=None)
        second = list(self.manager.jobs.values())[-1]
        self.result(second)
        self.manager._finish(second, "done", error=None)
        before = self.studies.load("study")
        self.assertEqual(before["convergence"]["reason"], "converged")
        self.stop_via_handler()
        self.assertEqual(self.studies.load("study"), before)
        job = self.manager.submit(model="plain", model_path="plain.py", sweep={"id": "ordinary"})
        self.stop_via_handler("ordinary")
        self.assertEqual(job.status, "cancelled")

    def test_unreadable_or_malformed_study_does_not_block_stopping_existing_jobs(self):
        for index, data in enumerate(("[1, 2]", "null", "{}", "{not json}")):
            with self.subTest(data=data):
                study_id = f"malformed-{index}"
                first = self.start(study_id)
                self.studies.path(study_id).write_text(data, encoding="utf-8")
                self.stop_via_handler(study_id)
                self.assertEqual(first.status, "cancelled")
                self.assertIn(study_id, self.studies._stop_requested)

    def test_write_failure_still_stops_queued_jobs_and_retains_the_stop(self):
        first = self.start()
        with mock.patch.object(cv, "write_study", side_effect=PermissionError("read-only study folder")):
            with self.assertLogs("fairbeam.convergence", level="WARNING"):
                self.stop_via_handler()
        self.assertEqual(first.status, "cancelled")
        self.assertTrue(first.cancel_requested)
        self.assertIn("study", self.studies._stop_requested)
        self.assertEqual(len(self.manager.jobs), 1)
        # Retrying once the folder is writable persists the intent and clears memory.
        self.stop_via_handler()
        self.assertTrue(self.studies.load("study")["stop_requested"])
        self.assertNotIn("study", self.studies._stop_requested)

    def test_write_failure_cannot_reenable_a_delayed_completion_hook(self):
        first = self.start()
        self.result(first)
        self.manager.on_finished = None
        self.manager._finish(first, "done", error=None)
        with mock.patch.object(cv, "write_study", side_effect=OSError("disk full")):
            with self.assertLogs("fairbeam.convergence", level="WARNING"):
                self.stop_via_handler()
        self.studies.on_finished(first)
        self.assertEqual(len(self.manager.jobs), 1)
        self.assertEqual(self.studies.load("study")["convergence"]["reason"], "cancelled")
        self.assertEqual(self.studies.load("study")["members"][0]["file"], first.bundle)

    def test_transient_read_failure_cannot_reenable_a_delayed_completion_hook(self):
        first = self.start()
        self.result(first)
        self.manager.on_finished = None
        self.manager._finish(first, "done", error=None)
        with mock.patch.object(self.studies, "load", return_value=None):
            self.stop_via_handler()
        self.studies.on_finished(first)
        self.assertEqual(len(self.manager.jobs), 1)
        self.assertEqual(self.studies.load("study")["convergence"]["reason"], "cancelled")


if __name__ == "__main__":
    unittest.main()
