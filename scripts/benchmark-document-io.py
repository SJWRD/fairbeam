"""Manual document Open/Save I/O benchmark using generated local data, never a solver.

Run from a Python environment with this repository's dependencies:
    python scripts/benchmark-document-io.py --trials 3 --output current.json
    python scripts/benchmark-document-io.py --trials 3 --legacy-result-reader --output legacy.json

Compare alternating runs when measuring timings. Results are local, cache-warm observations;
read counters describe logical Python I/O, not physical device reads. All workspaces and
advisory locks stay in an owned temporary directory. An optional JSON report must be new.
"""
import argparse
import json
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest import mock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--bundles", type=int, default=8)
    parser.add_argument("--padding-mib", type=int, default=8)
    parser.add_argument("--legacy-result-reader", action="store_true")
    args = parser.parse_args()
    for name, maximum in (("trials", 10), ("repeats", 20), ("bundles", 16), ("padding_mib", 16)):
        if not 1 <= getattr(args, name) <= maximum:
            parser.error(f"{name.replace('_', '-')} must be between 1 and {maximum}")
    if args.output and args.output.exists():
        parser.error("output already exists; choose a new report filename")
    workspace = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(workspace / "python"))
    from fairbeam import documents as doc, modelfiles, _filelocks
    from fairbeam.design import blank_design
    from fairbeam.document_workspace import DocumentWorkspace
    from fairbeam import document_workspace as workspace_module

    if args.legacy_result_reader:
        def legacy_result_bytes(path, document, reference):
            result = doc.read_result(path, document, reference)
            if result.get("model", {}).get("id") != document["design"]["model"]["id"]:
                raise doc.DocumentError("Result model identity does not match this document")
            raw = doc._read_bytes(path.parent / document["results_directory"] / reference["file"], doc.MAX_RESULT)
            if doc.digest(raw) != reference["sha256"]:
                raise doc.DocumentError("Result changed while it was being opened")
            return raw
        # This standalone benchmark process owns the imported module for its lifetime.
        workspace_module._result_bytes = legacy_result_bytes

    measurements = []
    unchanged_sidecars = True
    for trial in range(1, args.trials + 1):
        with tempfile.TemporaryDirectory(prefix="fairbeam-copy-benchmark-") as directory:
            with mock.patch.object(_filelocks, "_lock_directory", return_value=Path(directory) / "locks"):
                root = Path(directory).resolve()
                models, projects, history = (root / name for name in ("models", "projects", "history"))
                models.mkdir()
                projects.mkdir()
                design = blank_design("copy-benchmark", "Copy benchmark")
                modelfiles.create_design(models, "copy_benchmark", design)
                file = modelfiles.read_design_file(models, "copy_benchmark")
                manager = SimpleNamespace(input_path=lambda job: job.snapshot_path)
                bridge = DocumentWorkspace(models, projects, history, manager)
                snapshot = root / "source.design.json"
                source_bytes = json.dumps(design, ensure_ascii=False).encode("utf-8")
                snapshot.write_bytes(source_bytes)
                padding = "x" * (args.padding_mib * 1024 * 1024)
                for index in range(args.bundles):
                    name = f"result-{index}.json"
                    result = {"schema": "fairbeam.project/1", "model": design["model"],
                              "results": {"bands": [], "benchmark_index": index, "padding": padding}}
                    (projects / name).write_bytes(json.dumps(result, separators=(",", ":")).encode("utf-8"))
                    job = SimpleNamespace(status="done", bundle=name, kind="run", model=file["id"],
                                          input_snapshot={"sha256": doc.digest(source_bytes)},
                                          id=f"job-{index}", snapshot_path=str(snapshot))
                    assert bridge.record_completed(job)
                del padding, result

                def measured(operation, action):
                    counters = {"read_calls": 0, "read_bytes": 0, "hash_calls": 0, "hash_bytes": 0}
                    reader_counters = {"read_calls": 0, "read_bytes": 0, "hash_calls": 0, "hash_bytes": 0}
                    read, digest = doc._read_bytes, doc.digest
                    result_reader = workspace_module._result_bytes
                    in_reader, reader_calls, reader_seconds = False, 0, 0.0

                    def counted_read(*values, **options):
                        raw = read(*values, **options)
                        counters["read_calls"] += 1
                        counters["read_bytes"] += len(raw)
                        if in_reader:
                            reader_counters["read_calls"] += 1
                            reader_counters["read_bytes"] += len(raw)
                        return raw

                    def counted_digest(raw):
                        counters["hash_calls"] += 1
                        counters["hash_bytes"] += len(raw)
                        if in_reader:
                            reader_counters["hash_calls"] += 1
                            reader_counters["hash_bytes"] += len(raw)
                        return digest(raw)

                    def counted_result_reader(*values, **options):
                        nonlocal in_reader, reader_calls, reader_seconds
                        in_reader = True
                        reader_calls += 1
                        started = time.perf_counter()
                        try:
                            return result_reader(*values, **options)
                        finally:
                            reader_seconds += time.perf_counter() - started
                            in_reader = False

                    with mock.patch.object(doc, "_read_bytes", side_effect=counted_read), \
                            mock.patch.object(doc, "digest", side_effect=counted_digest), \
                            mock.patch.object(workspace_module, "_result_bytes", side_effect=counted_result_reader):
                        started = time.perf_counter()
                        response = action()
                        elapsed = time.perf_counter() - started
                    assert not response["warnings"], response["warnings"]
                    assert len(response["results"]) == args.bundles
                    measurements.append({"trial": trial, "operation": operation, "seconds": round(elapsed, 4),
                                         "read_calls": counters["read_calls"],
                                         "read_MiB": round(counters["read_bytes"] / 2 ** 20, 2),
                                         "hash_calls": counters["hash_calls"],
                                         "hash_MiB": round(counters["hash_bytes"] / 2 ** 20, 2),
                                         "result_reader_calls": reader_calls,
                                         "result_reader_seconds": round(reader_seconds, 4),
                                         "result_reader_read_calls": reader_counters["read_calls"],
                                         "result_reader_read_MiB": round(reader_counters["read_bytes"] / 2 ** 20, 2),
                                         "result_reader_hash_calls": reader_counters["hash_calls"],
                                         "result_reader_hash_MiB": round(reader_counters["hash_bytes"] / 2 ** 20, 2),
                                         "references": len(response["results"])})
                    return response

                path = root / "benchmark.fb"
                saved = measured("first save", lambda: bridge.save(path, file["id"], file["hash"]))
                folder = root / saved["document"]["results_directory"]
                before = {item.name: item.stat().st_mtime_ns for item in folder.iterdir()}
                for _ in range(args.repeats):
                    saved = measured("unchanged Save", lambda: bridge.save(
                        path, saved["file"]["id"], saved["file"]["hash"],
                        base_hash=saved["document"]["revision"], document_id=saved["document"]["id"]))
                after = {item.name: item.stat().st_mtime_ns for item in folder.iterdir()}
                unchanged_sidecars = unchanged_sidecars and before == after
                measured("bound Save As", lambda: bridge.save(
                    root / "copy.fb", saved["file"]["id"], saved["file"]["hash"],
                    document_id=saved["document"]["id"], save_as=True))
                # _result_bytes runs during Open; healthy cached Save paths do not use it.
                for _ in range(args.repeats):
                    measured("explicit Open", lambda: bridge.open(path))
    assert unchanged_sidecars
    summaries = {}
    for operation in dict.fromkeys(item["operation"] for item in measurements):
        seconds = [item["seconds"] for item in measurements if item["operation"] == operation]
        summaries[operation] = {"count": len(seconds), "median_seconds": round(statistics.median(seconds), 4),
                                "min_seconds": min(seconds), "max_seconds": max(seconds)}
    evidence = {"workspace": str(workspace), "python": platform.python_version(), "platform": platform.platform(),
                "result_reader": "legacy double read" if args.legacy_result_reader else "current",
                "fixture": f"{args.bundles} generated JSON bundles, each {args.padding_mib} MiB padding; no solver/network; local temporary directory",
                "unchanged_sidecar_mtimes": unchanged_sidecars, "measurements": measurements,
                "summary": summaries,
                "notes": "Counts cover successful documents._read_bytes calls and documents.digest calls during each measured operation. Setup is excluded; results are cache-warm and timings are local observations."}
    rendered = json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8", newline="\n") as report:
            report.write(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
