"""Run the pinned upstream two-port control; this does not qualify Design antennas.

python scripts/check-elmer-scattering.py --elmer-root <installation> --out <new-folder>
Downloads two upstream inputs at an immutable revision and retains inputs, hashes and logs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

import numpy as np

REVISION = "5a8de867068be0568f09af40fb90fee300dfbede"
BASE = f"https://raw.githubusercontent.com/ElmerCSC/elmerfem/{REVISION}/fem/tests/VectorHelmholtzImpMatrix/"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elmer-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    suffix = ".exe" if os.name == "nt" else ""
    grid = args.elmer_root.resolve() / "bin" / f"ElmerGrid{suffix}"
    solver = grid.with_name(f"ElmerSolver{suffix}")
    env = {**os.environ, "ELMER_HOME": str(args.elmer_root.resolve()), "OMP_NUM_THREADS": "1",
           "OPENBLAS_NUM_THREADS": "1", "PATH": str(grid.parent) + os.pathsep + os.environ.get("PATH", "")}
    record = {"status": "preparing", "upstream_revision": REVISION, "frequency_hz": 2.5e9,
              "qualifies_design_antennas": False, "threads": 1,
              "scope": "Upstream rectangular two-port control, not a patch or open-boundary test."}
    started = time.monotonic()
    try:
        for name in ("case2.sif", "two_channels.grd"):
            with urllib.request.urlopen(BASE + name, timeout=30) as response:
                (out / name).write_bytes(response.read())
        record["sha256"] = {p.name: digest(p) for p in (grid, solver, out / "case2.sif", out / "two_channels.grd")}
        for executable, arguments, logname in ((grid, ["1", "2", "two_channels.grd"], "grid.log"),
                                                (solver, ["case2.sif"], "solver.log")):
            with (out / logname).open("w", encoding="utf-8") as log:
                process = subprocess.run([str(executable), *arguments], cwd=out, env=env,
                                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                         timeout=120, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if process.returncode:
                raise RuntimeError(f"{executable.name} failed: inspect {logname}")
        log = (out / "solver.log").read_text(encoding="utf-8", errors="replace")
        if "PASSED all 2 tests!" not in log or "ALL DONE" not in log or "FATAL" in log:
            raise RuntimeError("Upstream reference or completion checks failed")
        scattering = np.loadtxt(out / "SMatrix.dat") + 1j * np.loadtxt(out / "SMatrix.dat_im")
        if scattering.shape != (2, 2) or not np.isfinite(scattering).all():
            raise RuntimeError("Expected a finite complex 2x2 scattering matrix")
        sigma = float(np.linalg.svd(scattering, compute_uv=False).max())
        reciprocity = float(np.max(np.abs(scattering - scattering.T)))
        # Power-normalized waveguide control: do not clip excessive power or individual S entries.
        if sigma > 1.001 or reciprocity > 1e-6:
            raise RuntimeError(f"Power/reciprocity check failed: sigma={sigma}, asymmetry={reciprocity}")
        record.update(status="control_passed", s_real=scattering.real.tolist(), s_imag=scattering.imag.tolist(),
                      max_singular_value=sigma, reciprocity_error=reciprocity,
                      s11_db=float(20 * np.log10(abs(scattering[0, 0]))),
                      checks={"upstream_references": True, "finite_s_matrix": True,
                              "passivity_tolerance": .001, "reciprocity_tolerance": 1e-6})
    except BaseException as exc:
        record.update(status="failed", error=str(exc))
        raise
    finally:
        record["elapsed_seconds"] = time.monotonic() - started
        (out / "result.json").write_text(json.dumps(record, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
