"""Experimental Design-to-Elmer closed PEC eigenmode workflow (no driven ports)."""
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

from .fem_geometry import mesh_design, write_elmer_mesh

SCHEMA = "fairbeam.elmer-design-result/1"


def prepare(design, settings):
    if not isinstance(design, dict):
        raise ValueError("Elmer Design run requires a design snapshot")
    if set(settings) - {"domain_mm", "max_cell_mm", "target_hz", "closed_pec"}:
        raise ValueError("Unsupported Elmer Design settings")
    if settings.get("closed_pec") is not True:
        raise ValueError("Explicit closed PEC enclosure selection is required")
    target = settings.get("target_hz")
    if isinstance(target, bool) or not isinstance(target, (float, int)) or not math.isfinite(target) or not 1e6 <= target <= 1e12:
        raise ValueError("Target frequency must be between 1 MHz and 1000 GHz")
    mesh = mesh_design(design, domain_mm=settings.get("domain_mm"), max_cell_mm=settings.get("max_cell_mm", 25))
    if len(mesh.elements) > 4000 or len(mesh.nodes) > 10000:
        raise ValueError("Experimental Elmer solve limited to 4000 elements / 10000 nodes; increase mesh cell size")
    if min(mesh.cell_counts) < 2:
        raise ValueError("Use at least two mesh cells along every axis")
    return mesh


def sif(mesh, target_hz):
    import fairbeam_elmer
    # Use the already exercised edge-element equation and physical unit convention.
    text = fairbeam_elmer.inputs(.025)[1]
    text = text.replace('Mesh DB "." "cavity"', 'Mesh DB "." "mesh"')
    bodies = []
    used = {body for body, _ in mesh.elements}
    for region in mesh.regions:
        rid = region["id"]
        if rid not in used:
            continue
        index = len(bodies) + 1
        bodies.append(f'''Body {index}
 Target Bodies(1) = {rid}
 Equation = 1
 Material = {index}
End
Material {index}
 Relative Permittivity = {region['eps_r']:.17g}
 Relative Permeability = {region['mu_r']:.17g}
End
''')
    start, end = text.index("Body 1\n"), text.index("Equation 1\n")
    text = text[:start] + "".join(bodies) + text[end:]
    shift = (2 * math.pi * target_hz / fairbeam_elmer.C0) ** 2
    text = text.replace("Eigen System Shift = Real 1.5e3", f"Eigen System Shift = Real {shift:.17g}")
    tags = sorted({tag for tag, _, _ in mesh.boundary_elements})
    text = text.replace("Target Boundaries(6) = 1 2 3 4 5 6", f"Target Boundaries({len(tags)}) = " + " ".join(map(str, tags)))
    return text


def run(design, settings, outdir, root):
    import fairbeam_elmer
    mesh = prepare(design, settings)
    home, solver, _ = fairbeam_elmer.discover(root)
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=False)
    metadata = write_elmer_mesh(mesh, out / "mesh")
    (out / "case.sif").write_text(sif(mesh, settings["target_hz"]), encoding="utf-8")
    manifest = {"schema": SCHEMA, "status": "model_built", "mesh": metadata,
                "settings": settings, "solver_sha256": fairbeam_elmer.digest(solver),
                "generator_sha256": fairbeam_elmer.digest(__file__),
                "sif_sha256": fairbeam_elmer.digest(out / "case.sif"), "threads": 1}
    def save():
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    save()
    try:
        env = os.environ.copy()
        env.update(ELMER_HOME=str(home), OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
                   PATH=str(solver.parent) + os.pathsep + env.get("PATH", ""))
        manifest["status"] = "solver_started"
        save()
        started = time.monotonic()
        process = subprocess.run([str(solver), "case.sif"], cwd=out, env=env,
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=120,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        log = process.stdout + process.stderr
        (out / "solver.log").write_text(log, encoding="utf-8")
        manifest.update(status="solver_returned", elapsed_seconds=time.monotonic() - started,
                        exit_code=process.returncode)
        save()
        if process.returncode or "ALL DONE" not in log or re.search(r"WARNING::.*FAILED|FATAL|ERROR::", log):
            raise ValueError("Elmer did not converge or failed; try a target nearer the expected mode and inspect solver.log")
        frequencies = sorted(float(v.replace("D", "E")) * fairbeam_elmer.C0
                             for v in (out / "eigen.dat").read_text().split())
        if len(frequencies) != 2 or any(not math.isfinite(f) or f <= 0 for f in frequencies):
            raise ValueError("Expected two finite positive eigenfrequencies near the target")
        fields = fairbeam_elmer.read_mode(out / "mesh/mode_t0001.vtu")
        if not any(v != 0 for key in ("elfield", "elfield Im") for v in fields[key]):
            raise ValueError("Elmer returned an empty electric field")
        result = {"schema": SCHEMA, "status": "results_exported", "frequency_hz": frequencies,
                  "s_parameters": None, "qualifies_driven_antenna": False, "precision_converged": False,
                  "checks": {"solver_completed": True, "finite_nonzero_field": True},
                  "mesh": metadata, "target_hz": settings["target_hz"],
                  "limitations": ["Closed PEC eigenmodes near the target frequency; not necessarily the lowest modes.",
                                  "No ports, S parameters, radiation pattern or mesh-convergence certification."],
                  "raw_sha256": {name: fairbeam_elmer.digest(out / name) for name in
                                 ("eigen.dat", "mesh/mode_t0001.vtu", "case.sif")}}
        manifest["status"] = "results_exported"
        save()
        return result
    except BaseException as exc:
        manifest.update(status="failed", error=str(exc))
        save()
        raise


def validate_result(value):
    if (value.get("schema") != SCHEMA or value.get("status") != "results_exported"
            or value.get("s_parameters") is not None or value.get("qualifies_driven_antenna") is not False
            or value.get("precision_converged") is not False
            or len(value.get("frequency_hz", [])) != 2
            or value.get("checks") != {"solver_completed": True, "finite_nonzero_field": True}):
        raise ValueError("Elmer Design result scope or completion checks invalid")
    return value
