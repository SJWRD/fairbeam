"""Optional, immutable human-browsable derivatives of a verified portable result.

The canonical checksummed bundle remains the source used to reopen a project.
No plot, CAD drawing or optimizer history is invented when the bundle lacks it.
"""
import json
from pathlib import Path

from . import documents as doc


def export_sections(root: Path, raw: bytes, reference: dict, design: dict, mkdir, write) -> None:
    bundle = json.loads(raw)
    results = bundle.get("results", {})
    # Create/check each level separately so an existing link cannot redirect a child write.
    mkdir(root)
    runs = root / "runs"
    mkdir(runs)
    folder = runs / ("run-" + reference["sha256"] + "-" + reference["design_sha256"][:16])
    mkdir(folder)

    def emit(section, name, value):
        target = folder / section
        mkdir(target)
        write(target / name, doc._json_bytes(value))

    emit("metadata", "run.json", {"schema": "fairbeam.result-export/1",
         "bundle_file": "../../" + reference["file"], "sha256": reference["sha256"],
         "design_sha256": reference["design_sha256"],
         **{k: bundle[k] for k in ("name", "created", "generator", "solver", "run", "units") if k in bundle}})
    emit("s-parameters", "data.json", {k: results[k] for k in
         ("frequency", "ports", "sparams", "bands", "s11_band_db") if k in results})
    if "farfield" in results:
        emit("far-field", "data.json", {"farfield": results["farfield"]})
    emit("geometry", "geometry.json", {k: bundle[k] for k in
         ("model", "units", "parts", "ports", "mesh", "domain") if k in bundle})
    if doc.design_digest(design) == reference["design_sha256"]:
        emit("geometry", "source.design.json", design)
    for section in ("drawings", "images", "optimization"):
        mkdir(folder / section)
    write(folder / "README.txt", (
        "This folder contains derivatives of the checksummed result bundle.\n"
        "S-parameter and far-field values are JSON data, not rendered plots.\n"
        "Source design is included only when its hash matches this run.\n"
        "Drawings, images and optimization are reserved for separately exported artifacts;\n"
        "empty folders do not mean those artifacts or an optimization were generated.\n"
        "Reopen the .fb project to continue. New result hashes get separate run folders.\n"
    ).encode("utf-8"))
