#!/usr/bin/env python3
"""Opt-in native gallery-to-Design smoke check; no field propagation is run.

Run with a Python environment that has Fairbeam's dependencies, openEMS and CSXCAD:
    python scripts/check-gallery-designs.py
    python scripts/check-gallery-designs.py --out gallery-design-receipt.json

Uses the actual gallery selections and the same preview/copy/native validation
path as the app. All copies, jobs and caches live in a temporary workspace. This
checks that examples can open as Designs, not numerical result convergence.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time


def audit(repo: Path, receipt: dict) -> None:
    # The preview subprocess must use this checkout too, even when another
    # Fairbeam version is installed in the selected Python environment.
    python_source = str(repo / "python")
    sys.path.insert(0, python_source)
    os.environ["PYTHONPATH"] = os.pathsep.join(
        filter(None, (python_source, os.environ.get("PYTHONPATH")))
    )
    from fairbeam import modelfiles
    from fairbeam.jobs import JobManager
    from fairbeam.server import App

    gallery = repo / "public" / "projects"
    entries = json.loads((gallery / "index.json").read_text(encoding="utf-8"))["projects"]
    filenames = [entry["file"] for entry in entries]
    if len(filenames) != len(set(filenames)):
        raise ValueError("gallery index contains duplicate project files")
    if set(filenames) != modelfiles.BUNDLED_PROJECT_FILES:
        missing = sorted(modelfiles.BUNDLED_PROJECT_FILES - set(filenames))
        extra = sorted(set(filenames) - modelfiles.BUNDLED_PROJECT_FILES)
        raise ValueError(f"gallery/bundled project mismatch: missing={missing}, extra={extra}")
    receipt["expected"] = len(entries)
    print(f"Checking {len(entries)} gallery selections with native Design validation...", flush=True)

    with tempfile.TemporaryDirectory(prefix="fairbeam-gallery-designs-") as workspace:
        root = Path(workspace)
        models, projects, jobs = root / "models", root / "projects", root / "jobs"
        models.mkdir()
        projects.mkdir()
        for name in sorted(modelfiles.BUNDLED_MODELS):
            shutil.copyfile(repo / "python" / "models" / f"{name}.py", models / f"{name}.py")
        for name in sorted(modelfiles.BUNDLED_DESIGNS):
            filename = f"{name}.design.json"
            shutil.copyfile(repo / "examples" / "designs" / filename, models / filename)
        for filename in filenames:
            shutil.copyfile(gallery / filename, projects / filename)

        manager = JobManager(jobs, projects, python=sys.executable, autostart=False)
        app = None
        try:
            app = App(models_dir=models, projects_dir=projects, jobs_dir=jobs,
                      manager=manager, python=sys.executable)
            for index, entry in enumerate(entries):
                started = time.monotonic()
                row = {"file": entry["file"], "source": entry["model"].replace("-", "_")}
                try:
                    project = json.loads((projects / entry["file"]).read_text(encoding="utf-8"))
                    row["selected_params"] = {p["key"]: p["value"] for p in project["model"]["params"]}
                    request = {"from": row["source"], "project": entry["file"]}
                    row["preview"] = app.preview_example_conversion(request)
                    copied = app.copy_example({**request, "id": f"gallery_check_{index}",
                                               "name": f"Gallery check {index + 1}"})
                    validation = copied["validation"]
                    row["validation"] = {key: validation[key] for key in ("valid", "checks", "error", "build_s")
                                         if key in validation}
                    errors = [check for check in validation.get("checks", []) if check.get("severity") == "error"]
                    row["ok"] = (copied["kind"] == "design" and validation["valid"] is True
                                 and not errors and row["preview"].get("within_tolerance") is not False)
                    if not row["ok"]:
                        row["error"] = str(validation.get("error") or errors or "conversion mesh tolerance failed")
                except Exception as exc:
                    row.update(ok=False, error=f"{type(exc).__name__}: {exc}")
                row["seconds"] = round(time.monotonic() - started, 3)
                receipt["examples"].append(row)
                status = "PASS" if row["ok"] else "FAIL"
                print(f"{status} {row['file']} ({row['seconds']:.2f}s)", flush=True)
                if not row["ok"]:
                    print(f"  {row['error']}", file=sys.stderr, flush=True)
        finally:
            if app is not None:
                app.close()
            else:
                manager.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, help="write a JSON receipt (optional; otherwise only print a summary)")
    args = parser.parse_args()
    started = time.monotonic()
    receipt = {"schema": 1, "scope": "gallery Design conversion and native build admission; no field propagation",
               "python_version": sys.version.split()[0], "examples": []}
    try:
        audit(Path(__file__).resolve().parents[1], receipt)
    except Exception as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        print(f"FAIL: {receipt['error']}", file=sys.stderr)
    rows = receipt["examples"]
    passed = sum(row["ok"] for row in rows)
    expected = receipt.get("expected", 0)
    ok = "error" not in receipt and expected > 0 and len(rows) == expected and passed == expected
    receipt.update(ok=ok, passed=passed, seconds=round(time.monotonic() - started, 3))
    print(f"{'PASS' if ok else 'FAIL'}: {passed}/{expected} gallery selections; no simulations run.", flush=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Receipt: {args.out}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
