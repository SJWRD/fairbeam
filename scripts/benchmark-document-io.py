"""Manual document Open/Save benchmark; generated local data, no solver or network.

    python scripts/benchmark-document-io.py --trials 3 --output current.json
    python scripts/benchmark-document-io.py --legacy-result-reader --output double-read.json
    python scripts/benchmark-document-io.py --padding-kib 4 --bundles 500 --per-reference-manifest --output per-reference.json

Compare alternating runs with identical fixture arguments. Baselines isolate one extra cost:
--legacy-result-reader reads/hashes each result twice; --per-reference-manifest constructs a
fresh validated manifest reader for every reference. Neither recreates an entire old release.
Counters describe logical Python I/O, not physical device reads. Timings are local, cache-warm
observations; parity/fixture checks are outside timings. Workspaces and locks stay in an owned
temporary directory. The optional JSON report must be new.
"""
import argparse
import copy
import hashlib
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
    padding = parser.add_mutually_exclusive_group()
    padding.add_argument("--padding-mib", type=int)
    padding.add_argument("--padding-kib", type=int)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--legacy-result-reader", action="store_true")
    modes.add_argument("--per-reference-manifest", action="store_true")
    args = parser.parse_args()
    for name, maximum in (("trials", 10), ("repeats", 20), ("bundles", 1000)):
        if not 1 <= getattr(args, name) <= maximum:
            parser.error(f"{name} must be between 1 and {maximum}")
    padding_bytes = (args.padding_kib * 1024 if args.padding_kib is not None else
                     (8 if args.padding_mib is None else args.padding_mib) * 1024 * 1024)
    if not 1024 <= padding_bytes <= 16 * 1024 * 1024:
        parser.error("padding must be between 1 KiB and 16 MiB per bundle")
    if padding_bytes * args.bundles > 256 * 1024 * 1024:
        parser.error("total fixture padding must not exceed 256 MiB")
    if args.output and args.output.exists():
        parser.error("output already exists; choose a new report filename")
    workspace = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(workspace / "python"))
    from fairbeam import documents as doc, modelfiles, _filelocks
    from fairbeam.design import blank_design
    from fairbeam.document_workspace import DocumentWorkspace

    original_reader = doc._ResultReader
    mode = ("isolated double result read/hash" if args.legacy_result_reader else
            "isolated per-reference manifest validation" if args.per_reference_manifest else
            "current batched reader")

    class BaselineReader(original_reader):
        def __init__(self, path, document):
            super().__init__(path, document)
            if args.per_reference_manifest:
                self.benchmark_path = path
                self.benchmark_document = copy.deepcopy(document)

        def read(self, reference):
            if args.per_reference_manifest:
                # Reuse production validation/read code, without copying the import loop.
                fresh = original_reader(self.benchmark_path, self.benchmark_document)
                return fresh.read(reference)
            raw, result = super().read(reference)
            # Isolate the old second I/O/hash pass; validation remains batched in this mode.
            second = doc._read_bytes(self._folder / reference["file"], doc.MAX_RESULT)
            if doc.digest(second) != reference["sha256"]:
                raise doc.DocumentError("Result changed while it was being opened")
            assert second == raw
            return second, result

    active_reader = original_reader if mode == "current batched reader" else BaselineReader

    def snapshot(folder):
        return {item.name: (item.stat().st_size, item.stat().st_mtime_ns,
                            hashlib.sha256(item.read_bytes()).hexdigest())
                for item in folder.iterdir()}

    measurements = []
    for trial in range(1, args.trials + 1):
        with tempfile.TemporaryDirectory(prefix="fairbeam-copy-benchmark-") as directory:
            root = Path(directory).resolve()
            with mock.patch.object(_filelocks, "_lock_directory", return_value=root / "locks"):
                models, projects, history = (root / name for name in ("models", "projects", "history"))
                models.mkdir()
                projects.mkdir()
                design = blank_design("copy-benchmark", "Copy benchmark")
                modelfiles.create_design(models, "copy_benchmark", design)
                file = modelfiles.read_design_file(models, "copy_benchmark")
                manager = SimpleNamespace(input_path=lambda job: job.snapshot_path)
                bridge = DocumentWorkspace(models, projects, history, manager)
                source = root / "source.design.json"
                source_bytes = json.dumps(design, ensure_ascii=False).encode("utf-8")
                source.write_bytes(source_bytes)
                sources = {}
                for index in range(args.bundles):
                    name = f"result-{index}.json"
                    result = {"schema": "fairbeam.project/1", "model": design["model"],
                              "results": {"bands": [], "benchmark_index": index,
                                          "padding": "x" * padding_bytes}}
                    raw = b"\xef\xbb\xbf" + json.dumps(result, separators=(",", ":")).encode("utf-8") + b"\r\n"
                    (projects / name).write_bytes(raw)
                    sources[doc.digest(raw)] = projects / name
                    job = SimpleNamespace(status="done", bundle=name, kind="run", model=file["id"],
                                          input_snapshot={"sha256": doc.digest(source_bytes)},
                                          id=f"job-{index}", snapshot_path=str(source))
                    assert bridge.record_completed(job)
                del result, raw
                source_snapshot = snapshot(projects)
                result_bytes = sum(item.stat().st_size for item in sources.values())
                expected_design_hash = doc.design_digest(design)

                def verify(response):
                    assert not response["warnings"], response["warnings"]
                    assert response["file"]["design"] == design
                    document, revision = doc.read(Path(response["document"]["path"]))
                    assert revision == response["document"]["revision"]
                    assert document["design"] == design
                    assert len(document["results"]) == len(response["results"]) == args.bundles
                    for reference, status in zip(document["results"], response["results"]):
                        assert reference["design_sha256"] == expected_design_hash
                        assert status == {**reference, "provenance": "source-design", "status": "current",
                                          "project_file": status["project_file"]}
                        expected = sources[reference["sha256"]].read_bytes()
                        assert (projects / status["project_file"]).read_bytes() == expected
                        folder = Path(response["document"]["path"]).parent / document["results_directory"]
                        assert (folder / reference["file"]).read_bytes() == expected
                    return document["results"]

                def measured(operation, action, expected=None):
                    counts = dict.fromkeys(("read_calls", "read_bytes", "hash_calls", "hash_bytes",
                                           "result_reader_calls", "result_reader_read_calls", "result_reader_read_bytes",
                                           "result_reader_hash_calls", "result_reader_hash_bytes",
                                           "design_hash_calls", "design_hash_bytes", "manifest_validations"), 0)
                    counts.update(result_reader_seconds=0.0, manifest_validation_seconds=0.0)
                    read, digest, design_digest, validate = doc._read_bytes, doc.digest, doc.design_digest, doc.validate
                    result_read = active_reader.read
                    in_reader = in_design = False

                    def counted_read(*values, **options):
                        raw = read(*values, **options)
                        counts["read_calls"] += 1
                        counts["read_bytes"] += len(raw)
                        if in_reader:
                            counts["result_reader_read_calls"] += 1
                            counts["result_reader_read_bytes"] += len(raw)
                        return raw

                    def counted_digest(raw):
                        counts["hash_calls"] += 1
                        counts["hash_bytes"] += len(raw)
                        prefix = "design" if in_design else "result_reader" if in_reader else None
                        if prefix:
                            counts[prefix + "_hash_calls"] += 1
                            counts[prefix + "_hash_bytes"] += len(raw)
                        return digest(raw)

                    def counted_design(value):
                        nonlocal in_design
                        previous, in_design = in_design, True
                        try:
                            return design_digest(value)
                        finally:
                            in_design = previous

                    def counted_validate(*values, **options):
                        counts["manifest_validations"] += 1
                        started = time.perf_counter()
                        try:
                            return validate(*values, **options)
                        finally:
                            counts["manifest_validation_seconds"] += time.perf_counter() - started

                    def counted_result_read(reader, reference):
                        nonlocal in_reader
                        previous, in_reader = in_reader, True
                        counts["result_reader_calls"] += 1
                        started = time.perf_counter()
                        try:
                            return result_read(reader, reference)
                        finally:
                            counts["result_reader_seconds"] += time.perf_counter() - started
                            in_reader = previous

                    with mock.patch.object(doc, "_ResultReader", active_reader), \
                            mock.patch.object(active_reader, "read", counted_result_read), \
                            mock.patch.object(doc, "_read_bytes", counted_read), \
                            mock.patch.object(doc, "digest", counted_digest), \
                            mock.patch.object(doc, "design_digest", counted_design), \
                            mock.patch.object(doc, "validate", counted_validate):
                        started = time.perf_counter()
                        response = action()
                        elapsed = time.perf_counter() - started
                    verify(response)
                    if expected is not None:
                        assert response == expected, "Open response differs from the unmodified reader control"
                        factor = 2 if args.legacy_result_reader else 1
                        assert counts["result_reader_calls"] == args.bundles
                        for kind in ("read", "hash"):
                            assert counts[f"result_reader_{kind}_calls"] == args.bundles * factor
                            assert counts[f"result_reader_{kind}_bytes"] == result_bytes * factor
                        assert counts["manifest_validations"] >= (args.bundles + 2 if args.per_reference_manifest else 2)
                        assert counts["design_hash_calls"] > 0
                    measurements.append({"trial": trial, "operation": operation, "seconds": elapsed,
                                         **counts, "references": len(response["results"])})
                    return response

                path = root / "benchmark.fb"
                saved = measured("first save", lambda: bridge.save(path, file["id"], file["hash"]))
                references = verify(saved)
                folder = root / saved["document"]["results_directory"]
                before, document_bytes = snapshot(folder), path.read_bytes()
                for _ in range(args.repeats):
                    saved = measured("unchanged Save", lambda: bridge.save(
                        path, saved["file"]["id"], saved["file"]["hash"],
                        base_hash=saved["document"]["revision"], document_id=saved["document"]["id"]))
                    assert verify(saved) == references
                    assert path.read_bytes() == document_bytes
                copied = measured("bound Save As", lambda: bridge.save(
                    root / "copy.fb", saved["file"]["id"], saved["file"]["hash"],
                    document_id=saved["document"]["id"], save_as=True))
                assert verify(copied) == references
                copy_folder = root / copied["document"]["results_directory"]
                copy_before = snapshot(copy_folder)
                # Prime caches with the unmodified reader and retain its exact reply.
                control = bridge.open(path)
                assert verify(control) == references
                for _ in range(args.repeats):
                    measured("explicit Open", lambda: bridge.open(path), control)
                assert path.read_bytes() == document_bytes
                assert snapshot(folder) == before
                assert snapshot(copy_folder) == copy_before
                assert {name: value for name, value in snapshot(projects).items() if name in source_snapshot} == source_snapshot

    summaries = {}
    for operation in dict.fromkeys(item["operation"] for item in measurements):
        seconds = [item["seconds"] for item in measurements if item["operation"] == operation]
        summaries[operation] = {"count": len(seconds), "median_seconds": statistics.median(seconds),
                                "min_seconds": min(seconds), "max_seconds": max(seconds)}
    evidence = {"workspace": str(workspace), "python": platform.python_version(), "platform": platform.platform(),
                "result_reader": mode, "fixture": {"bundles": args.bundles, "padding_bytes": padding_bytes,
                "total_padding_bytes": padding_bytes * args.bundles, "result_bytes": result_bytes},
                "open_response_matches_current_control": True, "exact_reopened_bytes_refs_statuses": True,
                "unchanged_sidecar_bytes_sizes_mtimes": True, "unchanged_source_results": True,
                "measurements": measurements, "summary": summaries,
                "notes": "Counters cover successful documents._read_bytes and documents.digest calls. Reader counters instrument _ResultReader.read; design hashes are counted separately from result hashes. Manifest counts cover documents.validate. Setup, cache priming and exact parity/unchanged-file assertions are excluded. Healthy cached Saves may have zero reader calls; every Open must have the exact nonzero result byte/hash totals for its mode. No solver/network."}
    rendered = json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8", newline="\n") as report:
            report.write(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
